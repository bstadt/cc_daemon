"""Public key endpoint - allows anyone to fetch a user's public key."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..authz import get_public_key, get_user_dir

router = APIRouter(tags=["keys"])


class PublicKeyResponse(BaseModel):
    """Response from public key endpoint."""
    user: str
    public_key: str


@router.get("/keys/{user_email}", response_model=PublicKeyResponse)
async def get_user_public_key(user_email: str):
    """
    Get a user's X25519 public key.

    This is a PUBLIC endpoint - no authentication required.
    Used for encrypting friend requests and establishing shared secrets.
    """
    # Check if user exists
    user_dir = get_user_dir(user_email)
    if not user_dir.exists():
        raise HTTPException(status_code=404, detail="User not found")

    # Get public key from authz
    public_key = get_public_key(user_email)
    if not public_key:
        raise HTTPException(
            status_code=404,
            detail="User has no public key (authz file missing or invalid)"
        )

    return PublicKeyResponse(user=user_email, public_key=public_key)
