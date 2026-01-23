"""Friend request endpoints."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from ..dependencies import get_current_user
from ..auth import TokenPayload
from ..authz import get_user_dir

logger = logging.getLogger(__name__)

router = APIRouter(tags=["friends"])


class FriendRequestBody(BaseModel):
    """Friend request payload."""
    to: str
    public_key: Optional[str] = None
    encrypted_master_key: Optional[str] = None


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name format (matches client)."""
    return email.replace("@", "-").replace(".", "-").lower()


@router.post("/friend-request")
async def send_friend_request(
    body: FriendRequestBody,
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Send a friend request to another user.

    This writes a friend request file to the recipient's
    claudeconnect/with-claudeconnect-io/ folder.

    The server has admin access to write to this folder since
    senders don't have permission to write there directly.
    """
    from_email = current_user.email
    to_email = body.to.strip().lower()

    if from_email == to_email:
        raise HTTPException(status_code=400, detail="Cannot send friend request to yourself")

    # Check if recipient exists
    recipient_dir = get_user_dir(to_email)
    if not recipient_dir.exists():
        raise HTTPException(status_code=404, detail=f"User {to_email} not found")

    # Build friend request file path
    from_email_sanitized = email_to_repo_name(from_email)
    request_filename = f"friend-request-{from_email_sanitized}.md"

    # The file goes in recipient's files/claudeconnect/with-claudeconnect-io/
    request_dir = recipient_dir / "files" / "claudeconnect" / "with-claudeconnect-io"
    request_dir.mkdir(parents=True, exist_ok=True)
    request_file = request_dir / request_filename

    # Check if request already exists
    if request_file.exists():
        raise HTTPException(status_code=409, detail="Friend request already pending")

    # Build request content (markdown format matching client expectations)
    timestamp = datetime.utcnow().isoformat() + "Z"
    content_lines = [
        f"# Friend Request from {from_email}",
        "",
        f"**From**: {from_email}",
        f"**Date**: {timestamp}",
        "",
    ]

    if body.public_key:
        content_lines.append(f"**Public-Key**: {body.public_key}")
        content_lines.append("")

    if body.encrypted_master_key:
        content_lines.append(f"**Encrypted-Master-Key**: {body.encrypted_master_key}")
        content_lines.append("")

    content_lines.extend([
        "---",
        "",
        "To accept this friend request, run:",
        f"```",
        f"claudeconnect accept-friend {from_email}",
        f"```",
        "",
        "To reject, run:",
        f"```",
        f"claudeconnect reject-friend {from_email}",
        f"```",
    ])

    content = "\n".join(content_lines) + "\n"

    # Write the friend request file
    request_file.write_text(content)

    logger.info(f"Friend request from {from_email} to {to_email}")

    return {
        "status": "ok",
        "from": from_email,
        "to": to_email,
        "file": str(request_file.relative_to(recipient_dir / "files")),
    }


class AcceptRejectBody(BaseModel):
    """Accept/reject friend request payload."""
    from_email: str


@router.post("/accept-friend")
async def accept_friend_request(
    body: AcceptRejectBody,
    current_user: TokenPayload = Depends(get_current_user),
):
    """
    Accept a friend request.

    Returns the friend request content so the client can extract keys,
    then deletes the friend request file.
    """
    my_email = current_user.email
    from_email = body.from_email.strip().lower()

    if from_email == my_email:
        raise HTTPException(status_code=400, detail="Invalid friend request")

    # Find the friend request file
    my_dir = get_user_dir(my_email)
    from_email_sanitized = email_to_repo_name(from_email)
    request_filename = f"friend-request-{from_email_sanitized}.md"
    request_file = my_dir / "files" / "claudeconnect" / "with-claudeconnect-io" / request_filename

    if not request_file.exists():
        raise HTTPException(status_code=404, detail=f"No friend request from {from_email}")

    # Read the content before deleting (client needs keys from it)
    content = request_file.read_text()

    # Delete the friend request file
    request_file.unlink()

    logger.info(f"Friend request from {from_email} accepted by {my_email}")

    return {
        "status": "ok",
        "from": from_email,
        "content": content,
    }
