"""JWT authentication for the sync server."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional
from dataclasses import dataclass
import time

import httpx
import jwt
from jwt import PyJWKClient, InvalidTokenError

from .config import settings

logger = logging.getLogger(__name__)

# Google's public keys endpoint (JWKS)
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ["https://accounts.google.com", "accounts.google.com"]

# Bot JWT configuration
BOT_JWT_SECRET = os.environ.get("CC_BOT_JWT_SECRET", "")
BOT_JWT_ISSUER = "claudeconnect"
BOT_JWT_ALGORITHM = "HS256"
# Long-lived tokens: 1 year (bots need persistence)
BOT_TOKEN_EXPIRY_SECONDS = 365 * 24 * 60 * 60

# PyJWKClient with caching
_jwk_client: Optional[PyJWKClient] = None


def _get_jwk_client() -> PyJWKClient:
    """Get or create the JWK client (handles key caching internally)."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwk_client


@dataclass
class TokenPayload:
    """Validated token payload."""
    email: str
    subject: str
    issued_at: int
    expires_at: int
    provider: str = "google"  # "google" or "moltbook"


def validate_google_id_token(token: str) -> Optional[TokenPayload]:
    """
    Validate a Google OAuth id_token.

    Args:
        token: The JWT id_token from Google OAuth

    Returns:
        TokenPayload with user info if valid, None if invalid
    """
    try:
        # Get the signing key from Google's JWKS
        jwk_client = _get_jwk_client()
        signing_key = jwk_client.get_signing_key_from_jwt(token)

        # Decode and verify the token
        decode_options = {
            "verify_aud": bool(settings.google_client_id),
            "verify_iss": True,
            "verify_exp": True,
        }

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.google_client_id if settings.google_client_id else None,
            issuer=GOOGLE_ISSUERS,
            options=decode_options,
        )

        email = payload.get("email")
        if not email:
            logger.warning("Token missing email claim")
            return None

        # Check email is verified
        if not payload.get("email_verified", False):
            logger.warning(f"Email not verified: {email}")
            return None

        # Check allowed domains if configured
        if settings.allowed_domains:
            domain = email.split("@")[1] if "@" in email else ""
            if domain not in settings.allowed_domains:
                logger.warning(f"Email domain not allowed: {domain}")
                return None

        return TokenPayload(
            email=email,
            subject=payload.get("sub", ""),
            issued_at=payload.get("iat", 0),
            expires_at=payload.get("exp", 0),
        )

    except InvalidTokenError as e:
        logger.warning(f"JWT validation failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error validating token: {e}")
        return None


def extract_email_from_token_unsafe(token: str) -> Optional[str]:
    """
    Extract email from token WITHOUT validation.
    Only use for logging/debugging, never for authorization.
    """
    try:
        # Decode without verification
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("email")
    except Exception:
        return None


# --- Bot JWT Functions ---


def create_bot_token(handle: str) -> str:
    """
    Create a long-lived JWT for a verified Moltbook bot.

    Args:
        handle: The Moltbook handle (username)

    Returns:
        Signed JWT string

    Raises:
        ValueError: If BOT_JWT_SECRET is not configured
    """
    if not BOT_JWT_SECRET:
        raise ValueError("BOT_JWT_SECRET not configured")

    now = int(time.time())
    email = f"{handle}@moltbook.cc.bot"

    payload = {
        "iss": BOT_JWT_ISSUER,
        "sub": f"moltbook:{handle}",
        "email": email,
        "provider": "moltbook",
        "handle": handle,
        "iat": now,
        "exp": now + BOT_TOKEN_EXPIRY_SECONDS,
    }

    return jwt.encode(payload, BOT_JWT_SECRET, algorithm=BOT_JWT_ALGORITHM)


def validate_bot_token(token: str) -> Optional[TokenPayload]:
    """
    Validate a ClaudeConnect bot JWT.

    Args:
        token: The JWT token

    Returns:
        TokenPayload if valid, None if invalid
    """
    if not BOT_JWT_SECRET:
        logger.warning("BOT_JWT_SECRET not configured, cannot validate bot tokens")
        return None

    try:
        payload = jwt.decode(
            token,
            BOT_JWT_SECRET,
            algorithms=[BOT_JWT_ALGORITHM],
            issuer=BOT_JWT_ISSUER,
            options={
                "verify_exp": True,
                "verify_iss": True,
            },
        )

        email = payload.get("email")
        if not email:
            logger.warning("Bot token missing email claim")
            return None

        # Verify it's a moltbook bot email
        if not email.endswith("@moltbook.cc.bot"):
            logger.warning(f"Invalid bot email format: {email}")
            return None

        return TokenPayload(
            email=email,
            subject=payload.get("sub", ""),
            issued_at=payload.get("iat", 0),
            expires_at=payload.get("exp", 0),
            provider="moltbook",
        )

    except InvalidTokenError as e:
        logger.warning(f"Bot JWT validation failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error validating bot token: {e}")
        return None


def generate_claim_nonce() -> str:
    """Generate a secure random nonce for bot claims."""
    return secrets.token_urlsafe(32)
