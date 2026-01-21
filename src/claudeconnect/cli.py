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

# ANSI color codes - matching Claude Code's aesthetic
CORAL = '\033[38;5;209m'      # Coral/salmon matching Claude Code
LIME = '\033[38;5;114m'       # Muted lime green for friend Claude
WHITE = '\033[97m'            # Bright white for main text
BOLD = '\033[1m'              # Bold text
DIM = '\033[2m'               # Dim for secondary text
BLACK = '\033[30m'            # Black for eyes
BLACK_BG = '\033[40m'         # Black background for transparency
YELLOW = '\033[38;5;228m'     # Soft yellow for sparkles
RESET = '\033[0m'
CLEAR = '\033[2J\033[H'       # Clear screen and move cursor to top

from .auth import login as do_login, ensure_valid_token, decode_jwt_payload, refresh_token
from .config import (
    get_config, get_tokens, Config, Tokens, is_logged_in, get_email,
    get_test_user_email, get_test_user_credentials, list_test_users,
    TestUserCredentials, TEST_USERS_DIR,
)
from .scanner import scan_directory
from .svn_ops import SvnClient, SvnError, email_to_repo_name, repo_url_for_email
from .sync import SyncLoop, sync_once

# Encryption is optional - only available if cryptography is installed
try:
    from .encryption import (
        is_encryption_available,
        generate_keypair,
        load_public_key,
        get_key_fingerprint,
        save_friend_public_key,
        load_friend_public_key,
    )
    HAS_ENCRYPTION = is_encryption_available()
except ImportError:
    HAS_ENCRYPTION = False


SERVER_URL = "https://v2.claudeconnect.io"


def display_startup_banner(context_dir: Path, email: str, clear_screen: bool = True) -> None:
    """Display ClaudeConnect startup banner with two Claude creatures and status."""
    # Clear screen for clean display (optional)
    if clear_screen:
        print(CLEAR, end='')

    # Two Claude creatures side by side - coral (you) and lime (friend)
    print()
    print(f" {CORAL}▐{BLACK_BG}▛███▜{RESET}{CORAL}▌{RESET} {YELLOW}✦{RESET} {LIME}▐{BLACK_BG}▛███▜{RESET}{LIME}▌{RESET}   {WHITE}{BOLD}Claude Connect{RESET}")
    print(f"{CORAL}▝▜█████▛▘{RESET} {LIME}▝▜█████▛▘{RESET}  {DIM}{email}{RESET}")
    print(f"  {CORAL}▘▘ ▝▝{RESET}     {LIME}▘▘ ▝▝{RESET}")
    print()

    # Check for friend requests and conversations
    friend_requests_dir = context_dir / "claudeconnect" / "friend_requests"
    conversations_dir = context_dir / "claudeconnect" / "conversations"

    # Collect friend request notifications (pending requests + accepted notifications)
    friend_notifications = []  # List of (display_text, is_accepted)

    if friend_requests_dir.exists():
        for f in friend_requests_dir.glob("*.md"):
            try:
                content = f.read_text()
                # Check for pending requests
                if "status: pending" in content:
                    for line in content.split("\n"):
                        if line.startswith("from:"):
                            sender = line.split(":", 1)[1].strip()
                            friend_notifications.append((sender, False))
                            break
            except Exception:
                pass

    # Check conversations for accepted friend requests
    accepted_friends = []
    if conversations_dir.exists():
        for conv_dir in conversations_dir.iterdir():
            if conv_dir.is_dir() and conv_dir.name.startswith("with-"):
                # Look for friend-accepted.md file
                accepted_file = conv_dir / "friend-accepted.md"
                if accepted_file.exists():
                    try:
                        content = accepted_file.read_text()
                        if "friend-request-accepted" in content.lower():
                            # Extract email from **From**: line
                            for line in content.split("\n"):
                                if "**From**:" in line or "From:" in line:
                                    email_part = line.split(":", 1)[1].strip()
                                    # Remove markdown bold if present
                                    email_part = email_part.replace("**", "").strip()
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
    recent_convos = []  # List of (peer_email, topic_preview, mtime)
    if conversations_dir.exists():
        month_ago = datetime.datetime.now().timestamp() - (30 * 24 * 60 * 60)
        for conv_dir in conversations_dir.iterdir():
            if conv_dir.is_dir() and conv_dir.name.startswith("with-"):
                peer_name = conv_dir.name[5:]  # Remove "with-" prefix
                # Convert back to email format (replace - with . and @)
                peer_email = peer_name.replace("-", ".")
                # Fix common email pattern: user.example.com -> user@example.com
                if peer_email.count(".") >= 2:
                    parts = peer_email.rsplit(".", 2)
                    if len(parts) == 3:
                        peer_email = f"{parts[0]}@{parts[1]}.{parts[2]}"

                latest_file = None
                latest_time = 0
                for f in conv_dir.glob("*.md"):
                    # Skip friend-accepted.md files for conversation listing
                    if f.name == "friend-accepted.md":
                        continue
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


