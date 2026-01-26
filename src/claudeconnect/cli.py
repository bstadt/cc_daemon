"""Claude Connect CLI.

Main entry point for the claudeconnect command.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx


def get_mock_dir() -> Path | None:
    """Get mock directory path if CC_MOCK_DIR is set."""
    mock_dir = os.environ.get("CC_MOCK_DIR")
    if mock_dir:
        path = Path(mock_dir)
        # Return path even if it doesn't exist yet (for init)
        return path
    return None


def is_mock_mode() -> bool:
    """Check if running in mock/dev mode."""
    return os.environ.get("CC_MOCK_DIR") is not None


from .terminal_ui import (
    BOLD,
    CLEAR,
    DIM,
    LIME,
    RESET,
    WHITE,
    get_double_banner_lines,
    get_persistent_banner_lines,
    render_persistent_banner,
    reset_persistent_banner,
    run_claude_with_persistent_banner,
    should_use_persistent_banner,
)


from .auth import login as do_login, ensure_valid_token, decode_jwt_payload, refresh_token
from .config import (
    get_config, get_tokens, Config, Tokens, is_logged_in, get_email,
    get_test_user_email, get_test_user_credentials, list_test_users,
    TestUserCredentials, TEST_USERS_DIR, get_shadow_dir, sanitize_email,
    SERVER_URL, API_BASE_URL, email_to_repo_name, get_active_account,
    get_tokens_file, get_config_file, ACTIVE_ACCOUNT_FILE, LEGACY_TOKENS_FILE,
)
from .scanner import scan_directory

# Encryption is optional - only available if cryptography is installed
try:
    from .encryption import (
        is_encryption_available,
        generate_keypair,
        generate_master_key,
        load_master_key,
        load_public_key,
        get_key_fingerprint,
        save_friend_public_key,
        load_friend_public_key,
        encrypt_master_key_for_recipient,
        decrypt_received_master_key,
        save_friend_master_key,
        has_friend_master_key,
        should_encrypt_file,
        encrypt_file_with_master_key,
    )
    HAS_ENCRYPTION = is_encryption_available()
except ImportError:
    HAS_ENCRYPTION = False


def display_startup_banner(context_dir: Path, email: str, clear_screen: bool = True, peer_name: str | None = None) -> None:
    """Display ClaudeConnect startup banner with two Claude creatures and status.

    If peer_name is provided, shows a simplified "Interactive session with <peer>" banner
    instead of the full dashboard with notifications/conversations.
    """
    # Clear screen for clean display (optional)
    if clear_screen:
        print(CLEAR, end='')

    print()
    for line in get_double_banner_lines(email):
        print(line)

    print()

    # For interactive sessions, show simplified banner and skip notifications
    if peer_name:
        print(f"  {WHITE}{BOLD}Interactive session with {LIME}{peer_name}{RESET}")
        print()
        # Ensure terminal attributes are reset
        print(RESET, end='', flush=True)
        return

    display_notifications(context_dir)


def display_notifications(context_dir: Path) -> None:
    """Display friend request and conversation notifications."""
    # Check for friend requests and conversations
    # New structure per system2.md:
    # - Friend requests: claudeconnect/with-claudeconnect-io/
    # - Conversations & acceptances: claudeconnect/with-<peer-email>/
    claudeconnect_dir = context_dir / "claudeconnect"
    system_messages_dir = claudeconnect_dir / "with-claudeconnect-io"

    # Collect friend request notifications (pending requests + accepted notifications)
    friend_notifications = []  # List of (display_text, is_accepted)

    # Check for pending friend requests in with-claudeconnect-io/
    if system_messages_dir.exists():
        for f in system_messages_dir.glob("*.md"):
            try:
                content = f.read_text()
                # Friend request files contain "Friend Request from" in title
                if "Friend Request from" in content or "friend request" in content.lower():
                    # Extract email from **From**: line
                    for line in content.split("\n"):
                        if "**From**:" in line or "From:" in line:
                            sender = line.split(":", 1)[1].strip()
                            sender = sender.replace("**", "").strip()
                            friend_notifications.append((sender, False))
                            break
            except Exception:
                pass

    # Check with-<peer>/ folders for accepted friend requests and conversations
    accepted_friends = []
    if claudeconnect_dir.exists():
        for peer_dir in claudeconnect_dir.iterdir():
            if peer_dir.is_dir() and peer_dir.name.startswith("with-"):
                # Skip system messages folder
                if peer_dir.name == "with-claudeconnect-io":
                    continue
                # Look for acceptance notifications (friend-request-accepted type)
                for f in peer_dir.glob("*.md"):
                    try:
                        content = f.read_text()
                        if "friend-request-accepted" in content.lower() or "accepted your friend request" in content.lower():
                            # Extract email from **From**: line
                            for line in content.split("\n"):
                                if "**From**:" in line or "From:" in line:
                                    email_part = line.split(":", 1)[1].strip()
                                    email_part = email_part.replace("**", "").strip()
                                    if email_part not in accepted_friends:
                                        accepted_friends.append(email_part)
                                    break
                    except Exception:
                        pass

    # Add accepted friends to notifications
    for email_addr in accepted_friends:
        # Extract username from email for shorter display
        username = email_addr.split("@")[0] if "@" in email_addr else email_addr
        friend_notifications.append((f"{username} accepted your request!", True))

    # Get recent conversations (last 30 days) with topic preview
    # Conversations are now in claudeconnect/with-<peer-email>/<session-id>.md
    recent_convos = []  # List of (peer_email, topic_preview, mtime)
    if claudeconnect_dir.exists():
        month_ago = datetime.datetime.now().timestamp() - (30 * 24 * 60 * 60)
        for peer_dir in claudeconnect_dir.iterdir():
            if peer_dir.is_dir() and peer_dir.name.startswith("with-"):
                # Skip system messages folder
                if peer_dir.name == "with-claudeconnect-io":
                    continue
                peer_name = peer_dir.name[5:]  # Remove "with-" prefix
                # Convert back to email format (replace - with . and @)
                peer_email = peer_name.replace("-", ".")
                # Fix common email pattern: user.example.com -> user@example.com
                if peer_email.count(".") >= 2:
                    parts = peer_email.rsplit(".", 2)
                    if len(parts) == 3:
                        peer_email = f"{parts[0]}@{parts[1]}.{parts[2]}"

                latest_file = None
                latest_time = 0
                for f in peer_dir.glob("*.md"):
                    # Skip acceptance notification files
                    try:
                        content = f.read_text()
                        if "friend-request-accepted" in content.lower():
                            continue
                    except Exception:
                        pass
                    try:
                        mtime = f.stat().st_mtime
                        if mtime > latest_time:
                            latest_time = mtime
                            latest_file = f
                    except Exception:
                        pass

                if latest_file and latest_time > month_ago:
                    # Extract topic from file
                    topic = ""
                    try:
                        content = latest_file.read_text()
                        for line in content.split("\n"):
                            if line.startswith("**Topic**:"):
                                topic = line.split(":", 1)[1].strip()
                                break
                    except Exception:
                        pass
                    recent_convos.append((peer_email, topic, latest_time))

        # Sort by most recent
        recent_convos.sort(key=lambda x: x[2], reverse=True)
        recent_convos = recent_convos[:5]  # Limit to 5

    # Display boxes side by side if there's activity
    if friend_notifications or recent_convos:
        W = 34  # Total box width

        def truncate_with_ellipsis(text: str, max_len: int) -> str:
            """Truncate text with ellipsis if it exceeds max_len."""
            if len(text) <= max_len:
                return text
            return text[:max_len - 3] + "..."

        def make_box(title: str, items: list[str]) -> list[str]:
            """Create a box with title and items, exactly W chars wide."""
            lines = []
            # Header: ┌─ TITLE ───────┐
            title_truncated = truncate_with_ellipsis(title, W - 6)
            dashes = W - 5 - len(title_truncated)
            lines.append(f"┌─ {title_truncated} " + "─" * dashes + "┐")
            # Items: │ content      │
            max_content_len = W - 4  # 2 for "│ " and 2 for " │"
            for item in items:
                content = truncate_with_ellipsis(item, max_content_len)
                padding = max_content_len - len(content)
                lines.append(f"│ {content}" + " " * padding + " │")
            # Footer
            lines.append("└" + "─" * (W - 2) + "┘")
            return lines

        fr_lines = []
        if friend_notifications:
            items = [f"∙ {notif[0]}" for notif in friend_notifications[:5]]
            total = len(friend_notifications)
            fr_lines = make_box(f"FRIEND REQUESTS ({total})", items)

        conv_lines = []
        if recent_convos:
            items = []
            for peer_email, topic, _ in recent_convos:
                # Show email + topic preview
                username = peer_email.split("@")[0] if "@" in peer_email else peer_email
                if topic:
                    items.append(f"∙ {username}: {topic}")
                else:
                    items.append(f"∙ {username}")
            conv_lines = make_box("CONVERSATIONS", items)

        # Print side by side or single
        if fr_lines and conv_lines:
            max_lines = max(len(fr_lines), len(conv_lines))
            empty_space = " " * W
            for i in range(max_lines):
                left = fr_lines[i] if i < len(fr_lines) else empty_space
                right = conv_lines[i] if i < len(conv_lines) else empty_space
                print(f" {left}  {right}")
        elif fr_lines:
            for line in fr_lines:
                print(f" {line}")
        elif conv_lines:
            for line in conv_lines:
                print(f" {line}")

        print()

    # Ensure terminal attributes are reset at end of banner
    print(RESET, end='', flush=True)


def get_valid_token() -> Tokens | None:
    """
    Get a valid (non-expired) token, refreshing if needed.

    Checks for mock mode (CC_MOCK_DIR) and test user mode (CC_TEST_USER) first.

    Returns:
        Valid Tokens, or None if not logged in or refresh fails.
    """
    # Check for mock/dev mode first
    mock_dir = get_mock_dir()
    if mock_dir:
        # Return mock tokens
        mock_email = "dev@example.com"
        mock_tokens_file = mock_dir / ".mock" / "config" / "tokens.json"
        if mock_tokens_file.exists():
            data = json.loads(mock_tokens_file.read_text())
            mock_email = data.get("email", mock_email)
        return Tokens(
            id_token="mock-id-token",
            refresh_token="mock-refresh-token",
            email=mock_email,
        )

    # Check for test user mode
    test_user_email = get_test_user_email()
    if test_user_email:
        creds = get_test_user_credentials(test_user_email)
        if creds:
            # Check if test user token is expired
            if creds.expires_at < int(time.time()):
                print(f"Test user {test_user_email} has expired.")
                return None
            # Return a Tokens-like object for compatibility
            return Tokens(
                id_token="",  # Not used for test users
                refresh_token="",  # Not used for test users
                email=creds.email,
            )
        else:
            print(f"Test user {test_user_email} not found locally.")
            print("Run `claudeconnect test-user list` to see available test users.")
            return None

    # Normal OAuth flow
    tokens = get_tokens()
    if not tokens:
        return None

    # Check if token is expired
    payload = decode_jwt_payload(tokens.id_token)
    exp = payload.get("exp", 0)

    if exp < int(time.time()):
        # Token expired - try to refresh
        if tokens.refresh_token:
            new_tokens = refresh_token(tokens.refresh_token)
            if new_tokens:
                return new_tokens
            else:
                print("Failed to refresh token.")
                return None
        else:
            # No refresh token - need to re-login
            return None

    return tokens


def ensure_repo(token: str) -> dict:
    """
    Ensure user's repo exists on server.

    For mock mode (CC_MOCK_DIR) and test users (CC_TEST_USER), returns mock/stored info.

    Args:
        token: OAuth id_token (ignored for mock/test users)

    Returns:
        Dict with 'repo', 'url', 'email' keys.

    Raises:
        Exception on failure.
    """
    # Check for mock/dev mode first
    mock_dir = get_mock_dir()
    if mock_dir:
        mock_email = "dev@example.com"
        mock_repo_file = mock_dir / ".mock" / "api-ensure-repo.json"
        if mock_repo_file.exists():
            return json.loads(mock_repo_file.read_text())
        return {
            "repo": email_to_repo_name(mock_email),
            "url": f"file://{mock_dir}",  # Local mock "repo"
            "email": mock_email,
            "created": False,
        }

    # Check for test user mode
    test_user_email = get_test_user_email()
    if test_user_email:
        creds = get_test_user_credentials(test_user_email)
        if creds:
            return {
                "repo": email_to_repo_name(creds.email),
                "url": creds.repo_url,
                "email": creds.email,
                "created": False,  # Already created on server
            }
        raise Exception(f"Test user {test_user_email} not found locally")

    # Normal OAuth flow
    response = httpx.post(
        f"{SERVER_URL}/api/ensure-repo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    if response.status_code != 200:
        data = response.json()
        raise Exception(data.get("error", "Failed to ensure repo"))

    return response.json()


def _init_mock_environment(mock_dir: Path) -> bool:
    """
    Initialize mock environment with sample data for UX development.

    Creates directory structure and sample files for:
    - Friend requests (pending)
    - Conversations
    - Accepted friend notifications

    Args:
        mock_dir: Path to mock environment directory

    Returns:
        True if successful
    """
    mock_dir.mkdir(parents=True, exist_ok=True)

    # Create directory structure
    (mock_dir / "claudeconnect" / "with-claudeconnect-io").mkdir(parents=True, exist_ok=True)
    (mock_dir / "claudeconnect" / "conversations" / "with-alice-example-com").mkdir(parents=True, exist_ok=True)
    (mock_dir / "claudeconnect" / "conversations" / "with-bob-example-com").mkdir(parents=True, exist_ok=True)
    (mock_dir / "notes").mkdir(parents=True, exist_ok=True)
    (mock_dir / ".mock" / "config").mkdir(parents=True, exist_ok=True)

    # Sample friend request 1
    (mock_dir / "claudeconnect" / "with-claudeconnect-io" / "friend-request-carol-example-com.md").write_text(
        """# Friend Request from carol@example.com

