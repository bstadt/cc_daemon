"""OAuth authentication flow for Claude Connect."""

from __future__ import annotations

import http.server
import json
import threading
import webbrowser
import urllib.parse
import base64
from typing import Optional
from dataclasses import dataclass

import httpx

from .config import Tokens, CONFIG_DIR, SERVER_URL


LOCAL_PORT = 3407


@dataclass
class AuthResult:
    """Result of authentication flow."""
    success: bool
    tokens: Optional[Tokens] = None
    error: Optional[str] = None


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (just to read email)."""
    try:
        # JWT is header.payload.signature
        payload_b64 = token.split(".")[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return {}


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    tokens: Optional[Tokens] = None
    error: Optional[str] = None

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass

    def do_GET(self):
        """Handle GET request from OAuth redirect."""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)

            id_token = params.get("id_token", [None])[0]
            refresh_token = params.get("refresh_token", [""])[0]

            if id_token:
                # Decode email from id_token
                payload = decode_jwt_payload(id_token)
                email = payload.get("email", "")

                CallbackHandler.tokens = Tokens(
                    id_token=id_token,
                    refresh_token=refresh_token,
                    email=email,
                )

                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html>
                    <body style="font-family: system-ui; text-align: center; padding: 50px;">
                        <h1>Logged in!</h1>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                """)
            else:
                error = params.get("error", ["Unknown error"])[0]
                CallbackHandler.error = error

                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(f"""
                    <html>
                    <body style="font-family: system-ui; text-align: center; padding: 50px;">
                        <h1>Login failed</h1>
                        <p>{error}</p>
                    </body>
                    </html>
                """.encode())
        else:
            self.send_response(404)
            self.end_headers()


def login(redirect_uri: Optional[str] = None) -> AuthResult:
    """
    Run the OAuth login flow.

    Opens browser to claudeconnect.io, waits for callback with tokens.

    Args:
        redirect_uri: Optional custom redirect URI (e.g., tunnel URL for headless login).
                      If provided, prints URL instead of opening browser.

    Returns:
        AuthResult with tokens on success, error on failure.
    """
    # Reset handler state
    CallbackHandler.tokens = None
    CallbackHandler.error = None

    # Start local server
    server = http.server.HTTPServer(("127.0.0.1", LOCAL_PORT), CallbackHandler)
    server.timeout = 120  # 2 minute timeout

    # Build login URL
    if redirect_uri is None:
        redirect_uri = f"http://localhost:{LOCAL_PORT}/callback"
    login_url = f"{SERVER_URL}/login?redirect_uri={urllib.parse.quote(redirect_uri)}"

    if "localhost" in redirect_uri:
        print(f"Opening browser for login...")
        webbrowser.open(login_url)
    else:
        # Remote/tunnel login - print URL for manual opening
        print(f"\nOpen this URL in any browser to login:\n")
        print(f"  {login_url}\n")

    print(f"Waiting for authentication (timeout: 2 minutes)...")

    # Wait for callback
    try:
        while CallbackHandler.tokens is None and CallbackHandler.error is None:
            server.handle_request()
    except Exception as e:
        return AuthResult(success=False, error=str(e))
    finally:
        server.server_close()

    if CallbackHandler.tokens:
        # Save tokens
        CallbackHandler.tokens.save()
        return AuthResult(success=True, tokens=CallbackHandler.tokens)
    else:
        return AuthResult(success=False, error=CallbackHandler.error)


def refresh_token(current_refresh_token: str) -> Optional[Tokens]:
    """
    Refresh the id_token using the refresh token.

    Args:
        current_refresh_token: The stored refresh token.

    Returns:
        New Tokens object, or None on failure.
    """
    try:
        response = httpx.get(
            f"{SERVER_URL}/refresh",
            params={"refresh_token": current_refresh_token},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        if "id_token" in data:
            new_id_token = data["id_token"]
            payload = decode_jwt_payload(new_id_token)
            email = payload.get("email", "")

            tokens = Tokens(
                id_token=new_id_token,
                refresh_token=current_refresh_token,
                email=email,
            )
            tokens.save()
            return tokens
    except Exception:
        pass

    return None


def ensure_valid_token() -> Optional[Tokens]:
    """
    Ensure we have a valid token, refreshing if needed.

    Returns:
        Valid Tokens, or None if not logged in / refresh failed.
    """
    tokens = Tokens.load()
    if not tokens:
        return None

    # TODO: Check if id_token is expired and refresh if needed
    # For now, just return the stored tokens
    # The server will reject if expired, and we can handle that

    return tokens
