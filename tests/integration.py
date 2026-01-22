#!/usr/bin/env python3
"""
ClaudeConnect Integration Test

Tests the full flow:
1. Server cleanup (purge all repos)
2. Client cleanup (~/.claude-connect)
3. Two-account login/init
4. File creation and sync
5. Friend request flow
6. Session between accounts
7. Transcript verification
8. Context pull verification

Requires two Google accounts with manual OAuth at each login.

Run with: pytest tests/integration.py -s
(the -s flag is required for interactive prompts)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional

import pytest

# Server config
SERVER = "v2.claudeconnect.io"
SSH_KEY = Path.home() / ".ssh" / "calco_key.pem"
CC_CONFIG_DIR = Path.home() / ".claude-connect"
PEERS_DIR = CC_CONFIG_DIR / "peers"


class Colors:
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    RESET = "\033[0m"


def log(msg: str):
    print(f"{Colors.GREEN}[TEST]{Colors.RESET} {msg}")


def warn(msg: str):
    print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")


def error(msg: str):
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")


def prompt(msg: str):
    print(f"{Colors.YELLOW}[ACTION REQUIRED]{Colors.RESET} {msg}")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=input_text)
    if check and result.returncode != 0:
        error(f"Command failed: {' '.join(cmd)}")
        error(f"stdout: {result.stdout}")
        error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def ssh_server(cmd: str) -> str:
    """Run command on server via SSH."""
    result = run(["ssh", "-i", str(SSH_KEY), f"ubuntu@{SERVER}", cmd])
    return result.stdout.strip()


def claudeconnect(*args, cwd: Path | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    """Run claudeconnect CLI command."""
    return run(["claudeconnect", *args], cwd=cwd, input_text=input_text)


def get_current_email() -> str:
    """Get email from current tokens."""
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    with open(tokens_file) as f:
        return json.load(f)["email"]


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name format."""
    return email.replace("@", "-").replace(".", "-").lower()


def wait_for_user(msg: str):
    """Prompt user and wait for Enter."""
    prompt(msg)
    input("Press Enter to continue...")




def clean_server():
    """Remove test account repos on server."""
    log("Cleaning test account repos...")
    # Only remove repos for test accounts, not all repos
    test_repos = [
        "brandonduderstadt-gmail-com",
        "thisismysignupacct-gmail-com",
    ]
    for repo in test_repos:
        ssh_server(f"sudo rm -rf /var/svn/repos/{repo}")
    log("Test account repos cleaned.")


def clean_client():
    """Remove local ~/.claude-connect."""
    log("Removing local ~/.claude-connect...")
    if CC_CONFIG_DIR.exists():
        shutil.rmtree(CC_CONFIG_DIR)
    log("Local config removed.")


def create_temp_dirs() -> tuple[Path, Path]:
    """Create temp directories for both accounts."""
    log("Creating temp directories...")
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_account1_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_account2_"))
    log(f"Created {temp1} (Account 1)")
    log(f"Created {temp2} (Account 2)")
    return temp1, temp2


def login(account_name: str, temp_dir: Path):
    """Login to an account."""
    log(f"Logging in as {account_name}...")
    os.chdir(temp_dir)
    wait_for_user(f"Please login with {account_name}")
    claudeconnect("login", cwd=temp_dir)


def init_account(account_name: str, temp_dir: Path) -> str:
    """Initialize an account and return email."""
    log(f"Initializing {account_name}...")
    wait_for_user(f"{account_name} logged in. Ready to init.")
    # Pass "y" to confirm switching context directory if prompted
    claudeconnect("init", cwd=temp_dir, input_text="y\n")
    email = get_current_email()
    log(f"{account_name} initialized: {email}")
    return email


