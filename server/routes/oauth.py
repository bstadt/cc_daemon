"""OAuth endpoints for login and token refresh."""

import logging
from urllib.parse import urlencode, quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
import httpx

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

# Google OAuth configuration. The client id/secret and base URL come from the
# shared Settings object (server/config.py) so this login flow and the ID-token
# audience check in auth.py can never read different values.
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@router.get("/login")
async def login(redirect_uri: str = Query(..., description="Client callback URL")):
    """
    Start OAuth login flow.

    Redirects to Google OAuth, which will redirect back to /oauth/callback,
    which will then redirect to the client's redirect_uri with tokens.
    """
    if not settings.google_client_id:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # Store the client's redirect_uri in state (we'll pass it through OAuth)
    # In production, you'd want to sign/encrypt this
    state = quote(redirect_uri)

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.server_base_url}/callback",
        "response_type": "code",
        "scope": "openid email",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }

    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/callback")
async def oauth_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """
    Handle OAuth callback from Google.

    Exchanges auth code for tokens, then redirects to client with tokens.
    """
    if error:
        # Redirect to client with error
        if state:
            from urllib.parse import unquote
            client_redirect = unquote(state)
            return RedirectResponse(url=f"{client_redirect}?error={error}")
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    # Exchange code for tokens
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": f"{settings.server_base_url}/callback",
                },
            )
            response.raise_for_status()
            tokens = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Token exchange failed: {e}")
        raise HTTPException(status_code=500, detail="Token exchange failed")

    id_token = tokens.get("id_token", "")
    refresh_token = tokens.get("refresh_token", "")

    if not id_token:
        raise HTTPException(status_code=500, detail="No id_token in response")

    # Redirect to client with tokens
    from urllib.parse import unquote
    client_redirect = unquote(state)

    params = urlencode({
        "id_token": id_token,
        "refresh_token": refresh_token,
    })

    return RedirectResponse(url=f"{client_redirect}?{params}")


@router.get("/refresh")
async def refresh_token(refresh_token: str = Query(..., description="Refresh token")):
    """
    Refresh an expired id_token using the refresh token.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            response.raise_for_status()
            tokens = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Token refresh failed: {e}")
        raise HTTPException(status_code=401, detail="Token refresh failed")

    id_token = tokens.get("id_token", "")
    if not id_token:
        raise HTTPException(status_code=500, detail="No id_token in response")

    return {"id_token": id_token}