Received: 2026-01-20T10:00:00Z
"""
    )

    # Sample friend request 2
    (mock_dir / "claudeconnect" / "with-claudeconnect-io" / "friend-request-david-example-com.md").write_text(
        """# Friend Request from david@example.com

Received: 2026-01-19T15:30:00Z
"""
    )

    # Sample conversation with Alice
    (mock_dir / "claudeconnect" / "conversations" / "with-alice-example-com" / "2026-01-15_abc12345.md").write_text(
        """# Conversation: Dev <-> Alice

**Session ID**: 2026-01-15_abc12345
**Date**: 2026-01-15T14:00:00
**Topic**: Architecture Planning

---

**Dev's Claude**: Let's discuss the system architecture...

**Alice's Claude**: I've been thinking about microservices vs monolith...
"""
    )

    # Sample new conversation
    (mock_dir / "claudeconnect" / "conversations" / "with-alice-example-com" / "2026-01-19_def67890.md").write_text(
        f"""# Conversation: Dev <-> Alice

**Session ID**: 2026-01-19_def67890
**Date**: {datetime.datetime.now().isoformat()}
**Topic**: Code Review

---

**Dev's Claude**: Can you review this PR?

**Alice's Claude**: Sure, looking at it now...
"""
    )

    # Sample accepted friend notification from Bob
    (mock_dir / "claudeconnect" / "conversations" / "with-bob-example-com" / "friend-accepted.md").write_text(
        """# Friend Request Accepted

**From**: bob@example.com
**Date**: 2026-01-19T14:00:00Z
**Type**: friend-request-accepted

Bob has accepted your friend request. You are now connected!
"""
    )

    # Sample note
    (mock_dir / "notes" / "sample-note.md").write_text(
        """# Sample Note

This is a sample note in your context directory.
"""
    )

    # Authz file
    (mock_dir / "authz").write_text(
        """[/]
dev@example.com = rw
alice@example.com = r
bob@example.com = r

[/claudeconnect/with-claudeconnect-io]
* = rw
dev@example.com = rw

[/claudeconnect/with-dev-example-com]
dev@example.com = rw
alice@example.com = rw
bob@example.com = rw
"""
    )

    # Privacy file
    (mock_dir / "privacy.md").write_text(
        """# Privacy Policy

This file controls what friends can see in your context.

## Public (friends can read)
- notes/
- projects/