def verify_init_structure(account_name: str, temp_dir: Path) -> bool:
    """
    Verify correct directory structure after initialization.

    Expected structure:
    - authz file with public key on first line
    - claudeconnect/ folder
    - claudeconnect/with-claudeconnect-io/ subfolder

    Returns:
        True if structure is correct, raises AssertionError otherwise.
    """
    log(f"Verifying directory structure for {account_name}...")

    # Check authz file exists and has public key comment at top
    authz_file = temp_dir / "authz"
    assert authz_file.exists(), f"authz file not found in {temp_dir}"

    authz_content = authz_file.read_text()
    first_line = authz_content.split("\n")[0]
    assert first_line.startswith("#"), f"authz first line should be a comment, got: {first_line[:50]}..."
    assert "Public-Key:" in first_line or "ssh-" in first_line, f"authz first line should contain public key, got: {first_line[:50]}..."
    log(f"  ✓ authz file exists with public key comment")

    # Check claudeconnect folder exists
    claudeconnect_dir = temp_dir / "claudeconnect"
    assert claudeconnect_dir.exists(), f"claudeconnect/ folder not found in {temp_dir}"
    assert claudeconnect_dir.is_dir(), f"claudeconnect is not a directory"
    log(f"  ✓ claudeconnect/ folder exists")

    # Check with-claudeconnect-io subfolder exists
    with_cc_dir = claudeconnect_dir / "with-claudeconnect-io"
    assert with_cc_dir.exists(), f"claudeconnect/with-claudeconnect-io/ not found in {temp_dir}"
    assert with_cc_dir.is_dir(), f"with-claudeconnect-io is not a directory"
    log(f"  ✓ claudeconnect/with-claudeconnect-io/ subfolder exists")

    log(f"Directory structure verified for {account_name}")
    return True


def create_poetry_file(temp_dir: Path):
    """Create poetry.md in context."""
    log("Creating poetry.md...")
    poetry_file = temp_dir / "poetry.md"
    poetry_file.write_text("# Poetry Collection\n\nturning and turning in the widening gyre\n")
    log("Created poetry.md")


def sync(account_name: str, temp_dir: Path):
    """Sync an account."""
    log(f"Syncing {account_name}...")
    claudeconnect("sync", cwd=temp_dir)
    log(f"{account_name} synced.")


def send_friend_request(temp_dir: Path, to_email: str):
    """Send friend request."""
    log(f"Sending friend request to {to_email}...")
    claudeconnect("friend", to_email, cwd=temp_dir)
    log(f"Friend request sent.")


def check_friend_request(temp_dir: Path, from_email: str):
    """Check if friend request exists."""
    log("Checking for friend request...")
    # Friend requests are stored in claudeconnect/with-claudeconnect-io/
    friend_requests_dir = temp_dir / "claudeconnect" / "with-claudeconnect-io"

    found = False
    if friend_requests_dir.exists():
        for f in friend_requests_dir.iterdir():
            if f.suffix == ".md" and "friend-request" in f.name:
                log(f"Found friend request: {f.name}")
                print(f.read_text())
                found = True
                break

    if not found:
        warn("Friend request file not found directly, checking directory:")
        if friend_requests_dir.exists():
            for f in friend_requests_dir.iterdir():
                print(f"  {f}")
        else:
            warn("with-claudeconnect-io directory doesn't exist")


def accept_friend_request(temp_dir: Path, from_email: str):
    """Accept friend request."""
    log(f"Accepting friend request from {from_email}...")
    claudeconnect("accept-friend", from_email, cwd=temp_dir)
    log("Friend request accepted.")


def start_session(temp_dir: Path, peer_email: str, topic: str):
    """Start a session with peer (2 turns, output streamed live)."""
    log(f"Starting session about {topic}...")
    # Run session with live output (not captured)
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "2"],
        cwd=temp_dir,
    )


def verify_transcript(account_name: str, temp_dir: Path) -> bool:
    """Verify transcript exists."""
    log(f"Verifying transcript in {account_name}...")
    conv_dir = temp_dir / "claudeconnect" / "conversations"

    if not conv_dir.exists():
        error(f"Conversations directory not found: {conv_dir}")
        return False

    transcripts = list(conv_dir.rglob("*.md"))
    if transcripts:
        log(f"Found {len(transcripts)} transcript(s)")
        for t in transcripts:
            log(f"  {t}")
        log("Preview of first transcript:")
        print(transcripts[0].read_text()[:1000])
        return True
    else:
        error("No transcripts found!")
        return False


