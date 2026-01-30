"""FastAPI dependencies for authentication and authorization."""

from fastapi import Header, HTTPException, status
from typing import Annotated

from .auth import validate_google_id_token, validate_bot_token, TokenPayload


async def get_current_user(
    authorization: Annotated[str, Header(description="Bearer token")]
) -> TokenPayload:
    """
    Dependency to extract and validate the current user from JWT.

    Supports both Google OAuth id_tokens and ClaudeConnect bot tokens.

    Usage:
        @app.get("/protected")
        async def protected(user: TokenPayload = Depends(get_current_user)):
            return {"email": user.email}
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token from "Bearer <token>" format
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    # Try Google token first
    payload = validate_google_id_token(token)
    if payload:
        return payload

    # Fall back to bot token
    payload = validate_bot_token(token)
    if payload:
        return payload

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


# Type alias for cleaner route signatures
CurrentUser = Annotated[TokenPayload, get_current_user]