## Private (only you)
- personal/
- .env files
"""
    )

    # Mock API response files
    (mock_dir / ".mock" / "api-ensure-repo.json").write_text(
        f'{{"repo": "dev-example-com", "url": "file://{mock_dir}", "email": "dev@example.com", "created": false}}'
    )

    (mock_dir / ".mock" / "config" / "tokens.json").write_text(
        '{"id_token": "mock-id-token", "refresh_token": "mock-refresh", "email": "dev@example.com"}'
    )

    return True


def generate_authz_content(
    email: str,
    private_files: list[str] | None = None,
    public_key_hex: str | None = None,
) -> str:
    """
    Generate initial authz file content for a new user.

    Per system2.md, the authz structure is:
    - [/] - owner has rw
    - [/claudeconnect/with-claudeconnect-io] - owner only (server uses admin bypass for writes)
    - [/claudeconnect/with-{owner-email}] - owner rw, friends get rw when added

    The public key is stamped at the top as a comment, making it globally readable.
    This allows anyone to encrypt content for this user without needing to friend first.

    Args:
        email: User's email
        private_files: List of file paths (relative to repo root) to make private
        public_key_hex: User's X25519 public key as hex string (64 chars)

    Returns:
        authz file content string
    """
    email_repo_name = email_to_repo_name(email)

    lines = []

    # Stamp public key at the top if provided
    if public_key_hex:
        lines.append(f"# Public-Key: {public_key_hex}")
        lines.append("")

    lines.extend([
        "[/]",
        f"{email} = rw",
        "",
        "# System messages folder (server writes here using admin bypass)",
        "[/claudeconnect/with-claudeconnect-io]",
        f"{email} = rw",
        "",
        f"# Friends can write conversations to your with-{email_repo_name} folder",
        f"[/claudeconnect/with-{email_repo_name}]",
        f"{email} = rw",
    ])

    # Add private file sections - only owner has access
    if private_files:
        lines.append("")
        lines.append("# Private files (contain sensitive information)")
        for file_path in sorted(set(private_files)):
            # Ensure path starts with /
            if not file_path.startswith("/"):
                file_path = "/" + file_path
            lines.append(f"[{file_path}]")
            lines.append(f"{email} = rw")

    return "\n".join(lines) + "\n"


def upload_authz_http(token: str, email: str, content: str) -> bool:
    """
    Upload authz file to v2s server via HTTP API.

    Args:
        token: OAuth id_token for authentication
        email: User's email address
        content: authz file content

    Returns:
        True if upload succeeded, False otherwise.
    """
    try:
        response = httpx.put(
            f"{API_BASE_URL}/files/{email}/authz",
            content=content.encode(),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if response.status_code == 200:
            return True
        else:
            print(f"  Error uploading authz: {response.text}")
            return False
    except httpx.HTTPError as e:
        print(f"  Error uploading authz: {e}")
        return False


def install_skill() -> bool:
    """
    Install the claudeconnect skill to ~/.claude/skills/.

    Returns:
        True if installed successfully.
    """
    try:
        import importlib.resources as pkg_resources

        # Destination: ~/.claude/skills/claudeconnect/SKILL.md
        skill_dir = Path.home() / ".claude" / "skills" / "claudeconnect"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_dest = skill_dir / "SKILL.md"

        # Read skill from package
        try:
            # Python 3.9+ with importlib.resources.files
            skill_content = pkg_resources.files("claudeconnect").joinpath("skills/SKILL.md").read_text()
        except (AttributeError, TypeError):
            # Fallback for older Python
            with pkg_resources.open_text("claudeconnect.skills", "SKILL.md") as f:
                skill_content = f.read()

        # Write skill file
        skill_dest.write_text(skill_content)
        return True

    except Exception as e:
        print(f"  Warning: Could not install skill: {e}")
        return False


def verify_init_structure(context_dir: Path, email: str) -> list[str]:
    """
    Verify that init created all expected directories and files.

    Shadow directory architecture (HTTP sync):
        ~/.claude-connect/accounts/<email>/shadow/
        ├── authz                           # Access control file (encrypted copy)
        └── claudeconnect/
            └── with-claudeconnect-io/      # System messages folder

        context_dir/                        # User's plaintext directory
        ├── authz                           # Access control file
        └── claudeconnect/
            └── with-claudeconnect-io/      # System messages folder

    Args:
        context_dir: The context directory to verify
        email: User's email (for shadow directory lookup)

    Returns:
        List of error messages for missing/invalid components.
        Empty list if everything is correct.
    """
    errors = []
    shadow_dir = get_shadow_dir(email)

    # Verify shadow directory exists
    if not shadow_dir.is_dir():
        errors.append(f"Shadow directory missing: {shadow_dir}")

    # Check claudeconnect directory structure in context dir
    cc_dir = context_dir / "claudeconnect"
    if not cc_dir.is_dir():
        errors.append("claudeconnect/ directory missing")
    else:
        # with-claudeconnect-io/ is the system messages folder (per system2.md)
        system_dir = cc_dir / "with-claudeconnect-io"
        if not system_dir.is_dir():
            errors.append("claudeconnect/with-claudeconnect-io/ directory missing")

    # Check authz file in context dir
    authz_file = context_dir / "authz"
    if not authz_file.is_file():
        errors.append("authz file missing")

    # Check skill installation
    skill_file = Path.home() / ".claude" / "skills" / "claudeconnect" / "SKILL.md"
    if not skill_file.is_file():
        errors.append("SKILL.md not installed at ~/.claude/skills/claudeconnect/")

    return errors


def init_context_dir(
    context_dir: Path,
    email: str,
    token: str,
    public_key_hex: str | None = None,
) -> bool:
    """
    Initialize a context directory using HTTP-based sync (v2s).

    Creates:
    - Shadow directory for encryption staging
    - authz file with proper permissions
    - claudeconnect/ directory structure

    Args:
        context_dir: The user's plaintext directory to initialize
        email: User email
        token: OAuth id_token for server communication
        public_key_hex: User's public key as hex string (stamped in authz)

    Returns:
        True if successful.
    """
    # Get shadow directory path (used for encryption staging)
    shadow_dir = get_shadow_dir(email)
    shadow_dir.mkdir(parents=True, exist_ok=True)

    # Create claudeconnect/ structure in both directories
    for target_dir in [shadow_dir, context_dir]:
        cc_dir = target_dir / "claudeconnect"
        system_messages_dir = cc_dir / "with-claudeconnect-io"

        # Create with-claudeconnect-io/ for system messages
        if not system_messages_dir.exists():
            system_messages_dir.mkdir(parents=True, exist_ok=True)

    # Handle authz file
    shadow_authz = shadow_dir / "authz"
    context_authz = context_dir / "authz"

    if shadow_authz.exists():
        # Authz exists in shadow, copy to context
        print("  Using existing authz file")
        shutil.copy2(shadow_authz, context_authz)
    else:
        # Create new authz
        print("  Creating authz file...")
        authz_content = generate_authz_content(email, None, public_key_hex)
        # Write to both locations
        shadow_authz.write_text(authz_content)
        context_authz.write_text(authz_content)

    # Upload authz to v2s server
    print("  Uploading authz to server...")
    authz_content = context_authz.read_text()
    if not upload_authz_http(token, email, authz_content):
        print("  Failed to upload authz to server")
        return False

    print(f"  Shadow directory: {shadow_dir}")
    return True


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """
    Claude Connect - Contextualized Claude instances communicating.

    Run without arguments to start Claude with sync enabled.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand - run main flow
        ctx.invoke(start)


@cli.command()
def login():
    """Login to Claude Connect with Google."""
    print("Logging in to Claude Connect...")

    result = do_login()

    if result.success:
        print(f"\n✓ Logged in as {result.tokens.email}")
        print(f"\nRun `claudeconnect` in your context directory to start.")
    else:
        print(f"\n✗ Login failed: {result.error}")
        sys.exit(1)


@cli.command()
@click.option("--full", is_flag=True, help="Also remove local config for this account.")
def logout(full: bool):
    """Logout of Claude Connect and remove local credentials."""
    active_email = get_active_account()
    tokens = get_tokens()
    if not active_email and tokens:
        active_email = tokens.email
    if not active_email and not LEGACY_TOKENS_FILE.exists():
        print("Not logged in.")
        return

    print("Logging out of ClaudeConnect...")

    if active_email:
        tokens_file = get_tokens_file(active_email)
        if tokens_file.exists():
            tokens_file.unlink()
            print(f"  Removed tokens for {active_email}")
        else:
            print(f"  No tokens found for {active_email}")
        if full:
            config_file = get_config_file(active_email)
            if config_file.exists():
                config_file.unlink()
                print("  Removed config.json")
            else:
                print("  No config.json found")

    if LEGACY_TOKENS_FILE.exists():
        LEGACY_TOKENS_FILE.unlink()
        print("  Removed legacy tokens.json")

    if ACTIVE_ACCOUNT_FILE.exists():
        ACTIVE_ACCOUNT_FILE.unlink()

    if full:
        print("\n✓ Logged out and cleared local configuration.")
    else:
        print("\n✓ Logged out. Run `claudeconnect login` to sign in again.")


@cli.command()
def status():
    """Show current status."""
    tokens = get_valid_token()
    config = get_config()

    if not tokens:
        print("Not logged in. Run `claudeconnect login` first.")
        return

    # Check for test user mode
    test_user_email = get_test_user_email()
    if test_user_email:
        creds = get_test_user_credentials(test_user_email)
        if creds:
            print(f"[Test User Mode]")
            print(f"Email: {creds.email}")
            print(f"Repo: {creds.repo_url}")
            expires_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(creds.expires_at))
            if creds.expires_at < int(time.time()):
                print(f"Status: EXPIRED (was {expires_str})")
            else:
                print(f"Expires: {expires_str}")
            if creds.context_dir:
                print(f"Context directory: {creds.context_dir}")
            return

    print(f"Logged in as: {tokens.email}")
    print(f"Repo: {email_to_repo_name(tokens.email)}")

    if config.context_dir:
        print(f"Context directory: {config.context_dir}")
        context_dir = Path(config.context_dir)
        shadow_dir = get_shadow_dir(tokens.email)

        # Check initialization status
        if shadow_dir.is_dir() and (context_dir / "claudeconnect").is_dir():
            print("Sync: Initialized (HTTP)")
        else:
            print("Sync: Not initialized")

        # Show encryption status
        if config.encryption_enabled:
            print("Encryption: Enabled")
        else:
            print("Encryption: Disabled")
    else:
        print("Context directory: Not set")


