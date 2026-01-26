"""File endpoints - download, upload, delete files."""

from fastapi import APIRouter, HTTPException, Depends, Response, Request
from fastapi.responses import StreamingResponse
import logging
import shutil

from ..dependencies import get_current_user
from ..auth import TokenPayload
from ..authz import get_user_dir, check_read_permission, check_write_permission, validate_authz

logger = logging.getLogger(__name__)

router = APIRouter(tags=["files"])


@router.get("/files/{user_email}/{path:path}")
async def download_file(
    user_email: str,
    path: str,
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Download a file from user_email's storage.

    Returns raw file bytes. Returns 403 if not authorized, 404 if not found.
    """
    # Sanitize path - no directory traversal or empty path
    if not path or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Check read permission
    if not check_read_permission(user_email, current_user.email, "/" + path):
        raise HTTPException(status_code=403, detail="Not authorized to read this file")

    # Get file path
    user_dir = get_user_dir(user_email)
    file_path = user_dir / "files" / path

    # Security check - ensure path is within user's directory
    try:
        file_path.resolve().relative_to((user_dir / "files").resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Check if file exists
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")

    # Read and return file
    content = file_path.read_bytes()

    # Guess content type
    content_type = "application/octet-stream"
    if path.endswith(".md"):
        content_type = "text/markdown"
    elif path.endswith(".txt"):
        content_type = "text/plain"
    elif path.endswith(".json"):
        content_type = "application/json"

    logger.info(f"Downloaded {path} from {user_email} by {current_user.email} ({len(content)} bytes)")

    return Response(content=content, media_type=content_type)


@router.put("/files/{user_email}/{path:path}")
async def upload_file(
    user_email: str,
    path: str,
    request: Request,
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Upload a file to user_email's storage.

    Requires write permission via authz. Owner can always write.
    If uploading 'authz', validates the syntax before saving (owner only).
    """
    # Sanitize path - no directory traversal or empty path
    if not path or ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Special handling for authz file - only owner can modify their own authz
    if path == "authz" and user_email != current_user.email:
        raise HTTPException(status_code=403, detail="Cannot modify another user's authz")

    # Check write permission
    if not check_write_permission(user_email, current_user.email, "/" + path):
        raise HTTPException(status_code=403, detail="Not authorized to write this file")

    # Get request body
    body = await request.body()

    # Special handling for authz file - validate before saving
    if path == "authz":
        try:
            content = body.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="authz must be valid UTF-8")

        is_valid, error_msg = validate_authz(content)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid authz file: {error_msg}"
            )

    # Get target user's data directory
    user_dir = get_user_dir(user_email)
    user_files_dir = user_dir / "files"

    # Build full path
    file_path = user_files_dir / path

    # Security check - ensure path is within user's directory
    try:
        file_path.resolve().relative_to(user_files_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Create parent directories
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Write file
    file_path.write_bytes(body)

    # For authz, also copy to user root (not in files/)
    if path == "authz":
        authz_root = user_dir / "authz"
        authz_root.write_bytes(body)

    logger.info(f"Uploaded {path} to {user_email} by {current_user.email} ({len(body)} bytes)")

    return {"status": "ok", "path": path, "size": len(body)}


@router.delete("/files/{user_email}/{path:path}")
async def delete_file(
    user_email: str,
    path: str,
    current_user: TokenPayload = Depends(get_current_user),
    recursive: bool = False,
):
    """
    Delete a file or directory from user_email's storage.

    Owner-only operation. Idempotent - returns success even if already gone.
    Idempotent - returns success even if already gone.
    """
    # Sanitize path - no directory traversal
    if ".." in path:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Prevent deleting authz file
    if path == "authz":
        raise HTTPException(status_code=400, detail="Cannot delete authz file")

    # Only owners can delete files/directories
    if user_email != current_user.email:
        raise HTTPException(status_code=403, detail="Only owner can delete files")

    # Check write permission (owner always has write)
    if not check_write_permission(user_email, current_user.email, "/" + path):
        raise HTTPException(status_code=403, detail="Not authorized to delete this file")

    # Get target user's data directory
    user_dir = get_user_dir(user_email)
    file_path = user_dir / "files" / path

    # Security check - ensure path is within user's directory
    try:
        file_path.resolve().relative_to((user_dir / "files").resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Delete file or directory if exists (idempotent)
    existed = False
    deleted_count = 0
    if file_path.exists():
        existed = True
        if file_path.is_file():
            file_path.unlink()
            deleted_count = 1
            logger.info(f"Deleted {path} from {user_email} by {current_user.email}")
        elif file_path.is_dir():
            if not recursive:
                raise HTTPException(status_code=400, detail="Path is a directory; use recursive delete")
            # Count files for reporting before delete
            deleted_count = sum(1 for _ in file_path.rglob("*") if _.is_file())
            shutil.rmtree(file_path)
            logger.info(
                f"Deleted directory {path} ({deleted_count} files) from {user_email} by {current_user.email}"
            )
        else:
            raise HTTPException(status_code=400, detail="Path is not a file or directory")

    return {"status": "ok", "path": path, "existed": existed, "deleted": deleted_count}