def get_svn_token(id_token: str) -> str | None:
    """
    Exchange Google JWT for a short Fernet token for SVN auth.

    For mock mode (CC_MOCK_DIR) and test users (CC_TEST_USER), returns mock/stored token.

    Args:
        id_token: Google OAuth id_token (ignored for mock/test users)

    Returns:
        Fernet token string, or None on failure.
    """
    # Check for mock/dev mode first
    mock_dir = get_mock_dir()
    if mock_dir:
        mock_token_file = mock_dir / ".mock" / "api-svn-token.json"
        if mock_token_file.exists():
            data = json.loads(mock_token_file.read_text())
            return data.get("svn_token", "mock-svn-token")
        return "mock-svn-token"

    # Check for test user mode
    test_user_email = get_test_user_email()
    if test_user_email:
        creds = get_test_user_credentials(test_user_email)
        if creds:
            return creds.svn_token
        return None

    # Normal OAuth flow
    try:
        response = httpx.post(
            f"{SERVER_URL}/api/svn-token",
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30,
        )

        if response.status_code != 200:
            data = response.json()
            print(f"Failed to get SVN token: {data.get('error', 'Unknown error')}")
            return None

        data = response.json()
        return data.get("svn_token")

    except Exception as e:
        print(f"Error getting SVN token: {e}")
        return None


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
            print("Token expired, refreshing...")
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
    (mock_dir / "claudeconnect" / "friend_requests").mkdir(parents=True, exist_ok=True)
    (mock_dir / "claudeconnect" / "conversations" / "with-alice-example-com").mkdir(parents=True, exist_ok=True)
    (mock_dir / "claudeconnect" / "conversations" / "with-bob-example-com").mkdir(parents=True, exist_ok=True)
    (mock_dir / "notes").mkdir(parents=True, exist_ok=True)
    (mock_dir / ".mock" / "config").mkdir(parents=True, exist_ok=True)

    # Sample friend request 1
    (mock_dir / "claudeconnect" / "friend_requests" / "carol@example.com.md").write_text(
        """# Friend Request

from: carol@example.com
date: 2026-01-20T10:00:00Z
status: pending

Hi! I'd like to connect our Claude instances to collaborate on projects.
"""
    )

    # Sample friend request 2
    (mock_dir / "claudeconnect" / "friend_requests" / "david@example.com.md").write_text(
        """# Friend Request

from: david@example.com
date: 2026-01-19T15:30:00Z
status: pending

Hey, let's share context!
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

[/claudeconnect/friend_requests]
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
    (mock_dir / ".mock" / "api-svn-token.json").write_text(
        '{"svn_token": "mock-svn-token-for-dev"}'
    )

    (mock_dir / ".mock" / "api-ensure-repo.json").write_text(
        f'{{"repo": "dev-example-com", "url": "file://{mock_dir}", "email": "dev@example.com", "created": false}}'
    )

    (mock_dir / ".mock" / "config" / "tokens.json").write_text(
        '{"id_token": "mock-id-token", "refresh_token": "mock-refresh", "email": "dev@example.com"}'
    )

    return True


def generate_authz_content(email: str, private_files: list[str] | None = None) -> str:
    """
    Generate initial authz file content for a new user.

    Per system2.md, the authz structure is:
    - [/] - owner has rw
    - [/claudeconnect/with-claudeconnect-io] - owner only (server uses admin bypass for writes)
    - [/claudeconnect/with-{owner-email}] - owner rw, friends get rw when added

    Args:
        email: User's email (SVN username)
        private_files: List of file paths (relative to repo root) to make private

    Returns:
        authz file content string
    """
    email_repo_name = email_to_repo_name(email)
    lines = [
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
    ]

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


def verify_init_structure(context_dir: Path) -> list[str]:
    """
    Verify that init created all expected directories and files per system2.md.

    Expected structure:
        context_dir/
        ├── .svn/                           # SVN working copy
        ├── authz                           # Access control file
        └── claudeconnect/
            └── with-claudeconnect-io/      # System messages folder

    Args:
        context_dir: The context directory to verify

    Returns:
        List of error messages for missing/invalid components.
        Empty list if everything is correct.
    """
    errors = []

    # Check SVN working copy
    svn_dir = context_dir / ".svn"
    if not svn_dir.is_dir():
        errors.append(".svn directory missing - not a valid SVN working copy")

    # Check claudeconnect directory structure
    cc_dir = context_dir / "claudeconnect"
    if not cc_dir.is_dir():
        errors.append("claudeconnect/ directory missing")
    else:
        # with-claudeconnect-io/ is the system messages folder (per system2.md)
        system_dir = cc_dir / "with-claudeconnect-io"
        if not system_dir.is_dir():
            errors.append("claudeconnect/with-claudeconnect-io/ directory missing")

    # Check authz file
    authz_file = context_dir / "authz"
    if not authz_file.is_file():
        errors.append("authz file missing")

    # Check skill installation
    skill_file = Path.home() / ".claude" / "skills" / "claudeconnect" / "SKILL.md"
    if not skill_file.is_file():
        errors.append("SKILL.md not installed at ~/.claude/skills/claudeconnect/")

    return errors


def ensure_authz_exists(
    context_dir: Path,
    svn: "SvnClient",
    email: str,
    private_files: list[str] | None = None,
) -> None:
    """
    Ensure authz file and claudeconnect directories exist per system2.md.

    Creates:
    - authz file with proper permissions
    - claudeconnect/with-claudeconnect-io/ directory (system messages folder)

    Note: Conversation directories (claudeconnect/with-{email}/) are created
    on-demand when sessions are started, not during init.

    Args:
        context_dir: The context directory
        svn: SVN client instance
        email: User's email
        private_files: Optional list of file paths to make private
    """
    authz_path = context_dir / "authz"
    files_to_add = []
    needs_commit = False

    # Ensure claudeconnect directory structure exists per system2.md
    cc_dir = context_dir / "claudeconnect"
    system_messages_dir = cc_dir / "with-claudeconnect-io"

    # Create with-claudeconnect-io/ for system messages (friend requests, notifications)
    # Conversation dirs (with-{email}/) are created on-demand
    if not system_messages_dir.exists():
        system_messages_dir.mkdir(parents=True, exist_ok=True)
        # Add .keep file so SVN tracks the empty directory
        keep_file = system_messages_dir / ".keep"
        keep_file.write_text("")
        files_to_add.append(keep_file)
        needs_commit = True

    if authz_path.exists():
        # If authz exists but we have new private files, update it
        if private_files:
            update_authz_with_private_files(authz_path, email, private_files)
    else:
        print("  Creating authz file...")
        authz_content = generate_authz_content(email, private_files)
        authz_path.write_text(authz_content)
        files_to_add.append(authz_path)
        needs_commit = True

    if needs_commit and files_to_add:
        try:
            for file_path in files_to_add:
                rel_path = file_path.relative_to(context_dir)
                svn.add(rel_path, parents=True)
            svn.commit("Initialize authz and claudeconnect directories")
            print("  Created authz and claudeconnect directories")
        except Exception as e:
            print(f"  Warning: Could not commit: {e}")


def update_authz_with_private_files(authz_path: Path, email: str, private_files: list[str]) -> None:
    """
    Update existing authz file to add private file sections.

    Args:
        authz_path: Path to authz file
        email: User's email
        private_files: List of file paths to make private
    """
    content = authz_path.read_text()

    # Check which files are already marked private
    new_private = []
    for file_path in private_files:
        if not file_path.startswith("/"):
            file_path = "/" + file_path
        if f"[{file_path}]" not in content:
            new_private.append(file_path)

    if not new_private:
        return  # All files already private

    # Append new private sections
    lines = ["\n# Private files (contain sensitive information)"]
    for file_path in sorted(set(new_private)):
        lines.append(f"[{file_path}]")
        lines.append(f"{email} = rw")

    authz_path.write_text(content.rstrip() + "\n" + "\n".join(lines) + "\n")
    print(f"  Updated authz with {len(new_private)} private file(s)")


def init_context_dir(context_dir: Path, repo_url: str, svn_token: str, email: str) -> bool:
    """
    Initialize a context directory with SVN checkout.

    Args:
        context_dir: The directory to initialize
        repo_url: SVN repository URL
        svn_token: Fernet token for SVN auth
        email: User email (SVN username)

    Returns:
        True if successful.
    """
    svn = SvnClient(context_dir, repo_url, svn_token, email)

    # Check if already a working copy
    if svn.is_working_copy():
        info = svn.info()
        if info and info.get("url") == repo_url:
            print(f"  Already initialized (revision {info['revision']})")
            # Still run migration/ensure for existing repos
            ensure_authz_exists(context_dir, svn, email)
            return True
        else:
            print(f"  Error: Directory is an SVN working copy for different repo")
            print(f"  Expected: {repo_url}")
            print(f"  Got: {info.get('url')}")
            return False

    # Check if directory has markdown files to import
    md_files = list(context_dir.glob("**/*.md"))
    private_files: list[str] = []  # Files to mark private in authz

    if md_files:
        print(f"  Found {len(md_files)} markdown files to sync")

        # Scan for sensitive information before syncing
        print("  Scanning for sensitive information...")
        report = scan_directory(context_dir, markdown_only=True)

        if report.has_issues:
            # Collect unique files with sensitive content
            sensitive_files = set()
            for match in report.matches:
                try:
                    rel_path = match.file_path.relative_to(context_dir)
                    sensitive_files.add(str(rel_path))
                except ValueError:
                    sensitive_files.add(str(match.file_path))

            private_files = list(sensitive_files)

            # Inform user - files will be auto-privatized, not blocked
            print(f"\n  Found sensitive content in {len(private_files)} file(s)")
            print(f"  These files will be marked PRIVATE (only you can see them):\n")
            for f in sorted(private_files):
                print(f"    - {f}")
            print()
            print(report.format_report(context_dir))
            print("  Files with sensitive content are automatically private.")
            print("  Friends will not be able to see these files.")
            print("  You can change this later by editing your authz file.\n")
        else:
            print("  No sensitive information detected")

    # Need to handle this carefully:
    # 1. If directory is empty, just checkout
    # 2. If directory has files, we need to:
    #    a. Checkout to a temp location
    #    b. Move .svn to the context dir
    #    c. Add existing files

    if not any(context_dir.iterdir()):
        # Empty directory - simple checkout
        try:
            svn.checkout()
            print("  Checked out empty repository")
            ensure_authz_exists(context_dir, svn, email, private_files)
            return True
        except SvnError as e:
            print(f"  Checkout failed: {e}")
            return False
    else:
        # Directory has files - need to overlay SVN
        print("  Initializing with existing files...")

        # Checkout to temp location (use /tmp to avoid permission issues)
        import tempfile
        temp_dir = Path(tempfile.mkdtemp(prefix="claudeconnect_init_"))
        temp_svn = SvnClient(temp_dir, repo_url, svn_token, email)

        try:
            temp_svn.checkout()
        except SvnError as e:
            print(f"  Checkout failed: {e}")
            return False

        # Move .svn folder to context dir
        svn_folder = temp_dir / ".svn"
        target_svn = context_dir / ".svn"

        if target_svn.exists():
            print("  Error: .svn folder already exists")
            return False

        svn_folder.rename(target_svn)

        # Clean up temp dir
        shutil.rmtree(temp_dir)

        # Now add all markdown files
        added = svn.add_all_markdown()
        print(f"  Added {len(added)} markdown files")

        # Set ignore patterns for non-markdown
        svn.set_ignore([
            "*.py",
            "*.json",
            "*.yaml",
            "*.yml",
            "*.txt",
            "*.log",
            "*.sqlite",
            "*.db",
            "__pycache__",
            ".git",
            ".DS_Store",
            "node_modules",
            "venv",
            ".venv",
        ])

        # Initial commit
        try:
            rev = svn.commit("Initial sync from claudeconnect")
            if rev:
                print(f"  Committed initial sync (revision {rev})")
            ensure_authz_exists(context_dir, svn, email, private_files)
            return True
        except SvnError as e:
            print(f"  Initial commit failed: {e}")
            return False


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

        # Check if it's a valid working copy
        svn_token = get_svn_token(tokens.id_token)
        if svn_token:
            svn = SvnClient(
                Path(config.context_dir),
                repo_url_for_email(tokens.email),
                svn_token,
            )
            info = svn.info()
            if info:
                print(f"SVN revision: {info['revision']}")
            else:
                print("SVN status: Not initialized")
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
def start():
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
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    cwd = Path.cwd()

    # Determine context directory
    if config.context_dir:
        context_dir = Path(config.context_dir)
        if cwd != context_dir and not cwd.is_relative_to(context_dir):
            print(f"Your context directory is: {context_dir}")
            print(f"Current directory: {cwd}")
            print(f"\nRun `claudeconnect` from your context directory.")
            print(f"Or use `claudeconnect init` here to switch.")
            sys.exit(1)
    else:
        # First time - use current directory
        context_dir = cwd

    # Ensure repo exists on server
    print(f"Connecting as {tokens.email}...")
    try:
        repo_info = ensure_repo(tokens.id_token)
        # Always compute repo_url locally for consistency
        repo_url = repo_url_for_email(tokens.email)

        if repo_info.get("created"):
            print(f"  Created new repo: {repo_info['repo']}")
        else:
            print(f"  Using repo: {repo_info['repo']}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Get SVN token (Fernet)
    print("Getting SVN credentials...")
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    # Initialize context directory if needed
    # Username is email, password is the Fernet token
    svn = SvnClient(context_dir, repo_url, svn_token, tokens.email)
    if not svn.is_working_copy():
        print(f"\nInitializing context directory: {context_dir}")
        if not init_context_dir(context_dir, repo_url, svn_token, tokens.email):
            sys.exit(1)

        # Save context dir to config
        config.context_dir = str(context_dir)
        config.save()

        # Install skill for Claude Code
        if install_skill():
            print("  Installed claudeconnect skill")

    # Initial sync
    print("\nSyncing...")
    sync_once(context_dir, repo_url, svn_token, tokens.email)

    # Display startup banner with friend requests and conversations
    display_startup_banner(context_dir, tokens.email)

    # Start sync loop and Claude
    print("Starting Claude Code with sync enabled...")
    print(f"{DIM}(Sync runs every 30 seconds in background){RESET}\n")

    # Run async main
    asyncio.run(run_with_sync(context_dir, repo_url, svn_token, tokens.email))


async def run_with_sync(
    context_dir: Path,
    repo_url: str,
    token: str,
    email: str,
):
    """Run Claude Code with background sync loop."""
    # Start sync loop
    sync_loop = SyncLoop(context_dir, repo_url, token, email, interval=30)
    await sync_loop.start()

    try:
        # Run Claude Code
        # Use subprocess to run claude, streaming output
        process = await asyncio.create_subprocess_exec(
            "claude",
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
        await sync_loop.stop()
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

    # Ensure repo exists
    print(f"Connecting as {tokens.email}...")
    try:
        repo_info = ensure_repo(tokens.id_token)
        # Always compute repo_url locally for consistency
        repo_url = repo_url_for_email(tokens.email)
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Get SVN token
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    # Set up encryption if requested
    if encrypt:
        print("  Setting up encryption...")
        try:
            _, public_bytes = generate_keypair()
            fingerprint = get_key_fingerprint(public_bytes)
            print(f"  Generated keypair (fingerprint: {fingerprint})")
        except FileExistsError:
            # Keys already exist, load them
            public_bytes = load_public_key()
            fingerprint = get_key_fingerprint(public_bytes)
            print(f"  Using existing keypair (fingerprint: {fingerprint})")
        except Exception as e:
            print(f"  Error generating keypair: {e}")
            print("  Continuing without encryption...")
            encrypt = False

    # Initialize
    print(f"\nInitializing: {cwd}")
    if init_context_dir(cwd, repo_url, svn_token, tokens.email):
        config.context_dir = str(cwd)
        config.encryption_enabled = encrypt
        config.save()

        # Install skill for Claude Code
        if install_skill():
            print("  Installed claudeconnect skill")
        else:
            print("  Warning: Could not install claudeconnect skill")
            print("    You may need to manually copy SKILL.md to ~/.claude/skills/claudeconnect/")

        # Verify directory structure was created correctly
        verification_errors = verify_init_structure(cwd)
        if verification_errors:
            print("\n⚠ Warning: Some components were not set up correctly:")
            for error in verification_errors:
                print(f"    - {error}")
            print("  You may need to run `claudeconnect init` again or create these manually.")

        print("\n✓ Context directory initialized")
        if encrypt:
            print("  Encryption: ENABLED (zero-trust)")
            print(f"  Key fingerprint: {fingerprint}")
            print("  Your private key is stored at ~/.claude-connect/keys/private.key")
        print(f"  Run `claudeconnect` to start Claude with sync.")
    else:
        sys.exit(1)


@cli.command()
def sync():
    """Manually trigger a sync."""
    tokens = get_valid_token()
    config = get_config()

    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    if not config.context_dir:
        print("No context directory configured.")
        sys.exit(1)

    # Get SVN token
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    context_dir = Path(config.context_dir)
    repo_url = repo_url_for_email(tokens.email)

    print("Syncing...")
    if sync_once(context_dir, repo_url, svn_token, tokens.email):
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
    from .session import pull_peer_context

    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    print(f"Pulling {peer_email}'s context...")
    peer_dir = pull_peer_context(peer_email, svn_token, tokens.email)

    if peer_dir:
        print(f"\n✓ Context pulled to: {peer_dir}")
    else:
        print(f"\n✗ Failed to pull context")
        sys.exit(1)


def add_friend_to_authz(authz_path: Path, my_email: str, peer_email: str) -> bool:
    """
    Add a friend to the authz file with appropriate permissions.

    Grants:
    - Read access to [/] (can read your context)
    - Write access to [/claudeconnect/with-{my_email}] (can push conversations to you)

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
    my_with_section = f"[/claudeconnect/with-{my_email_repo_name}]"

    # Track which sections we've seen
    current_section = None
    added_to_root = False
    added_to_with = False
    with_section_exists = False

    # Check if friend already has access
    has_root_access = f"{peer_email} = r" in authz_content or f"{peer_email} = rw" in authz_content
    has_with_access = False

    # Check with-{email} section specifically
    in_with_section = False
    for line in lines:
        if line.strip().startswith('['):
            in_with_section = my_with_section in line
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

        # Add write access after owner's rw line in [/claudeconnect/with-{my_email}] section
        if (current_section == my_with_section and
            not added_to_with and
            not has_with_access and
            '= rw' in line and
            my_email in line):
            new_lines.append(f"{peer_email} = rw")
            added_to_with = True
            changes_made = True
            print(f"  Added {peer_email} write access to {my_with_section}")

    # If with-{email} section doesn't exist, add it
    if not with_section_exists:
        new_lines.append("")
        new_lines.append(f"# Friends can write conversations to your with-{my_email_repo_name} folder")
        new_lines.append(my_with_section)
        new_lines.append(f"{my_email} = rw")
        new_lines.append(f"{peer_email} = rw")
        changes_made = True
        print(f"  Created {my_with_section} section")
        print(f"  Added {peer_email} write access to {my_with_section}")
    elif not added_to_with and not has_with_access:
        # Section exists but we didn't find owner's line - append to end of section
        final_lines = []
        in_with = False
        added = False
        for line in new_lines:
            final_lines.append(line)
            if my_with_section in line:
                in_with = True
            elif in_with and not added:
                # Add after first line of section
                final_lines.append(f"{peer_email} = rw")
                added = True
                changes_made = True
                print(f"  Added {peer_email} write access to {my_with_section}")
                in_with = False
        new_lines = final_lines

    if changes_made:
        authz_path.write_text('\n'.join(new_lines))
    else:
        print(f"  {peer_email} already has access in your authz")

    return changes_made


@cli.command()
@click.argument("peer_email")
@click.option("--message", "-m", default="Hi! I'd like to connect our Claude instances.", help="Message to include")
def friend(peer_email: str, message: str):
    """Send a friend request to another user.

    This command:
    1. Updates your authz to grant them read access + conversation write access
    2. Sends a friend request to their repo
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

    # Step 1: Update local authz to grant them appropriate access
    context_dir = Path(config.context_dir)
    authz_path = context_dir / "authz"

    if not authz_path.exists():
        print("Error: authz file not found. Run `claudeconnect init` first.")
        sys.exit(1)

    add_friend_to_authz(authz_path, my_email, peer_email)

    # Step 2: Get our public key if encryption is enabled (to include in request)
    config = get_config()
    my_public_key_hex = None
    if config.encryption_enabled and HAS_ENCRYPTION:
        try:
            my_public_key = load_public_key()
            my_public_key_hex = my_public_key.hex()
            print(f"  Will share your public key with friend request")
        except FileNotFoundError:
            print("  Warning: No encryption keys found. Run `claudeconnect init --encrypt` first.")

    # Step 3: Sync to commit authz changes
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    repo_url = repo_url_for_email(my_email)
    print("  Syncing authz changes...")
    sync_once(context_dir, repo_url, svn_token, my_email)

    # Step 4: Send friend request via API (include public key if available)
    print("  Sending friend request...")
    try:
        request_data = {"to": peer_email, "message": message}
        if my_public_key_hex:
            request_data["public_key"] = my_public_key_hex

        response = httpx.post(
            f"{SERVER_URL}/api/friend-request",
            headers={"Authorization": f"Bearer {tokens.id_token}"},
            json=request_data,
            timeout=30,
        )

        if response.status_code == 200:
            print(f"\n✓ Friend request sent to {peer_email}")
            print(f"  They will see your request in their claudeconnect/with-claudeconnect-io/ folder.")
            print(f"  Once they accept, they can send you conversations.")
        elif response.status_code == 404:
            print(f"\n✗ User {peer_email} not found on ClaudeConnect")
            sys.exit(1)
        else:
            data = response.json()
            print(f"\n✗ Failed to send request: {data.get('error', 'Unknown error')}")
            sys.exit(1)

    except Exception as e:
        print(f"\n✗ Error sending request: {e}")
        sys.exit(1)


@cli.command("accept-friend")
@click.argument("peer_email")
def accept_friend(peer_email: str):
    """Accept a pending friend request.

    This command:
    1. Updates your authz to grant them read access + conversation write access
    2. Deletes the friend request file
    3. Syncs changes to the server so they can access your context
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
    # Server writes friend requests as markdown files
    request_file = system_messages_dir / f"friend-request-{peer_email}.md"

    if not request_file.exists():
        print(f"No friend request found from {peer_email}")
        print(f"  Checked: {request_file}")
        sys.exit(1)

    print(f"Accepting friend request from {peer_email}...")

    # Step 1: Update local authz to grant them appropriate access
    authz_path = context_dir / "authz"

    if not authz_path.exists():
        print("Error: authz file not found. Run `claudeconnect init` first.")
        sys.exit(1)

    add_friend_to_authz(authz_path, my_email, peer_email)

    # Step 2: Extract and save friend's public key if present in request
    config = get_config()
    if config.encryption_enabled and HAS_ENCRYPTION:
        try:
            # Read friend request to extract public key
            request_content = request_file.read_text()
            # Look for public key in the request (format: Public-Key: <hex>)
            import re
            key_match = re.search(r'Public-Key:\s*([a-fA-F0-9]{64})', request_content)
            if key_match:
                peer_public_key_hex = key_match.group(1)
                peer_public_key = bytes.fromhex(peer_public_key_hex)
                save_friend_public_key(peer_email, peer_public_key)
                fingerprint = get_key_fingerprint(peer_public_key)
                print(f"  Saved friend's public key (fingerprint: {fingerprint})")
            else:
                print("  Note: Friend request did not include public key (encryption won't work)")
        except Exception as e:
            print(f"  Warning: Could not process friend's public key: {e}")

    # Step 4: Delete the friend request file
    try:
        request_file.unlink()
        print(f"  Removed friend request file")
    except Exception as e:
        print(f"  Warning: Could not delete request file: {e}")

    # Step 5: Sync to commit authz changes and request deletion
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    repo_url = repo_url_for_email(my_email)
    print("  Syncing changes to server...")
    if not sync_once(context_dir, repo_url, svn_token, my_email):
        print("  Warning: Sync may have failed. Run `claudeconnect sync` to retry.")

    print(f"\n✓ Friend request accepted!")
    print(f"  {peer_email} can now read your context and send you conversations.")
    print(f"  Pull their context with: claudeconnect pull {peer_email}")


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
    # Server writes friend requests as markdown files
    request_file = system_messages_dir / f"friend-request-{peer_email}.md"

    if not request_file.exists():
        print(f"No friend request found from {peer_email}")
        print(f"  Checked: {request_file}")
        sys.exit(1)

    print(f"Rejecting friend request from {peer_email}...")

    # Delete the friend request file
    try:
        request_file.unlink()
        print(f"  Removed friend request file")
    except Exception as e:
        print(f"  Error: Could not delete request file: {e}")
        sys.exit(1)

    # Sync to commit the deletion
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    repo_url = repo_url_for_email(my_email)
    print("  Syncing changes to server...")
    if not sync_once(context_dir, repo_url, svn_token, my_email):
        print("  Warning: Sync may have failed. Run `claudeconnect sync` to retry.")

    print(f"\n✓ Friend request rejected.")


# =============================================================================
# Test User Commands
# =============================================================================

def parse_ttl(ttl: str) -> int:
    """
    Parse TTL string to hours.

    Examples: "1h" -> 1, "24h" -> 24, "7d" -> 168, "2d" -> 48
    """
    ttl = ttl.lower().strip()
    if ttl.endswith("h"):
        return int(ttl[:-1])
    elif ttl.endswith("d"):
        return int(ttl[:-1]) * 24
    else:
        # Assume hours if no suffix
        return int(ttl)


@cli.group("test-user")
def test_user():
    """Manage ephemeral test users for development and testing."""
    pass


@test_user.command("create")
@click.option("--ttl", default="24h", help="Time to live (e.g., 1h, 24h, 7d)")
def test_user_create(ttl: str):
    """Create an ephemeral test user.

    Requires admin access (must be logged in as an admin email).
    """
    from datetime import datetime

    # Need to be logged in as admin
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in. Run `claudeconnect login` first.")
        sys.exit(1)

    ttl_hours = parse_ttl(ttl)

    print(f"Creating test user (TTL: {ttl_hours}h)...")

    try:
        response = httpx.post(
            f"{SERVER_URL}/api/test-user/create",
            headers={"Authorization": f"Bearer {tokens.id_token}"},
            json={"ttl_hours": ttl_hours},
            timeout=30,
        )

        if response.status_code == 403:
            print("✗ Admin access required to create test users.")
            sys.exit(1)

        if response.status_code != 200:
            data = response.json()
            print(f"✗ Failed to create test user: {data.get('error', 'Unknown error')}")
            sys.exit(1)

        data = response.json()

        # Always compute repo_url locally for consistency
        computed_repo_url = repo_url_for_email(data["email"])

        # Save locally
        creds = TestUserCredentials(
            email=data["email"],
            svn_token=data["svn_token"],
            repo_url=computed_repo_url,
            expires_at=data["expires_at"],
        )
        creds.save()

        expires_str = datetime.fromtimestamp(data["expires_at"]).strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n✓ Created test user: {data['email']}")
        print(f"  Repo: {computed_repo_url}")
        print(f"  Expires: {expires_str}")
        print(f"\n  To use this test user:")
        print(f"    CC_TEST_USER={data['email']} claudeconnect init")

    except Exception as e:
        print(f"✗ Error creating test user: {e}")
        sys.exit(1)


@test_user.command("list")
def test_user_list():
    """List local test users."""
    from datetime import datetime

    users = list_test_users()

    if not users:
        print("No test users found locally.")
        print("Create one with: claudeconnect test-user create")
        return

    print(f"Found {len(users)} test user(s):\n")

    now = int(time.time())
    for email in sorted(users):
        creds = get_test_user_credentials(email)
        if creds:
            expired = creds.expires_at < now
            expires_str = datetime.fromtimestamp(creds.expires_at).strftime("%Y-%m-%d %H:%M:%S")
            status = " (EXPIRED)" if expired else ""

            print(f"  {email}{status}")
            print(f"    Repo: {creds.repo_url}")
            print(f"    Expires: {expires_str}")
            if creds.context_dir:
                print(f"    Context: {creds.context_dir}")
            print()


@test_user.command("delete")
@click.argument("email")
@click.option("--keep-local", is_flag=True, help="Keep local working copy")
@click.option("--local-only", is_flag=True, help="Only delete local credentials (don't call server)")
def test_user_delete(email: str, keep_local: bool, local_only: bool):
    """Delete a test user (remote repo + local credentials)."""
    import shutil

    # Check if we have local credentials
    creds = get_test_user_credentials(email)
    if not creds:
        print(f"No local credentials for {email}")
        return

    # Delete server-side unless local-only
    if not local_only:
        tokens = get_valid_token()
        if not tokens:
            print("Not logged in. Use --local-only to just delete local credentials.")
            sys.exit(1)

        print(f"Deleting test user {email}...")

        try:
            response = httpx.post(
                f"{SERVER_URL}/api/test-user/delete",
                headers={"Authorization": f"Bearer {tokens.id_token}"},
                json={"email": email},
                timeout=30,
            )

            if response.status_code == 403:
                print("✗ Admin access required to delete test users.")
                print("  Use --local-only to just delete local credentials.")
                sys.exit(1)

            if response.status_code == 404:
                print("  Server: Test user not found (may already be deleted)")
            elif response.status_code != 200:
                data = response.json()
                print(f"  Server error: {data.get('error', 'Unknown error')}")
            else:
                print("  Deleted server repo")

        except Exception as e:
            print(f"  Server error: {e}")
            print("  Continuing with local deletion...")

    # Delete local context if we have one and not keeping it
    if creds.context_dir and not keep_local:
        ctx_dir = Path(creds.context_dir)
        if ctx_dir.exists():
            if click.confirm(f"  Delete local working copy at {ctx_dir}?"):
                shutil.rmtree(ctx_dir)
                print(f"  Deleted: {ctx_dir}")

    # Delete local credentials
    creds.delete()
    print(f"  Deleted local credentials")
    print(f"\n✓ Deleted test user: {email}")


@test_user.command("delete-all")
@click.confirmation_option(prompt="Delete ALL local test users?")
def test_user_delete_all():
    """Delete all local test users."""
    users = list_test_users()

    if not users:
        print("No test users found locally.")
        return

    tokens = get_valid_token()

    for email in users:
        creds = get_test_user_credentials(email)
        if not creds:
            continue

        print(f"Deleting {email}...")

        # Try server deletion if logged in
        if tokens:
            try:
                response = httpx.post(
                    f"{SERVER_URL}/api/test-user/delete",
                    headers={"Authorization": f"Bearer {tokens.id_token}"},
                    json={"email": email},
                    timeout=30,
                )
                if response.status_code == 200:
                    print("  Deleted server repo")
            except Exception:
                pass

        # Delete local
        creds.delete()
        print("  Deleted local credentials")

    print(f"\n✓ Deleted {len(users)} test user(s)")


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