@cli.command()
def dashboard():
    """Show ClaudeConnect dashboard with friend requests and conversations."""
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    if not config.context_dir:
        print("No context directory set. Run `claudeconnect init` first.")
        sys.exit(1)

    context_dir = Path(config.context_dir)
    if not context_dir.exists():
        print(f"Context directory not found: {context_dir}")
        sys.exit(1)

    display_startup_banner(context_dir, tokens.email, clear_screen=False)


@cli.command()
@click.option("--system-prompt", type=str, default=None, help="System prompt to pass to Claude")
@click.option("--initial-prompt", type=str, default=None, help="Initial user message to send")
@click.option("--context-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None,
              help="Override context directory (for interactive sessions)")
@click.option("--peer", type=str, default=None, help="Peer name for interactive session banner")
@click.option("--session-id", type=str, default=None, help="Session ID (UUID) for transcript tracking")
def start(system_prompt: str | None, initial_prompt: str | None, context_dir: Path | None, peer: str | None, session_id: str | None):
    """Start Claude with sync enabled (default command)."""
    # Check for mock/dev mode
    mock_dir = get_mock_dir()
    if mock_dir:
        tokens = get_valid_token()  # Returns mock tokens
        context_dir = mock_dir

        # Check if mock environment is initialized
        if not (mock_dir / "claudeconnect").exists():
            print("[DEV MODE] Mock environment not initialized.")
            print(f"Run: CC_MOCK_DIR={mock_dir} claudeconnect init")
            sys.exit(1)

        print(f"[DEV MODE] Using mock environment: {mock_dir}")
        print(f"Logged in as: {tokens.email}")
        print("\nSkipping sync (mock mode)...")

        # Start Claude without sync
        print("\nStarting Claude Code...")
        print("(Sync disabled in dev mode)\n")

        try:
            process = subprocess.run(
                ["claude"],
                cwd=context_dir,
            )
        except FileNotFoundError:
            print("Error: 'claude' command not found.")
            print("Make sure Claude Code is installed.")
        except KeyboardInterrupt:
            pass

        print("\nGoodbye!")
        return

    # Check login and token validity
    tokens = get_valid_token()
    if not tokens:
        print("You're not logged in yet.\n")
        response = input("Would you like to login now? [Y/n] ").strip().lower()
        if response in ("", "y", "yes"):
            result = do_login()
            if result.success:
                print(f"\n✓ Logged in as {result.tokens.email}\n")
                tokens = result.tokens
            else:
                print(f"\n✗ Login failed: {result.error}")
                sys.exit(1)
        else:
            print("\nRun `claudeconnect login` when you're ready.")
            sys.exit(0)

    config = get_config()
    cwd = Path.cwd()

    # Determine context directory (use override if provided)
    if context_dir:
        # Using explicit context directory (e.g., for interactive sessions)
        pass  # context_dir already set from parameter
    elif config.context_dir:
        context_dir = Path(config.context_dir)
        if cwd != context_dir and not cwd.is_relative_to(context_dir):
            print(f"Your context directory is: {context_dir}")
            print(f"Current directory: {cwd}")
            print(f"\nRun `claudeconnect` from your context directory.")
            print(f"Or use `claudeconnect init` here to switch.")
            sys.exit(1)
    else:
        # First time - need to init
        print("No context directory configured yet.\n")
        print(f"Current directory: {cwd}")
        response = input("\nWould you like to initialize this directory? [Y/n] ").strip().lower()
        if response in ("", "y", "yes"):
            # Run init with encryption enabled by default
            print()
            ctx = click.get_current_context()
            ctx.invoke(init, no_encrypt=False)
            # Reload config after init
            config = get_config()
            if not config.context_dir:
                sys.exit(1)
            context_dir = Path(config.context_dir)
        else:
            print("\nRun `claudeconnect init` in your context directory when ready.")
            sys.exit(0)

    # Interactive sessions have quieter output
    is_interactive = peer is not None

    if not is_interactive:
        print(f"Connecting as {tokens.email}...")

    # Initial sync using HTTP
    if not is_interactive:
        print("\nSyncing...")
    if not sync_http(context_dir, tokens.email, tokens.id_token, verbose=not is_interactive):
        if not is_interactive:
            print("  Sync failed")
        sys.exit(1)

    # Display startup banner with friend requests and conversations
    banner_lines = None
    if should_use_persistent_banner():
        banner_lines = get_persistent_banner_lines(tokens.email, peer)
        render_persistent_banner(banner_lines)
        if not is_interactive:
            display_notifications(context_dir)
    else:
        display_startup_banner(context_dir, tokens.email, peer_name=peer)

    # Start sync loop and Claude
    if not is_interactive:
        print("Starting Claude Code with sync enabled...")
        print(f"{DIM}(Sync runs every 30 seconds in background){RESET}\n")

    # Ensure terminal state is clean before launching Claude
    # This prevents ANSI escape sequences from bleeding into Claude's rendering
    print(RESET, end='', flush=True)
    sys.stdout.flush()

    # Run async main with HTTP sync
    asyncio.run(run_with_http_sync(
        context_dir, tokens.email, tokens.id_token,
        system_prompt=system_prompt, initial_prompt=initial_prompt,
        session_id=session_id,
        banner_lines=banner_lines,
        render_initial_banner=False,
    ))


async def run_with_http_sync(
    context_dir: Path,
    email: str,
    id_token: str,
    interval: int = 30,
    system_prompt: str | None = None,
    initial_prompt: str | None = None,
    session_id: str | None = None,
    banner_lines: list[str] | None = None,
    render_initial_banner: bool = True,
):
    """Run Claude Code with background HTTP sync loop."""
    stop_event = asyncio.Event()

    async def sync_loop():
        """Background sync loop using HTTP."""
        while not stop_event.is_set():
            try:
                # Wait for interval or stop event
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    break  # Stop event was set
                except asyncio.TimeoutError:
                    pass  # Interval elapsed, do sync

                # Refresh token if needed
                tokens = get_valid_token()
                if tokens:
                    sync_http(context_dir, tokens.email, tokens.id_token)
            except Exception as e:
                # Log but don't crash the sync loop
                pass

    # Start sync loop task
    sync_task = asyncio.create_task(sync_loop())

    try:
        # Build Claude command with optional prompts
        claude_args = ["claude"]
        if system_prompt:
            claude_args.extend(["--system-prompt", system_prompt])
        if session_id:
            claude_args.extend(["--session-id", session_id])
        if initial_prompt:
            claude_args.append(initial_prompt)

        use_persistent_banner = banner_lines is not None and should_use_persistent_banner()

        if use_persistent_banner:
            await asyncio.to_thread(
                run_claude_with_persistent_banner,
                claude_args,
                context_dir,
                banner_lines,
                render_initial_banner,
            )
        else:
            # Run Claude Code
            process = await asyncio.create_subprocess_exec(
                *claude_args,
                stdin=None,  # Inherit stdin
                stdout=None,  # Inherit stdout
                stderr=None,  # Inherit stderr
                cwd=context_dir,
            )

            await process.wait()

    except FileNotFoundError:
        print("Error: 'claude' command not found.")
        print("Make sure Claude Code is installed.")
    except KeyboardInterrupt:
        pass
    finally:
        # Stop sync loop
        stop_event.set()
        await sync_task
        if banner_lines is not None:
            reset_persistent_banner()
        print("\nSync stopped. Goodbye!")


