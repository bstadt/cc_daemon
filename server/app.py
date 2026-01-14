"""Claude Connect Flask application.

Handles OAuth flow and repo management.
Friending is handled entirely via SVN (friend_requests folder) and authz files.
"""

import os
import secrets
import subprocess
import base64
import json
import time
from functools import wraps
from pathlib import Path
from flask import Flask, redirect, request, session, url_for, jsonify
from authlib.integrations.flask_client import OAuth
from urllib.parse import urlencode
from werkzeug.middleware.proxy_fix import ProxyFix
from cryptography.fernet import Fernet, InvalidToken
import httpx

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))

# Admin emails for test user management
ADMIN_EMAILS = {"brandon@calcifercomputing.com"}

# Fernet key for SVN tokens - must be 32 url-safe base64-encoded bytes
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FERNET_KEY = os.environ.get("FERNET_KEY")
if FERNET_KEY:
    fernet = Fernet(FERNET_KEY.encode())
else:
    fernet = None

# Handle reverse proxy (Apache) - trust X-Forwarded headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Paths
SVN_REPOS_DIR = Path("/var/svn/repos")
EPHEMERAL_USERS_FILE = Path("/var/svn/ephemeral-users.json")


def load_ephemeral_users() -> dict:
    """Load ephemeral users tracking file."""
    if not EPHEMERAL_USERS_FILE.exists():
        return {}
    try:
        return json.loads(EPHEMERAL_USERS_FILE.read_text())
    except (json.JSONDecodeError, IOError):
        return {}


def save_ephemeral_users(users: dict) -> None:
    """Save ephemeral users tracking file."""
    EPHEMERAL_USERS_FILE.write_text(json.dumps(users, indent=2))


def require_admin(f):
    """Decorator for admin-only endpoints."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing Authorization header"}), 401

        id_token = auth_header[7:]
        valid, email_or_error = verify_id_token(id_token)
        if not valid:
            return jsonify({"error": email_or_error}), 401

        if email_or_error not in ADMIN_EMAILS:
            return jsonify({"error": "Admin access required"}), 403

        # Store admin email for use in endpoint
        request.admin_email = email_or_error
        return f(*args, **kwargs)
    return decorated


# OAuth setup
oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email",
        "access_type": "offline",  # Request refresh token
        "prompt": "consent",  # Force consent to always get refresh token
    },
)


def email_to_repo_name(email: str) -> str:
    """Convert email to SVN repo name."""
    return email.replace("@", "-").replace(".", "-").lower()


def decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without verification (server already validated)."""
    try:
        payload_b64 = token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return {}


def verify_id_token(id_token: str) -> tuple[bool, str]:
    """
    Verify Google id_token and extract email.

    Returns:
        Tuple of (valid, email_or_error)
    """
    try:
        # Use Google's tokeninfo endpoint for verification
        response = httpx.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={id_token}",
            timeout=10,
        )

        if response.status_code != 200:
            return False, "Invalid token"

        data = response.json()
        email = data.get("email")

        if not email:
            return False, "No email in token"

        # Verify it's for our app
        expected_client_id = os.environ.get("GOOGLE_CLIENT_ID")
        if data.get("aud") != expected_client_id:
            return False, "Token not for this application"

        return True, email

    except Exception as e:
        return False, str(e)


def generate_authz_content(email: str) -> str:
    """Generate initial authz file content for a new user."""
    return f"""[/]
{email} = rw

[/claudeconnect/friend_requests]
* = rw
{email} = rw
"""


def ensure_authz(repo_path: Path, email: str, use_sudo: bool = False, force: bool = False) -> None:
    """
    Ensure authz file exists and has correct base permissions.
    Does NOT modify friend permissions - those are managed by Claude via the protocol.

    Args:
        repo_path: Path to the SVN repository
        email: User's email for authz
        use_sudo: If True, use sudo tee to write (for existing repos owned by www-data)
        force: If True, overwrite existing authz file (use for new repos to replace svnadmin default)
    """
    authz_path = repo_path / "conf" / "authz"

    # Only write if authz doesn't exist (don't overwrite friend permissions)
    # Unless force=True (for newly created repos where we need to replace the default template)
    if authz_path.exists() and not force:
        return

    authz_content = generate_authz_content(email)

    if use_sudo:
        subprocess.run(
            ["sudo", "tee", str(authz_path)],
            input=authz_content.encode(),
            stdout=subprocess.DEVNULL,
            check=True,
        )
    else:
        authz_path.write_text(authz_content)


