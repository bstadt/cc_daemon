"""Manifest endpoint - returns list of files user can access."""

import hashlib
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from ..dependencies import get_current_user
from ..auth import TokenPayload
from ..authz import get_user_dir, check_read_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["manifest"])


class FileInfo(BaseModel):
    """Information about a single file."""
    path: str
    sha256: str
    size: int
    mtime: float


class ManifestResponse(BaseModel):
    """Response from manifest endpoint."""
    user: str
    files: List[FileInfo]


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


@router.get("/manifest/{user_email}", response_model=ManifestResponse)
async def get_manifest(
    user_email: str,
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Get manifest of files the requester can access from user_email.

    Returns list of files with their hashes, sizes, and modification times.
    Only includes files the requester is authorized to see based on authz.
    """
    # Get user's data directory
    user_dir = get_user_dir(user_email)
    files_dir = user_dir / "files"

    # Check if user exists
    if not user_dir.exists():
        raise HTTPException(status_code=404, detail=f"User {user_email} not found")

    # If files directory doesn't exist, return empty manifest
    if not files_dir.exists():
        return ManifestResponse(user=user_email, files=[])

    # Walk files directory and build manifest
    file_list: List[FileInfo] = []

    for file_path in files_dir.rglob("*"):
        # Skip directories
        if not file_path.is_file():
            continue

        # Get relative path from files directory
        rel_path = file_path.relative_to(files_dir)
        path_str = str(rel_path)

        # Check read permission
        if not check_read_permission(user_email, current_user.email, "/" + path_str):
            continue

        # Get file info
        try:
            stat = file_path.stat()
            file_info = FileInfo(
                path=path_str,
                sha256=compute_sha256(file_path),
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
            file_list.append(file_info)
        except (OSError, IOError) as e:
            logger.warning(f"Error reading file {file_path}: {e}")
            continue

    logger.info(f"Manifest for {user_email} requested by {current_user.email}: {len(file_list)} files")

    return ManifestResponse(user=user_email, files=file_list)