@cli.command()
@click.option("--no-encrypt", is_flag=True, help="Disable client-side encryption")
def init(no_encrypt: bool):
    """Initialize current directory as context directory.

    Encryption is enabled by default (X25519 + AES-256-GCM).
    Use --no-encrypt to disable if you don't need privacy.
    """
    encrypt = not no_encrypt
    # Check for mock/dev mode
    mock_dir = get_mock_dir()
    if mock_dir:
        print("[DEV MODE] Initializing mock environment...")
        print(f"  Directory: {mock_dir}")
        if _init_mock_environment(mock_dir):
            print("\n✓ Mock environment initialized with sample data:")
            print("  - 2 pending friend requests (carol, david)")
            print("  - 2 conversations with alice")
            print("  - 1 accepted friend notification (bob)")
            print(f"\nRun: CC_MOCK_DIR={mock_dir} claudeconnect start")
        else:
            print("  Failed to initialize mock environment")
            sys.exit(1)
        return

    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    cwd = Path.cwd()

    # Validate encryption requirements
    if encrypt and not HAS_ENCRYPTION:
        print("Warning: Encryption requires cryptography package.")
        print("  Install with: pip install claudeconnect[encryption]")
        print("  Continuing without encryption...")
        encrypt = False

    if config.context_dir and Path(config.context_dir) != cwd:
        print(f"Warning: Switching context directory")
        print(f"  From: {config.context_dir}")
        print(f"  To: {cwd}")
        if not click.confirm("Continue?"):
            return

    print(f"Connecting as {tokens.email}...")

    # Set up encryption if requested
    public_key_hex = None
    fingerprint = None
    if encrypt:
        print("  Setting up encryption...")
        try:
            # Generate or load X25519 keypair (account-scoped)
            try:
                _, public_bytes = generate_keypair(tokens.email)
                fingerprint = get_key_fingerprint(public_bytes)
                print(f"  Generated keypair (fingerprint: {fingerprint})")
            except FileExistsError:
                # Keys already exist, load them
                public_bytes = load_public_key(tokens.email)
                fingerprint = get_key_fingerprint(public_bytes)
                print(f"  Using existing keypair (fingerprint: {fingerprint})")

            # Generate or load master key (account-scoped)
            try:
                generate_master_key(tokens.email)
                print("  Generated master encryption key")
            except FileExistsError:
                # Master key already exists
                load_master_key(tokens.email)  # Verify it's loadable
                print("  Using existing master key")

            # Convert public key to hex for authz
            public_key_hex = public_bytes.hex()

        except Exception as e:
            print(f"  Error generating keys: {e}")
            print("  Continuing without encryption...")
            encrypt = False
            public_key_hex = None

    # Initialize using HTTP-based sync (v2s)
    print(f"\nInitializing: {cwd}")
    if init_context_dir(cwd, tokens.email, tokens.id_token, public_key_hex):
        config.context_dir = str(cwd)
        config.encryption_enabled = encrypt
        config.save(tokens.email)

        # Install skill for Claude Code
        if install_skill():
            print("  Installed claudeconnect skill")
        else:
            print("  Warning: Could not install claudeconnect skill")
            print("    You may need to manually copy SKILL.md to ~/.claude/skills/claudeconnect/")

        # Verify directory structure was created correctly
        verification_errors = verify_init_structure(cwd, tokens.email)
        if verification_errors:
            print("\n⚠ Warning: Some components were not set up correctly:")
            for error in verification_errors:
                print(f"    - {error}")
            print("  You may need to run `claudeconnect init` again or create these manually.")

        print("\n✓ Context directory initialized")
        if encrypt:
            print("  Encryption: ENABLED (zero-trust)")
            print(f"  Key fingerprint: {fingerprint}")
            print(f"  Your private key is stored at ~/.claude-connect/accounts/{tokens.email}/keys/private.key")
        print(f"  Run `claudeconnect` to start Claude with sync.")
    else:
        sys.exit(1)


def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    import hashlib
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def compute_bytes_sha256(content: bytes) -> str:
    """Compute SHA256 hash of bytes."""
    import hashlib
    return hashlib.sha256(content).hexdigest()


def sync_http(context_dir: Path, email: str, id_token: str, max_workers: int = 10, verbose: bool = False) -> bool:
    """Sync local files with server using HTTP API.

    Uses shadow directory for encrypted file storage and comparison.
    - Shadow dir contains encrypted versions (matches server)
    - Context dir contains plaintext versions (user's working copy)
    - Conflicts resolved by most recent mtime
    - Uploads and downloads run in parallel for speed

    Args:
        verbose: If True, print progress and summary. Default False for silent background sync.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    config = get_config(email)
    encryption_enabled = config.encryption_enabled and HAS_ENCRYPTION

    # Shadow directory stores encrypted files (mirrors server)
    shadow_dir = get_shadow_dir(email)
    shadow_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {id_token}"}

    # Step 1: Get manifest from server (encrypted hashes)
    try:
        response = httpx.get(
            f"{API_BASE_URL}/manifest/{email}",
            headers=headers,
            timeout=60,
        )
        if response.status_code != 200:
            print(f"  Failed to get manifest: {response.text}")
            return False
        manifest = response.json()
    except Exception as e:
        print(f"  Error getting manifest: {e}")
        return False

    server_files = {f["path"]: f for f in manifest.get("files", [])}

    # Step 2: Build shadow directory manifest (encrypted files)
    shadow_files = {}
    for file_path in shadow_dir.rglob("*"):
        if file_path.is_file():
            rel_path = str(file_path.relative_to(shadow_dir))
            # Skip hidden files (any path component starting with '.')
            if any(part.startswith('.') for part in Path(rel_path).parts):
                continue
            shadow_files[rel_path] = {
                "path": rel_path,
                "sha256": compute_file_sha256(file_path),
                "mtime": file_path.stat().st_mtime,
            }

    # Step 3: Build context directory manifest (plaintext files)
    context_files = {}
    for file_path in context_dir.rglob("*"):
        if file_path.is_file():
            rel_path = str(file_path.relative_to(context_dir))
            # Skip hidden files (any path component starting with '.')
            if any(part.startswith('.') for part in Path(rel_path).parts):
                continue
            context_files[rel_path] = {
                "path": rel_path,
                "mtime": file_path.stat().st_mtime,
            }

    # Step 4: Categorize files into upload/download lists
    to_upload = []  # (path, context_path, shadow_path)
    to_download = []  # (path, shadow_path, context_path, server_info)

    all_paths = set(server_files.keys()) | set(shadow_files.keys()) | set(context_files.keys())

    for path in all_paths:
        server_info = server_files.get(path)
        shadow_info = shadow_files.get(path)
        context_info = context_files.get(path)

        shadow_path = shadow_dir / path
        context_path = context_dir / path

        # Case 1: File exists on server but not in shadow (or hash differs)
        if server_info and (not shadow_info or shadow_info["sha256"] != server_info["sha256"]):
            local_mtime = context_info["mtime"] if context_info else 0
            server_mtime = server_info.get("mtime", 0)

            if local_mtime > server_mtime and context_info:
                # Local is newer - upload
                to_upload.append((path, context_path, shadow_path))
            else:
                # Server is newer - download
                to_download.append((path, shadow_path, context_path, server_info))

        # Case 2: File exists locally but not on server - upload
        elif context_info and not server_info:
            to_upload.append((path, context_path, shadow_path))

        # Case 3: File in shadow matches server
        elif shadow_info and server_info and shadow_info["sha256"] == server_info["sha256"]:
            if context_info and context_info["mtime"] > shadow_info["mtime"]:
                # Context changed - upload
                to_upload.append((path, context_path, shadow_path))
            elif not context_info:
                # Context missing - decrypt from shadow to context
                to_download.append((path, shadow_path, context_path, server_info))

    total_ops = len(to_upload) + len(to_download)
    if total_ops == 0:
        return True

    # Progress tracking
    completed = 0
    uploaded = 0
    downloaded = 0
    errors = []
    lock = threading.Lock()

    def print_progress(action: str, path: str):
        nonlocal completed
        with lock:
            completed += 1
            if verbose:
                pct = int(completed / total_ops * 100)
                # Clear line and print progress
                print(f"\r  [{pct:3d}%] {action}: {path[:60]:<60}", end="", flush=True)

    def upload_file(path: str, context_path: Path, shadow_path: Path) -> bool:
        """Upload a single file to server."""
        nonlocal uploaded
        try:
            content = context_path.read_bytes()
            encrypted_content = content
            if encryption_enabled and should_encrypt_file(path):
                try:
                    encrypted_content = encrypt_file_with_master_key(content, email)
                except Exception:
                    pass  # Upload unencrypted if encryption fails

            response = httpx.put(
                f"{API_BASE_URL}/files/{email}/{path}",
                headers=headers,
                content=encrypted_content,
                timeout=60,
            )
            if response.status_code == 200:
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                shadow_path.write_bytes(encrypted_content)
                with lock:
                    uploaded += 1
                print_progress("UP", path)
                return True
            else:
                with lock:
                    errors.append(f"Upload {path}: HTTP {response.status_code}")
                return False
        except Exception as e:
            with lock:
                errors.append(f"Upload {path}: {e}")
            return False

    def download_file(path: str, shadow_path: Path, context_path: Path) -> bool:
        """Download a single file from server."""
        nonlocal downloaded
        try:
            response = httpx.get(
                f"{API_BASE_URL}/files/{email}/{path}",
                headers=headers,
                timeout=60,
            )
            if response.status_code == 200:
                encrypted_content = response.content

                # Save encrypted to shadow
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                shadow_path.write_bytes(encrypted_content)

                # Decrypt and save to context
                decrypted_content = encrypted_content
                if encryption_enabled:
                    try:
                        from .encryption import is_encrypted_file, decrypt_file_with_master_key
                        if is_encrypted_file(encrypted_content):
                            decrypted_content = decrypt_file_with_master_key(encrypted_content, email)
                    except Exception:
                        pass  # Keep as-is if decryption fails

                context_path.parent.mkdir(parents=True, exist_ok=True)
                context_path.write_bytes(decrypted_content)
                with lock:
                    downloaded += 1
                print_progress("DOWN", path)
                return True
            else:
                with lock:
                    errors.append(f"Download {path}: HTTP {response.status_code}")
                return False
        except Exception as e:
            with lock:
                errors.append(f"Download {path}: {e}")
            return False

    # Step 5: Execute uploads and downloads in parallel
    if verbose:
        print(f"  Syncing {len(to_upload)} upload(s), {len(to_download)} download(s)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []

        # Submit all uploads
        for path, context_path, shadow_path in to_upload:
            futures.append(executor.submit(upload_file, path, context_path, shadow_path))

        # Submit all downloads
        for path, shadow_path, context_path, _ in to_download:
            futures.append(executor.submit(download_file, path, shadow_path, context_path))

        # Wait for all to complete
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                with lock:
                    errors.append(str(e))

    # Clear progress line and print summary
    if verbose:
        print(f"\r  Downloaded {downloaded} file(s), uploaded {uploaded} file(s)" + " " * 40)

        if errors:
            print(f"  Warnings ({len(errors)}):")
            for err in errors[:5]:
                print(f"    - {err}")
            if len(errors) > 5:
                print(f"    ... and {len(errors) - 5} more")

    # Handle interactive session transcripts
    try:
        from .transcripts import (
            discover_new_interactive_transcripts,
            import_transcript,
        )

        # Discover new transcripts from Claude Code's storage
        new_transcripts = discover_new_interactive_transcripts(email, context_dir)

        for jsonl_path, metadata in new_transcripts:
            # Import to local context (sync_http will upload to our repo automatically)
            transcript_path = import_transcript(jsonl_path, metadata, email, context_dir)

            if transcript_path is None:
                continue
            # Transcript is saved locally; upload occurs on the next sync cycle.
            # Peers pull from our with-<peer> folder in our repo.

    except Exception:
        # Silent failure - don't crash sync loop
        pass

    # Auto-accept reciprocal friend requests (from people we already friended)
    try:
        accepted = auto_accept_reciprocal_requests(context_dir, email, id_token, verbose=verbose)
        if accepted and verbose:
            print(f"  Auto-accepted {accepted} reciprocal friend request(s)")
    except Exception:
        # Silent failure - don't crash sync loop
        pass

    return True


@cli.command()
def sync():
    """Manually trigger a sync with the server."""
    tokens = get_valid_token()
    config = get_config()

    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    if not config.context_dir:
        print("No context directory configured.")
        sys.exit(1)

    context_dir = Path(config.context_dir)

    print("Syncing...")
    if sync_http(context_dir, tokens.email, tokens.id_token, verbose=True):
        print("✓ Sync complete")
    else:
        sys.exit(1)


@cli.command()
@click.argument("peer_email")
@click.option("--topic", "-t", help="Conversation topic")
@click.option("--single", is_flag=True, help="Use single-instance mode (one Claude simulates both sides)")
@click.option("--turns", default=6, help="Max conversation turns (default: 6)")
def session(peer_email: str, topic: str | None, single: bool, turns: int):
    """Start a conversation session with a friend's Claude.

    By default, runs two separate Claude instances (each only sees their own context).
    Use --single to have one Claude simulate both sides (legacy mode).
    """
    print(f"Starting session with {peer_email}...")

    if single:
        from .session import run_session
        print("  Mode: Single-instance (simulated conversation)")
        success, result = asyncio.run(run_session(peer_email, topic))
    else:
        from .session import run_dual_session
        print("  Mode: Dual-instance (separate Claude per user)")
        success, result = asyncio.run(run_dual_session(peer_email, topic, turns))

    if success:
        print(f"\n✓ Session complete!")
        print(f"  Transcript: {result}")
    else:
        print(f"\n✗ Session failed: {result}")
        sys.exit(1)


@cli.command()
@click.argument("peer_email")
def pull(peer_email: str):
    """Pull a friend's context without starting a session."""
    from .session import pull_peer_context_http

    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    print(f"Pulling {peer_email}'s context...")
    peer_dir = pull_peer_context_http(peer_email, tokens.id_token, tokens.email)

    if peer_dir:
        print(f"\n✓ Context pulled to: {peer_dir}")
    else:
        print(f"\n✗ Failed to pull context")
        sys.exit(1)