def create_svn_repo(repo_name: str, email: str) -> tuple[bool, str]:
    """
    Create an SVN repository, or confirm existing repo.

    Args:
        repo_name: Name for the repo (sanitized email)
        email: User's email for authz

    Returns:
        Tuple of (success, message_or_error)
    """
    repo_path = SVN_REPOS_DIR / repo_name

    if repo_path.exists():
        return True, "Repo already exists"

    try:
        # Create the repository
        result = subprocess.run(
            ["svnadmin", "create", str(repo_path)],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return False, f"svnadmin create failed: {result.stderr}"

        # Create default authz file BEFORE chown
        # Use force=True to replace svnadmin's default template
        ensure_authz(repo_path, email, force=True)

        # Set ownership to www-data (Apache user) AFTER writing authz
        subprocess.run(
            ["sudo", "chown", "-R", "www-data:www-data", str(repo_path)],
            check=True,
        )

        return True, "Repo created successfully"

    except Exception as e:
        return False, str(e)


@app.route("/")
def index():
    return "<h1>Claude Connect</h1><p><a href='/login'>Login with Google</a></p>"


@app.route("/login")
def login():
    # Store where to redirect after auth (localhost for daemon)
    redirect_uri = request.args.get("redirect_uri", "")
    session["daemon_redirect"] = redirect_uri

    callback_url = url_for("callback", _external=True)
    # Request offline access to get refresh token
    return google.authorize_redirect(
        callback_url,
        access_type="offline",
        prompt="consent",
    )


@app.route("/callback")
def callback():
    token = google.authorize_access_token()

    # Get the tokens we need
    id_token = token.get("id_token")
    refresh_token = token.get("refresh_token", "")

    if not id_token:
        return "Failed to get id_token", 400

    # Redirect to daemon with Google's tokens
    daemon_redirect = session.pop("daemon_redirect", "")
    if daemon_redirect:
        params = urlencode({
            "id_token": id_token,
            "refresh_token": refresh_token,
        })
        return redirect(f"{daemon_redirect}?{params}")

    # No daemon redirect, just show success
    return f"<h1>Logged in!</h1><p>id_token: {id_token[:50]}...</p>"


@app.route("/refresh")
def refresh():
    """Exchange refresh token for new id_token."""
    refresh_token = request.args.get("refresh_token")
    if not refresh_token:
        return {"error": "Missing refresh_token"}, 400

    try:
        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": os.environ.get("GOOGLE_CLIENT_ID"),
                "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET"),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        response.raise_for_status()
        data = response.json()
        return {
            "id_token": data.get("id_token"),
            "expires_in": data.get("expires_in"),
        }
    except Exception as e:
        return {"error": str(e)}, 400


@app.route("/api/ensure-repo", methods=["POST"])
def ensure_repo():
    """
    Ensure user's repo exists, creating if needed.

    Expects Authorization header with Bearer id_token.

    Returns:
        JSON with repo name and URL.
    """
    # Get token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing Authorization header"}), 401

    id_token = auth_header[7:]  # Strip "Bearer "

    # Verify token and get email
    valid, email_or_error = verify_id_token(id_token)
    if not valid:
        return jsonify({"error": email_or_error}), 401

    email = email_or_error
    repo_name = email_to_repo_name(email)

    # Create repo if needed
    success, message = create_svn_repo(repo_name, email)
    if not success:
        return jsonify({"error": message}), 500

    return jsonify({
        "repo": repo_name,
        "url": f"https://claudeconnect.io/svn/{repo_name}",
        "email": email,
        "created": message == "Repo created successfully",
    })