def pull_and_verify_poetry(temp_dir: Path, peer_email: str) -> bool:
    """Pull peer's context and verify poetry.md."""
    log(f"Pulling {peer_email}'s context...")
    claudeconnect("pull", peer_email, cwd=temp_dir)

    repo_name = email_to_repo_name(peer_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if peer_poetry.exists():
        content = peer_poetry.read_text()
        log("Pulled poetry.md:")
        print(content)
        if "widening gyre" in content:
            log("Content verified - 'widening gyre' found!")
            return True
        else:
            error("Content verification failed")
            return False
    else:
        error(f"Could not find: {peer_poetry}")
        if PEERS_DIR.exists():
            warn("Peers directory contents:")
            for p in PEERS_DIR.iterdir():
                print(f"  {p}")
        return False


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_account1_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_account2_"))
    log(f"Created {temp1} (Account 1)")
    log(f"Created {temp2} (Account 2)")

    yield temp1, temp2

    # Cleanup
    if temp1.exists():
        log(f"Cleaning up {temp1}")
        shutil.rmtree(temp1)
    if temp2.exists():
        log(f"Cleaning up {temp2}")
        shutil.rmtree(temp2)


@pytest.mark.integration
def test_full_flow(temp_dirs):
    """
    Full integration test for ClaudeConnect.

    Run with: pytest tests/integration.py -s -m integration
    """
    temp1, temp2 = temp_dirs

    # Setup
    clean_server()
    clean_client()

    # Account 1 first login
    login("Account 1", temp1)
    account1_email = init_account("Account 1", temp1)
    verify_init_structure("Account 1", temp1)

    # Account 2 setup
    os.chdir(temp2)
    login("Account 2", temp2)
    account2_email = init_account("Account 2", temp2)
    verify_init_structure("Account 2", temp2)
    create_poetry_file(temp2)
    sync("Account 2", temp2)
    send_friend_request(temp2, account1_email)

    # Account 1 receives and accepts
    os.chdir(temp1)
    login("Account 1", temp1)
    init_account("Account 1", temp1)
    sync("Account 1", temp1)
    check_friend_request(temp1, account2_email)
    accept_friend_request(temp1, account2_email)

    # Session
    start_session(temp1, account2_email, "talk about poetry!")
    verify_transcript("Account 1", temp1)
    pull_and_verify_poetry(temp1, account2_email)

    # Account 2 verifies
    os.chdir(temp2)
    login("Account 2", temp2)
    init_account("Account 2", temp2)
    sync("Account 2", temp2)
    success = verify_transcript("Account 2", temp2)

    # Summary
    print()
    log("==========================================")
    log("Integration test complete!")
    log("==========================================")
    log(f"Account 1: {account1_email}")
    log(f"Account 2: {account2_email}")

    assert success, "Transcript did not sync to Account 2"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        login("Account 1", temp1)
        account1_email = init_account("Account 1", temp1)
        verify_init_structure("Account 1", temp1)

        os.chdir(temp2)
        login("Account 2", temp2)
        account2_email = init_account("Account 2", temp2)
        verify_init_structure("Account 2", temp2)
        create_poetry_file(temp2)
        sync("Account 2", temp2)
        send_friend_request(temp2, account1_email)

        os.chdir(temp1)
        login("Account 1", temp1)
        init_account("Account 1", temp1)
        sync("Account 1", temp1)
        check_friend_request(temp1, account2_email)
        accept_friend_request(temp1, account2_email)

        start_session(temp1, account2_email, "poetry")
        verify_transcript("Account 1", temp1)
        pull_and_verify_poetry(temp1, account2_email)

        os.chdir(temp2)
        login("Account 2", temp2)
        init_account("Account 2", temp2)
        sync("Account 2", temp2)
        verify_transcript("Account 2", temp2)

        log("==========================================")
        log("Integration test complete!")
        log("==========================================")
        log(f"Account 1: {account1_email}")
        log(f"Account 2: {account2_email}")

    except Exception as e:
        error(f"Test failed: {e}")
        raise
    finally:
        if temp1 and temp1.exists():
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            shutil.rmtree(temp2)


if __name__ == "__main__":
    main()