@cli.command()
@click.argument("peer_email")
def interactive(peer_email: str):
    """Start an interactive session with a friend's Claude.

    Opens a new Terminal window where you can chat directly with a Claude
    instance that has access to your friend's context. The conversation
    will be captured and can be shared with both parties.

    This command is macOS only. For other platforms, use `claudeconnect session`
    for autonomous conversations between Claudes.

    Example:
        claudeconnect interactive alice@example.com
    """
    from .session import run_interactive_session

    success, result = run_interactive_session(peer_email)

    if not success:
        print(f"\n✗ Failed to start interactive session: {result}")
        sys.exit(1)


def add_friend_to_authz(authz_path: Path, my_email: str, peer_email: str) -> bool:
    """
    Add a friend to the authz file with appropriate permissions.

    Grants:
    - Read access to [/] (can read your context)
    - Write access to [/claudeconnect/with-{peer_email}] (peer can push conversations to you)

    The with-folder is named after the peer because they are the one writing to it.
    E.g., if alice@ex.com friends bob@ex.com, alice's authz grants bob write access
    to [/claudeconnect/with-bob-ex-com] so bob can save transcripts there.

    Args:
        authz_path: Path to authz file
        my_email: Owner's email
        peer_email: Friend's email to add

    Returns:
        True if changes were made, False if friend already has access.
    """
    authz_content = authz_path.read_text()
    lines = authz_content.split('\n')
    new_lines = []
    changes_made = False

    my_email_repo_name = email_to_repo_name(my_email)
    peer_email_repo_name = email_to_repo_name(peer_email)
    # The with-folder is named after the PEER - they write conversations TO your repo
    peer_with_section = f"[/claudeconnect/with-{peer_email_repo_name}]"

    # Track which sections we've seen
    current_section = None
    added_to_root = False
    added_to_with = False
    with_section_exists = False

    # Check if friend already has access
    has_root_access = f"{peer_email} = r" in authz_content or f"{peer_email} = rw" in authz_content
    has_with_access = False

    # Check with-{peer_email} section specifically
    in_with_section = False
    for line in lines:
        if line.strip().startswith('['):
            in_with_section = peer_with_section in line
            if in_with_section:
                with_section_exists = True
        elif in_with_section and peer_email in line:
            has_with_access = True

    # Process lines and add friend where needed
    for i, line in enumerate(lines):
        new_lines.append(line)

        # Track current section
        if line.strip().startswith('['):
            current_section = line.strip()

        # Add read access after owner's rw line in [/] section
        if (current_section == '[/]' and
            not added_to_root and
            not has_root_access and
            '= rw' in line and
            my_email in line):
            new_lines.append(f"{peer_email} = r")
            added_to_root = True
            changes_made = True
            print(f"  Added {peer_email} read access to [/]")

        # Add write access after owner's rw line in [/claudeconnect/with-{peer_email}] section
        if (current_section == peer_with_section and
            not added_to_with and
            not has_with_access and
            '= rw' in line and
            my_email in line):
            new_lines.append(f"{peer_email} = rw")
            added_to_with = True
            changes_made = True
            print(f"  Added {peer_email} write access to {peer_with_section}")

    # If with-{peer_email} section doesn't exist, add it
    if not with_section_exists:
        new_lines.append("")
        new_lines.append(f"# {peer_email} can write conversations to your with-{peer_email_repo_name} folder")
        new_lines.append(peer_with_section)
        new_lines.append(f"{my_email} = rw")
        new_lines.append(f"{peer_email} = rw")
        changes_made = True
        print(f"  Created {peer_with_section} section")
        print(f"  Added {peer_email} write access to {peer_with_section}")
    elif not added_to_with and not has_with_access:
        # Section exists but we didn't find owner's line - append to end of section
        final_lines = []
        in_with = False
        added = False
        for line in new_lines:
            final_lines.append(line)
            if peer_with_section in line:
                in_with = True
            elif in_with and not added:
                # Add after first line of section
                final_lines.append(f"{peer_email} = rw")
                added = True
                changes_made = True
                print(f"  Added {peer_email} write access to {peer_with_section}")
                in_with = False
        new_lines = final_lines

    if changes_made:
        authz_path.write_text('\n'.join(new_lines))
    else:
        print(f"  {peer_email} already has access in your authz")

    return changes_made


def remove_friend_from_authz(authz_path: Path, peer_email: str) -> bool:
    """
    Remove a friend's access from the authz file.

    Removes:
    - Read access from [/] section
    - Write access from [/claudeconnect/with-{peer}] section
    - Legacy write access from [/claudeconnect/conversations] if present

    Args:
        authz_path: Path to authz file
        peer_email: Friend's email to remove

    Returns:
        True if changes were made, False otherwise.
    """
    authz_content = authz_path.read_text()
    lines = authz_content.split('\n')
    new_lines = []
    changes_made = False

    peer_email_repo_name = email_to_repo_name(peer_email)
    peer_with_section = f"[/claudeconnect/with-{peer_email_repo_name}]"
    legacy_section = "[/claudeconnect/conversations]"

    current_section = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith('['):
            current_section = stripped

        if current_section in ('[/]', peer_with_section, legacy_section):
            if '=' in stripped:
                left = stripped.split('=', 1)[0].strip()
                if left == peer_email:
                    if current_section == '[/]':
                        print(f"  Removed {peer_email} read access from [/]")
                    elif current_section == peer_with_section:
                        print(f"  Removed {peer_email} write access from {peer_with_section}")
                    else:
                        print(f"  Removed {peer_email} write access from {legacy_section}")
                    changes_made = True
                    continue

        new_lines.append(line)

    if changes_made:
        authz_path.write_text('\n'.join(new_lines))
    else:
        print(f"  {peer_email} not found in your authz")

    return changes_made