@app.route("/api/svn-token", methods=["POST"])
def get_svn_token():
    """
    Exchange Google JWT for a short Fernet token for SVN auth.

    Expects Authorization header with Bearer id_token.
    Returns a Fernet-encrypted token containing email and expiry.
    """
    if not fernet:
        return jsonify({"error": "SVN tokens not configured"}), 500

    # Get token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing Authorization header"}), 401

    id_token = auth_header[7:]  # Strip "Bearer "

    # Verify token and get email
    valid, email_or_error = verify_id_token(id_token)
    if not valid:
        return jsonify({"error": email_or_error}), 401

    email = email_or_error

    # Create Fernet token with email and expiry (24 hours)
    expiry = int(time.time()) + (24 * 3600)
    payload = f"{email}|{expiry}"
    svn_token = fernet.encrypt(payload.encode()).decode()

    return jsonify({
        "svn_token": svn_token,
        "email": email,
        "expires_in": 24 * 3600,
    })


@app.route("/api/verify-svn-token", methods=["POST"])
def verify_svn_token():
    """
    Verify a Fernet SVN token and return the email.

    Used by Apache mod_authnz_external for auth.
    Expects JSON body with "token" field.
    """
    if not fernet:
        return jsonify({"error": "SVN tokens not configured"}), 500

    data = request.get_json() or {}
    token = data.get("token", "")

    if not token:
        return jsonify({"valid": False, "error": "Missing token"}), 400

    try:
        # Decrypt and parse
        payload = fernet.decrypt(token.encode()).decode()
        email, expiry_str = payload.rsplit("|", 1)
        expiry = int(expiry_str)

        # Check expiry
        if expiry < time.time():
            return jsonify({"valid": False, "error": "Token expired"}), 401

        return jsonify({"valid": True, "email": email})

    except InvalidToken:
        return jsonify({"valid": False, "error": "Invalid token"}), 401
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 400


@app.route("/api/lookup-repo", methods=["GET"])
def lookup_repo():
    """
    Look up a user's repo URL by email.

    Used by clients to find where to send friend requests.
    Query param: email
    """
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Missing email parameter"}), 400

    repo_name = email_to_repo_name(email)
    repo_path = SVN_REPOS_DIR / repo_name

    if not repo_path.exists():
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "email": email,
        "repo": repo_name,
        "url": f"https://claudeconnect.io/svn/{repo_name}",
    })


