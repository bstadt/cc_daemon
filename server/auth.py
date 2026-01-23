"""JWT authentication for the sync server."""

from __future__ import annotations

import logging
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