def fetch_peer_public_key(peer_email: str) -> bytes | None:
    """
    Fetch a peer's public key from the server API.

    The public key is stored in their authz file and exposed via
    /api/keys/<email> endpoint.

    Args:
        peer_email: Peer's email address

    Returns:
        Public key bytes (32 bytes) or None if not found
    """
    api_url = f"{API_BASE_URL}/keys/{peer_email}"

    try:
        response = httpx.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            public_key_hex = data.get("public_key")
            if public_key_hex and len(public_key_hex) == 64:
                return bytes.fromhex(public_key_hex)
        elif response.status_code == 404:
            # User not found or no public key
            pass
    except Exception as e:
        print(f"  Warning: Could not fetch peer's public key: {e}")

    return None


@cli.command()
@click.argument("peer_email")
def friend(peer_email: str):
    """Send a friend request to another user.

    This command:
    1. Fetches peer's public key from their authz
    2. Encrypts your master key for them (so they can read your files immediately)
    3. Updates your authz to grant them read access + write to with-{peer}/
    4. Creates claudeconnect/with-{peer}/ directory for them to write conversations to
    5. Uploads authz to server
    6. Sends a friend request with the encrypted master key
    """
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    if not config.context_dir:
        print("No context directory configured. Run `claudeconnect init` first.")
        sys.exit(1)

    peer_email = peer_email.strip().lower()
    my_email = tokens.email

    if peer_email == my_email:
        print("Cannot friend yourself.")
        sys.exit(1)

    print(f"Sending friend request to {peer_email}...")

    # Step 1: Fetch peer's public key from their authz
    print("  Fetching peer's public key...")
    peer_public_key = fetch_peer_public_key(peer_email)
    if not peer_public_key:
        print(f"  Warning: Could not find public key for {peer_email}")
        print(f"  They may not have encryption set up, or their repo doesn't exist.")
        print(f"  Continuing without encrypted master key...")

    # Step 2: Encrypt our master key for them (if we have both keys)
    encrypted_master_key_hex = None
    my_public_key_hex = None
    if HAS_ENCRYPTION and peer_public_key:
        try:
            # Load our master key and encrypt it for the peer (account-scoped)
            my_master_key = load_master_key(my_email)
            encrypted_blob = encrypt_master_key_for_recipient(my_master_key, peer_public_key)
            encrypted_master_key_hex = encrypted_blob.hex()
            print(f"  Encrypted master key for {peer_email}")

            # Also include our public key so they can encrypt for us
            my_public_key = load_public_key(my_email)
            my_public_key_hex = my_public_key.hex()
        except FileNotFoundError:
            print("  Warning: No encryption keys found. Run `claudeconnect init` to generate keys.")
        except Exception as e:
            print(f"  Warning: Could not encrypt master key: {e}")

    # Step 3: Update local authz to grant them appropriate access
    context_dir = Path(config.context_dir)
    authz_path = context_dir / "authz"

    if not authz_path.exists():
        print("Error: authz file not found. Run `claudeconnect init` first.")
        sys.exit(1)

    add_friend_to_authz(authz_path, my_email, peer_email)

    # Step 4: Create with-{peer} directory for them to write conversations to
    peer_email_sanitized = email_to_repo_name(peer_email)
    with_peer_dir = context_dir / "claudeconnect" / f"with-{peer_email_sanitized}"
    with_peer_dir.mkdir(parents=True, exist_ok=True)
    # Create a README so the directory syncs (empty dirs don't sync)
    readme_path = with_peer_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(f"This directory contains conversations between {peer_email} and {my_email}.\n")
    print(f"  Created {with_peer_dir.relative_to(context_dir)}/")

    # Step 5: Upload authz to server via HTTP
    print("  Uploading authz changes...")
    authz_content = authz_path.read_text()
    if not upload_authz_http(tokens.id_token, my_email, authz_content):
        print("Failed to upload authz")
        sys.exit(1)

    # Step 6: Send friend request via API
    print("  Sending friend request...")
    try:
        request_data = {"to": peer_email}
        if my_public_key_hex:
            request_data["public_key"] = my_public_key_hex
        if encrypted_master_key_hex:
            request_data["encrypted_master_key"] = encrypted_master_key_hex

        response = httpx.post(
            f"{SERVER_URL}/api/friend-request",
            headers={"Authorization": f"Bearer {tokens.id_token}"},
            json=request_data,
            timeout=30,
        )

        if response.status_code == 200:
            # Sync to upload the with-{peer} directory to server
            print("  Syncing files to server...")
            sync_http(context_dir, my_email, tokens.id_token)

            print(f"\n✓ Friend request sent to {peer_email}")
            print(f"  They will see your request in their claudeconnect/with-claudeconnect-io/ folder.")
            if encrypted_master_key_hex:
                print(f"  Your encrypted master key was included - they can read your files immediately after accepting.")
            else:
                print(f"  Once they accept, they can send you conversations.")
        elif response.status_code == 404:
            print(f"\n✗ User {peer_email} not found on ClaudeConnect")
            sys.exit(1)
        else:
            try:
                data = response.json()
                # FastAPI uses 'detail', others might use 'error'
                error_msg = data.get('detail') or data.get('error') or 'Unknown error'
            except Exception:
                error_msg = response.text
            print(f"\n✗ Failed to send request (HTTP {response.status_code}): {error_msg}")
            sys.exit(1)

    except Exception as e:
        print(f"\n✗ Error sending request: {e}")
        sys.exit(1)


@cli.command("accept-friend")
@click.argument("peer_email")
def accept_friend(peer_email: str):
    """Accept a pending friend request.

    This command:
    1. Extracts and decrypts friend's master key (so you can read their files)
    2. Updates your authz to grant them read access
    3. Sends a reciprocal friend request with YOUR encrypted master key (so they can read your files)
    4. Uploads authz changes to the server
    5. Deletes the friend request file
    """
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    if not config.context_dir:
        print("No context directory configured. Run `claudeconnect init` first.")
        sys.exit(1)

    peer_email = peer_email.strip().lower()
    my_email = tokens.email

    if peer_email == my_email:
        print("Cannot accept friend request from yourself.")
        sys.exit(1)

    context_dir = Path(config.context_dir)

    # Check if friend request exists in with-claudeconnect-io/ (per system2.md)
    system_messages_dir = context_dir / "claudeconnect" / "with-claudeconnect-io"
    peer_email_sanitized = email_to_repo_name(peer_email)
    request_file = system_messages_dir / f"friend-request-{peer_email_sanitized}.md"

    if not request_file.exists():
        print(f"No friend request found from {peer_email}")
        print(f"  Checked: {request_file}")
        print(f"  Run `claudeconnect sync` to pull latest files from server.")
        sys.exit(1)

    print(f"Accepting friend request from {peer_email}...")

    # Step 1: Update local authz to grant them appropriate access
    authz_path = context_dir / "authz"

    if not authz_path.exists():
        print("Error: authz file not found. Run `claudeconnect init` first.")
        sys.exit(1)

    add_friend_to_authz(authz_path, my_email, peer_email)

    # Step 2: Extract keys from friend request
    request_content = request_file.read_text()
    if HAS_ENCRYPTION:
        try:
            import re

            # Extract and save friend's public key
            key_match = re.search(r'\*\*Public-Key\*\*:\s*([a-fA-F0-9]{64})', request_content)
            if key_match:
                peer_public_key_hex = key_match.group(1)
                peer_public_key = bytes.fromhex(peer_public_key_hex)
                save_friend_public_key(peer_email, peer_public_key, my_email)
                fingerprint = get_key_fingerprint(peer_public_key)
                print(f"  Saved friend's public key (fingerprint: {fingerprint})")
            else:
                print("  Note: Friend request did not include public key")

            # Extract and decrypt friend's master key (this lets us read their files!)
            master_key_match = re.search(r'\*\*Encrypted-Master-Key\*\*:\s*([a-fA-F0-9]+)', request_content)
            if master_key_match:
                encrypted_master_key_hex = master_key_match.group(1)
                encrypted_blob = bytes.fromhex(encrypted_master_key_hex)
                # Decrypt with our private key (account-scoped)
                friend_master_key = decrypt_received_master_key(encrypted_blob, my_email)
                save_friend_master_key(peer_email, friend_master_key, my_email)
                print(f"  Decrypted and saved friend's master key - you can now read their files!")
            else:
                print("  Note: Friend request did not include encrypted master key")
                print("  You won't be able to read their encrypted files.")

        except Exception as e:
            print(f"  Warning: Could not process encryption keys: {e}")

    # Step 3: Send reciprocal friend request back to sender (so they get our master key)
    if HAS_ENCRYPTION:
        try:
            # We have peer's public key from step 2, now encrypt our master key for them
            peer_public_key = load_friend_public_key(peer_email, my_email)
            if peer_public_key:
                my_master_key = load_master_key(my_email)
                encrypted_blob = encrypt_master_key_for_recipient(my_master_key, peer_public_key)
                encrypted_master_key_hex = encrypted_blob.hex()

                my_public_key = load_public_key(my_email)
                my_public_key_hex = my_public_key.hex()

                # Send friend request back to peer via API
                print("  Sending reciprocal friend request (so they can read your files)...")
                request_data = {
                    "to": peer_email,
                    "public_key": my_public_key_hex,
                    "encrypted_master_key": encrypted_master_key_hex,
                }
                response = httpx.post(
                    f"{SERVER_URL}/api/friend-request",
                    headers={"Authorization": f"Bearer {tokens.id_token}"},
                    json=request_data,
                    timeout=30,
                )
                if response.status_code == 200:
                    print(f"  Sent your encrypted master key to {peer_email}")
                elif response.status_code == 409:
                    print(f"  Reciprocal request already pending for {peer_email}")
                else:
                    print(f"  Warning: Could not send reciprocal request: {response.text}")
            else:
                print("  Note: Could not send reciprocal request - no public key for peer")
        except Exception as e:
            print(f"  Warning: Could not send reciprocal friend request: {e}")

    # Step 4: Upload authz to server via HTTP
    print("  Uploading authz changes...")
    authz_content = authz_path.read_text()
    if not upload_authz_http(tokens.id_token, my_email, authz_content):
        print("Failed to upload authz")
        sys.exit(1)

    # Step 5: Delete the friend request file locally and from server
    try:
        request_file.unlink()
        print(f"  Removed local friend request file")
    except Exception as e:
        print(f"  Warning: Could not delete local request file: {e}")

    # Delete from server via accept-friend API
    try:
        response = httpx.post(
            f"{API_BASE_URL}/accept-friend",
            headers={"Authorization": f"Bearer {tokens.id_token}"},
            json={"from_email": peer_email},
            timeout=30,
        )
        if response.status_code == 200:
            print(f"  Removed friend request from server")
        elif response.status_code == 404:
            pass  # Already deleted or never existed on server
        else:
            print(f"  Warning: Could not delete server request file: {response.text}")
    except Exception as e:
        print(f"  Warning: Could not delete server request file: {e}")

    print(f"\n✓ Friend request accepted!")
    print(f"  {peer_email} can now read your context and send you conversations.")


