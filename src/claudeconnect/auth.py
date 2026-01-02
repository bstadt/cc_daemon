"""Authentication - server handles OAuth, we store and use id_token."""

import json
import base64
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
import httpx

from .config import (
    LOGIN_URL,
    REFRESH_URL,
    CALLBACK_PORT,
    CALLBACK_URL,
    TOKENS_FILE,
    ensure_config_dir,
)


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle callback from server with tokens."""

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "id_token" in params:
            self.server.id_token = params["id_token"][0]
            self.server.refresh_token = params.get("refresh_token", [""])[0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family: system-ui; text-align: center; padding-top: 100px;">
                <h1>Logged in!</h1>
                <p>You can close this window.</p>
                </body></html>
            """)
        else:
            error = params.get("error", ["unknown"])[0]
            self.server.id_token = None
            self.server.refresh_token = None
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(f"<html><body>Error: {error}</body></html>".encode())

    def log_message(self, format, *args):
        pass


def decode_id_token(id_token: str) -> dict:
    """Decode JWT payload (no verification, just reading claims)."""
    payload = id_token.split(".")[1]
    # Add padding if needed
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def login() -> dict:
    """Run login flow - opens browser, waits for tokens."""
    login_url = f"{LOGIN_URL}?{urlencode({'redirect_uri': CALLBACK_URL})}"

    print("Opening browser to authenticate...")
    webbrowser.open(login_url)

    print(f"Waiting for callback on localhost:{CALLBACK_PORT}...")
    server = HTTPServer(("localhost", CALLBACK_PORT), CallbackHandler)
    server.id_token = None
    server.refresh_token = None
    server.handle_request()

    if not server.id_token:
        raise Exception("Login failed - no id_token received")

    # Decode email from id_token
    claims = decode_id_token(server.id_token)
    email = claims.get("email", "unknown")

    # Save tokens
    ensure_config_dir()
    data = {
        "id_token": server.id_token,
        "refresh_token": server.refresh_token,
        "email": email,
    }
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    TOKENS_FILE.chmod(0o600)

    return data


def load_tokens():
    """Load saved tokens."""
    if not TOKENS_FILE.exists():
        return None
    with open(TOKENS_FILE) as f:
        return json.load(f)


def save_tokens(data: dict):
    """Save tokens to disk."""
    ensure_config_dir()
    with open(TOKENS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    TOKENS_FILE.chmod(0o600)


def refresh_id_token() -> str:
    """Use refresh_token to get a new id_token from server."""
    data = load_tokens()
    if not data or "refresh_token" not in data:
        raise Exception("No refresh token. Run 'claudeconnect login' again.")

    response = httpx.get(
        REFRESH_URL,
        params={"refresh_token": data["refresh_token"]},
    )
    response.raise_for_status()
    result = response.json()

    if "error" in result:
        raise Exception(f"Refresh failed: {result['error']}")

    # Update stored id_token
    data["id_token"] = result["id_token"]
    claims = decode_id_token(result["id_token"])
    data["email"] = claims.get("email", data.get("email", "unknown"))
    save_tokens(data)

    return result["id_token"]


def get_id_token() -> str:
    """Get valid id_token, refreshing if needed."""
    data = load_tokens()
    if not data or "id_token" not in data:
        raise Exception("Not logged in. Run 'claudeconnect login' first.")

    # Check if token is expired
    try:
        claims = decode_id_token(data["id_token"])
        import time
        if claims.get("exp", 0) < time.time():
            print("Token expired, refreshing...")
            return refresh_id_token()
    except Exception:
        pass  # If we can't check, just use the token

    return data["id_token"]


def get_email():
    """Get saved email."""
    data = load_tokens()
    return data.get("email") if data else None
