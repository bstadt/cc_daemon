#!/usr/bin/env python3
"""
ClaudeConnect File Operations Integration Test

Extends the base integration test to verify file add/sync operations:
1. Full base flow (login, init, friend request, session)
2. Account 2 adds philosophy.md and syncs
3. Account 1 pulls and verifies philosophy.md content

Run with: pytest tests/test_fileops.py -s -m integration
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


def create_philosophy_file(temp_dir: Path):
    """Create philosophy.md in context."""
    log("Creating philosophy.md...")
    philosophy_file = temp_dir / "philosophy.md"
    philosophy_file.write_text(
        "# Philosophy\n\n"
        "Truth, with a capital T, is kind of like God - God isnt relative to anything, "
        "and so there is nothing to be said about it. This is why theologians talk about ineffability.\n"
    )
    log("Created philosophy.md")


def update_poetry_file(temp_dir: Path):
    """Update poetry.md with new content."""
    log("Updating poetry.md...")
    poetry_file = temp_dir / "poetry.md"
    poetry_file.write_text(
        "# Poetry Collection\n\n"
        "come on you primate, describe those visions\n"
        "you had in the wild\n"
        "that led you to crawl\n"
        "from the chaotic animal\n"
        "dimension of things\n"
        "into the springtime of mind.\n"
    )
    log("Updated poetry.md")


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
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
    )


def verify_transcript(account_name: str, temp_dir: Path, peer_email: str) -> bool:
    """Verify transcript exists in with-<peer> directory."""
    log(f"Verifying transcript in {account_name}...")
    peer_dir_name = f"with-{email_to_repo_name(peer_email)}"
    conv_dir = temp_dir / "claudeconnect" / peer_dir_name

    if not conv_dir.exists():
        error(f"Peer directory not found: {conv_dir}")
        return False

    # Look for transcript files (exclude friend-request files)
    transcripts = [f for f in conv_dir.glob("*.md") if "friend-request" not in f.name]
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


def pull_and_verify_philosophy(temp_dir: Path, peer_email: str) -> bool:
    """Pull peer's context and verify philosophy.md."""
    log(f"Pulling {peer_email}'s context for philosophy.md...")
    claudeconnect("pull", peer_email, cwd=temp_dir)

    repo_name = email_to_repo_name(peer_email)
    peer_philosophy = PEERS_DIR / repo_name / "philosophy.md"

    if peer_philosophy.exists():
        content = peer_philosophy.read_text()
        log("Pulled philosophy.md:")
        print(content)
        if "ineffability" in content:
            log("Content verified - 'ineffability' found!")
            return True
        else:
            error("Content verification failed - 'ineffability' not found")
            return False
    else:
        error(f"Could not find: {peer_philosophy}")
        if PEERS_DIR.exists():
            warn("Peers directory contents:")
            for p in PEERS_DIR.iterdir():
                print(f"  {p}")
                # List files in peer dir
                peer_dir = PEERS_DIR / p.name
                if peer_dir.is_dir():
                    for f in peer_dir.iterdir():
                        print(f"    {f.name}")
        return False