@cli.command()
@click.argument("peer_email")
def unfriend(peer_email: str):
    """Remove a friend's access from your authz and sync the change."""
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    if not config.context_dir:
        print("No context directory configured. Run `claudeconnect init` first.")
        sys.exit(1)

    peer_email = peer_email.strip().lower()
    my_email = tokens.email

    if peer_email == my_email:
        print("Cannot unfriend yourself.")
        sys.exit(1)

    print(f"Removing {peer_email} from your friends...")

    authz_path = Path(config.context_dir) / "authz"
    if not authz_path.exists():
        print("Error: authz file not found. Run `claudeconnect init` first.")
        sys.exit(1)

    if not remove_friend_from_authz(authz_path, peer_email):
        print("  No changes needed.")
        return

    print("  Uploading authz changes...")
    authz_content = authz_path.read_text()
    if not upload_authz_http(tokens.id_token, my_email, authz_content):
        print("Failed to upload authz")
        sys.exit(1)

    print(f"\n✓ {peer_email} can no longer access your context.")
    print(f"  Pull their context with: claudeconnect pull {peer_email}")


def auto_accept_reciprocal_requests(
    context_dir: Path,
    my_email: str,
    id_token: str,
    verbose: bool = False,
) -> int:
    """
    Auto-accept friend requests from people we already initiated friendship with.

    A "reciprocal" request is one where:
    - We already sent them a friend request (they're in our authz)
    - They accepted and sent their master key back to us

    For these, we just need to extract their master key - no authz update needed.

    Args:
        context_dir: Path to context directory
        my_email: Our email address
        id_token: JWT for API calls
        verbose: If True, print status messages

    Returns:
        Number of requests auto-accepted
    """
    import re

    system_messages_dir = context_dir / "claudeconnect" / "with-claudeconnect-io"
    if not system_messages_dir.exists():
        return 0

    # Load authz to check who we've already friended
    authz_path = context_dir / "authz"
    if not authz_path.exists():
        return 0

    authz_content = authz_path.read_text()

    # Find all friend request files
    request_files = list(system_messages_dir.glob("friend-request-*.md"))
    if not request_files:
        return 0

    accepted_count = 0

    for request_file in request_files:
        try:
            request_content = request_file.read_text()

            # Extract sender's email from the request
            from_match = re.search(r'\*\*From\*\*:\s*(\S+@\S+)', request_content)
            if not from_match:
                continue

            peer_email = from_match.group(1).strip().lower()

            # Check if this person is already in our authz (we initiated to them)
            # They should have read access in [/] section
            peer_has_access = f"{peer_email} = r" in authz_content or f"{peer_email} = rw" in authz_content

            if not peer_has_access:
                # Not a reciprocal request - needs manual acceptance
                continue

            # Check if we already have their master key
            if HAS_ENCRYPTION and has_friend_master_key(peer_email, my_email):
                # Already have their key, just clean up the request file
                pass
            elif HAS_ENCRYPTION:
                # Extract and save their master key
                master_key_match = re.search(r'\*\*Encrypted-Master-Key\*\*:\s*([a-fA-F0-9]+)', request_content)
                if master_key_match:
                    try:
                        encrypted_master_key_hex = master_key_match.group(1)
                        encrypted_blob = bytes.fromhex(encrypted_master_key_hex)
                        friend_master_key = decrypt_received_master_key(encrypted_blob, my_email)
                        save_friend_master_key(peer_email, friend_master_key, my_email)
                        if verbose:
                            print(f"  Auto-accepted reciprocal request from {peer_email} (got their master key)")
                    except Exception as e:
                        if verbose:
                            print(f"  Warning: Could not decrypt master key from {peer_email}: {e}")
                        continue
                else:
                    if verbose:
                        print(f"  Auto-accepted reciprocal request from {peer_email} (no master key included)")

                # Also save their public key if present
                key_match = re.search(r'\*\*Public-Key\*\*:\s*([a-fA-F0-9]{64})', request_content)
                if key_match:
                    try:
                        peer_public_key = bytes.fromhex(key_match.group(1))
                        save_friend_public_key(peer_email, peer_public_key, my_email)
                    except Exception:
                        pass  # Non-fatal

            # Delete the request file locally
            try:
                request_file.unlink()
            except Exception:
                pass

            # Delete from server
            try:
                response = httpx.post(
                    f"{API_BASE_URL}/accept-friend",
                    headers={"Authorization": f"Bearer {id_token}"},
                    json={"from_email": peer_email},
                    timeout=30,
                )
                # Ignore response - best effort cleanup
            except Exception:
                pass

            accepted_count += 1

        except Exception as e:
            if verbose:
                print(f"  Warning: Error processing {request_file.name}: {e}")
            continue

    return accepted_count


@cli.command("reject-friend")
@click.argument("peer_email")
def reject_friend(peer_email: str):
    """Reject a pending friend request.

    This command:
    1. Deletes the friend request file without granting access
    2. Syncs changes to the server
    """
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    if not config.context_dir:
        print("No context directory configured. Run `claudeconnect init` first.")
        sys.exit(1)

    peer_email = peer_email.strip().lower()
    my_email = tokens.email

    context_dir = Path(config.context_dir)

    # Check if friend request exists in with-claudeconnect-io/ (per system2.md)
    system_messages_dir = context_dir / "claudeconnect" / "with-claudeconnect-io"
    # Server writes friend requests with sanitized email in filename
    peer_email_sanitized = email_to_repo_name(peer_email)
    request_file = system_messages_dir / f"friend-request-{peer_email_sanitized}.md"

    if not request_file.exists():
        print(f"No friend request found from {peer_email}")
        print(f"  Checked: {request_file}")
        sys.exit(1)

    print(f"Rejecting friend request from {peer_email}...")

    # Delete the friend request file locally
    try:
        request_file.unlink()
        print(f"  Removed local friend request file")
    except Exception as e:
        print(f"  Error: Could not delete request file: {e}")
        sys.exit(1)

    # Delete from server via files API (user has write access to their own folder)
    try:
        rel_path = f"claudeconnect/with-claudeconnect-io/friend-request-{peer_email_sanitized}.md"
        response = httpx.delete(
            f"{API_BASE_URL}/files/{my_email}/{rel_path}",
            headers={"Authorization": f"Bearer {tokens.id_token}"},
            timeout=30,
        )
        if response.status_code == 200:
            print(f"  Removed friend request from server")
        elif response.status_code == 404:
            pass  # Already deleted, that's fine
        else:
            print(f"  Warning: Could not delete server request file: {response.text}")
    except Exception as e:
        print(f"  Warning: Could not delete server request file: {e}")

    print(f"\n✓ Friend request rejected.")


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