@app.route("/api/friend-request", methods=["POST"])
def send_friend_request():
    """
    Send a friend request to another user.

    Expects:
        - Authorization header with Bearer id_token
        - JSON body with "to" (target email) and optional "message"

    This endpoint commits a friend request file to the target's repo.
    """
    import tempfile
    from datetime import datetime

    # Authenticate sender
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing Authorization header"}), 401

    id_token = auth_header[7:]
    valid, email_or_error = verify_id_token(id_token)
    if not valid:
        return jsonify({"error": email_or_error}), 401

    sender_email = email_or_error

    # Get target email from body
    data = request.get_json() or {}
    target_email = data.get("to", "").strip().lower()
    message = data.get("message", "Hi! I'd like to connect our Claude instances.")

    if not target_email:
        return jsonify({"error": "Missing 'to' field"}), 400

    if target_email == sender_email:
        return jsonify({"error": "Cannot send friend request to yourself"}), 400

    # Look up target repo
    target_repo_name = email_to_repo_name(target_email)
    target_repo_path = SVN_REPOS_DIR / target_repo_name

    if not target_repo_path.exists():
        return jsonify({"error": f"User {target_email} not found"}), 404

    # Create friend request JSON
    request_content = {
        "from": sender_email,
        "to": target_email,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "message": message
    }

    # Commit to target's claudeconnect/friend_requests folder using svnmucc
    request_json = json.dumps(request_content, indent=2)

    # Write to temp file
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    temp_file.write(request_json)
    temp_file.close()

    # Make temp file readable (default is 0600 which blocks svnmucc)
    os.chmod(temp_file.name, 0o644)

    try:
        # Use svnmucc to put the file
        repo_url = f"file:///var/svn/repos/{target_repo_name}"
        filename = f"{sender_email.replace('@', '-').replace('.', '-')}.json"

        result = subprocess.run(
            [
                "sudo", "-u", "www-data", "svnmucc",
                "-U", repo_url,
                "put", temp_file.name, f"claudeconnect/friend_requests/{filename}",
                "-m", f"Friend request from {sender_email}"
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return jsonify({"error": f"Failed to send request: {result.stderr}"}), 500

        return jsonify({
            "success": True,
            "message": f"Friend request sent to {target_email}",
            "from": sender_email,
            "to": target_email
        })

    finally:
        os.unlink(temp_file.name)


# =============================================================================
# Ephemeral Test User Endpoints
# =============================================================================

@app.route("/api/test-user/create", methods=["POST"])
@require_admin
def create_test_user():
    """
    Create an ephemeral test user.

    Requires admin authentication via Bearer token.
    Body: {"ttl_hours": 24}

    Returns:
        JSON with email, svn_token, repo_url, expires_at
    """
    if not fernet:
        return jsonify({"error": "SVN tokens not configured"}), 500

    data = request.get_json() or {}
    ttl_hours = data.get("ttl_hours", 24)

    # Generate ephemeral identity
    user_id = secrets.token_hex(8)
    email = f"test-{user_id}@ephemeral.claudeconnect.io"
    repo_name = email_to_repo_name(email)

    # Create repo
    success, msg = create_svn_repo(repo_name, email)
    if not success:
        return jsonify({"error": msg}), 500

    # Generate long-lived SVN token (TTL-based)
    expiry = int(time.time()) + (ttl_hours * 3600)
    payload = f"{email}|{expiry}"
    svn_token = fernet.encrypt(payload.encode()).decode()

    # Track for cleanup
    users = load_ephemeral_users()
    users[email] = {
        "repo": repo_name,
        "expiry": expiry,
        "created": int(time.time()),
        "created_by": request.admin_email,
    }
    save_ephemeral_users(users)

    return jsonify({
        "email": email,
        "svn_token": svn_token,
        "repo_url": f"https://claudeconnect.io/svn/{repo_name}",
        "expires_at": expiry,
        "ttl_hours": ttl_hours,
    })


@app.route("/api/test-user/delete", methods=["POST"])
@require_admin
def delete_test_user():
    """
    Delete an ephemeral test user and their SVN repo.

    Requires admin authentication.
    Body: {"email": "test-xxx@ephemeral.claudeconnect.io"}
    """
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()

    if not email:
        return jsonify({"error": "Missing email"}), 400

    # Safety check: only allow deleting ephemeral users
    if not email.endswith("@ephemeral.claudeconnect.io"):
        return jsonify({"error": "Can only delete ephemeral test users"}), 400

    # Load ephemeral users tracking
    users = load_ephemeral_users()
    if email not in users:
        return jsonify({"error": "Test user not found"}), 404

    repo_name = users[email]["repo"]
    repo_path = SVN_REPOS_DIR / repo_name

    # Delete SVN repo
    if repo_path.exists():
        result = subprocess.run(
            ["sudo", "rm", "-rf", str(repo_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return jsonify({"error": f"Failed to delete repo: {result.stderr}"}), 500

    # Remove from tracking
    del users[email]
    save_ephemeral_users(users)

    return jsonify({
        "success": True,
        "deleted": email,
        "repo": repo_name,
    })


@app.route("/api/test-user/cleanup", methods=["POST"])
@require_admin
def cleanup_test_users():
    """
    Clean up expired ephemeral test users.

    Called by cron or manually. Deletes repos whose TTL has passed.
    """
    users = load_ephemeral_users()
    now = int(time.time())
    deleted = []

    for email, data in list(users.items()):
        if data["expiry"] < now:
            repo_path = SVN_REPOS_DIR / data["repo"]
            if repo_path.exists():
                result = subprocess.run(
                    ["sudo", "rm", "-rf", str(repo_path)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    deleted.append(email)
                    del users[email]
            else:
                # Repo already gone, just remove tracking
                deleted.append(email)
                del users[email]

    save_ephemeral_users(users)

    return jsonify({
        "success": True,
        "deleted": deleted,
        "remaining": len(users),
    })


@app.route("/api/test-user/list", methods=["GET"])
@require_admin
def list_test_users():
    """
    List all ephemeral test users.

    Requires admin authentication.
    """
    users = load_ephemeral_users()
    now = int(time.time())

    result = []
    for email, data in users.items():
        result.append({
            "email": email,
            "repo": data["repo"],
            "created": data["created"],
            "created_by": data.get("created_by", "unknown"),
            "expires_at": data["expiry"],
            "expired": data["expiry"] < now,
            "ttl_remaining": max(0, data["expiry"] - now),
        })

    return jsonify({
        "users": result,
        "total": len(result),
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