def pull_and_verify_updated_poetry(temp_dir: Path, peer_email: str) -> bool:
    """Pull peer's context and verify poetry.md has been updated."""
    log(f"Verifying updated poetry.md from {peer_email}...")

    repo_name = email_to_repo_name(peer_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if peer_poetry.exists():
        content = peer_poetry.read_text()
        log("Pulled updated poetry.md:")
        print(content)
        if "primate" in content and "springtime of mind" in content:
            log("Content verified - updated poetry found ('primate', 'springtime of mind')!")
            return True
        elif "widening gyre" in content:
            error("Content verification failed - still has OLD poetry content ('widening gyre')")
            return False
        else:
            error("Content verification failed - unexpected content")
            return False
    else:
        error(f"Could not find: {peer_poetry}")
        return False


def pull_and_verify_phase2_files(temp_dir: Path, peer_email: str) -> tuple[bool, bool]:
    """
    Pull peer's context and verify both philosophy.md (new) and poetry.md (updated).

    Returns:
        Tuple of (philosophy_success, poetry_success)
    """
    log(f"Pulling {peer_email}'s context for Phase 2 verification...")
    claudeconnect("pull", peer_email, cwd=temp_dir)

    repo_name = email_to_repo_name(peer_email)
    peer_dir = PEERS_DIR / repo_name

    # Verify philosophy.md
    peer_philosophy = peer_dir / "philosophy.md"
    philosophy_success = False
    if peer_philosophy.exists():
        content = peer_philosophy.read_text()
        log("Pulled philosophy.md:")
        print(content)
        if "ineffability" in content:
            log("✓ philosophy.md verified - 'ineffability' found!")
            philosophy_success = True
        else:
            error("✗ philosophy.md verification failed - 'ineffability' not found")
    else:
        error(f"✗ Could not find: {peer_philosophy}")

    # Verify updated poetry.md
    peer_poetry = peer_dir / "poetry.md"
    poetry_success = False
    if peer_poetry.exists():
        try:
            content = peer_poetry.read_text()
            log("Pulled updated poetry.md:")
            print(content)
            if "primate" in content and "springtime of mind" in content:
                log("✓ poetry.md verified - updated content found ('primate', 'springtime of mind')!")
                poetry_success = True
            elif "widening gyre" in content:
                error("✗ poetry.md verification failed - still has OLD content ('widening gyre')")
            else:
                error("✗ poetry.md verification failed - unexpected content")
        except UnicodeDecodeError as e:
            error(f"✗ poetry.md appears to still be encrypted (not decrypted by pull)")
            # Show raw bytes for debugging
            raw = peer_poetry.read_bytes()
            log(f"  File size: {len(raw)} bytes")
            log(f"  First 50 bytes (hex): {raw[:50].hex()}")
    else:
        error(f"✗ Could not find: {peer_poetry}")

    return philosophy_success, poetry_success


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
def test_fileops_flow(temp_dirs):
    """
    File operations integration test for ClaudeConnect.

    Extends base flow to test adding files after initial setup:
    1. Base flow: login, init, friend request, poetry session
    2. Account 2 adds philosophy.md and syncs
    3. Account 1 pulls and verifies philosophy.md
    4. Account 1 starts philosophy session

    Run with: pytest tests/test_fileops.py -s -m integration
    """
    temp1, temp2 = temp_dirs

    # ==========================================
    # PHASE 1: Base flow (same as integration.py)
    # ==========================================

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

    # Poetry session
    start_session(temp1, account2_email, "talk about poetry!")
    verify_transcript("Account 1", temp1, account2_email)
    pull_and_verify_poetry(temp1, account2_email)

    # Account 2 verifies poetry transcript
    os.chdir(temp2)
    login("Account 2", temp2)
    init_account("Account 2", temp2)
    sync("Account 2", temp2)
    poetry_transcript_success = verify_transcript("Account 2", temp2, account1_email)
    assert poetry_transcript_success, "Poetry transcript did not sync to Account 2"

    # ==========================================
    # PHASE 2: File operations - add philosophy.md
    # ==========================================

    log("")
    log("==========================================")
    log("PHASE 2: Adding philosophy.md + updating poetry.md")
    log("==========================================")

    # Account 2 adds philosophy.md AND updates poetry.md
    create_philosophy_file(temp2)
    update_poetry_file(temp2)
    sync("Account 2", temp2)
    log("Account 2 synced philosophy.md (new) and poetry.md (updated)")

    # Account 1 pulls and verifies both files
    os.chdir(temp1)
    login("Account 1", temp1)
    init_account("Account 1", temp1)
    sync("Account 1", temp1)

    philosophy_success, poetry_update_success = pull_and_verify_phase2_files(temp1, account2_email)
    assert philosophy_success, "Failed to pull and verify philosophy.md"
    assert poetry_update_success, "Failed to pull and verify updated poetry.md"

    # Start philosophy session
    start_session(temp1, account2_email, "talk about philosophy!")
    philosophy_transcript_success = verify_transcript("Account 1", temp1, account2_email)

    # Account 2 verifies philosophy transcript
    os.chdir(temp2)
    login("Account 2", temp2)
    init_account("Account 2", temp2)
    sync("Account 2", temp2)
    account2_philosophy_transcript = verify_transcript("Account 2", temp2, account1_email)

    # ==========================================
    # Summary
    # ==========================================
    print()
    log("==========================================")
    log("File operations test complete!")
    log("==========================================")
    log(f"Account 1: {account1_email}")
    log(f"Account 2: {account2_email}")
    log(f"Poetry transcript synced: {poetry_transcript_success}")
    log(f"Philosophy.md pulled: {philosophy_success}")
    log(f"Poetry.md updated: {poetry_update_success}")
    log(f"Philosophy transcript (Account 1): {philosophy_transcript_success}")
    log(f"Philosophy transcript (Account 2): {account2_philosophy_transcript}")

    assert account2_philosophy_transcript, "Philosophy transcript did not sync to Account 2"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # Phase 1: Base flow
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
        verify_transcript("Account 1", temp1, account2_email)
        pull_and_verify_poetry(temp1, account2_email)

        os.chdir(temp2)
        login("Account 2", temp2)
        init_account("Account 2", temp2)
        sync("Account 2", temp2)
        verify_transcript("Account 2", temp2, account1_email)

        # Phase 2: Add philosophy.md + update poetry.md
        log("")
        log("==========================================")
        log("PHASE 2: Adding philosophy.md + updating poetry.md")
        log("==========================================")

        create_philosophy_file(temp2)
        update_poetry_file(temp2)
        sync("Account 2", temp2)

        os.chdir(temp1)
        login("Account 1", temp1)
        init_account("Account 1", temp1)
        sync("Account 1", temp1)
        pull_and_verify_phase2_files(temp1, account2_email)

        start_session(temp1, account2_email, "philosophy")
        verify_transcript("Account 1", temp1, account2_email)

        os.chdir(temp2)
        login("Account 2", temp2)
        init_account("Account 2", temp2)
        sync("Account 2", temp2)
        verify_transcript("Account 2", temp2, account1_email)

        log("==========================================")
        log("File operations test complete!")
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
