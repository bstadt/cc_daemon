#!/usr/bin/env python3
"""
ClaudeConnect Big Vault Integration Test

Tests syncing large document collections:
1. Account 2 creates 2000 markdown files BEFORE init
2. Account 2 initializes and syncs (tests batch upload during init)
3. Friend request flow
4. Account 1 pulls all 2000 files
5. Conversation about the vault

Run with: pytest tests/test_big_vault.py -s -m integration
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
import time
from pathlib import Path
from typing import Optional

import pytest

# Server config
SERVER = "v2.claudeconnect.io"
SSH_KEY = Path.home() / ".ssh" / "calco_key.pem"
CC_CONFIG_DIR = Path.home() / ".claude-connect"
PEERS_DIR = CC_CONFIG_DIR / "peers"

# Test config
NUM_VAULT_FILES = 2000
BORGES_LINE = "The universe (which others call the Library) is composed of an indefinite, perhaps infinite number of hexagonal galleries"


class Colors:
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    CYAN = "\033[0;36m"
    RESET = "\033[0m"


def log(msg: str):
    print(f"{Colors.GREEN}[TEST]{Colors.RESET} {msg}")


def warn(msg: str):
    print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")


def error(msg: str):
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")


def prompt(msg: str):
    print(f"{Colors.YELLOW}[ACTION REQUIRED]{Colors.RESET} {msg}")


def progress(current: int, total: int, msg: str):
    print(f"{Colors.CYAN}[{current}/{total}]{Colors.RESET} {msg}")


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


def claudeconnect(*args, cwd: Path | None = None, input_text: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run claudeconnect CLI command with extended timeout for large operations."""
    result = subprocess.run(
        ["claudeconnect", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=timeout,
    )
    if result.returncode != 0:
        error(f"Command failed: claudeconnect {' '.join(args)}")
        error(f"stdout: {result.stdout}")
        error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: claudeconnect {' '.join(args)}")
    return result


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


def create_vault_files(temp_dir: Path, num_files: int = NUM_VAULT_FILES):
    """
    Create a large number of markdown files BEFORE initialization.

    Each file contains the Borges line + the file number.
    Files are organized in a 'vault' subdirectory.
    """
    log(f"Creating {num_files} vault files...")
    vault_dir = temp_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    for i in range(1, num_files + 1):
        file_path = vault_dir / f"{i}.md"
        content = f"# File {i}\n\n{BORGES_LINE}\n\nFile number: {i}\n"
        file_path.write_text(content)

        # Progress every 500 files
        if i % 500 == 0:
            elapsed = time.time() - start_time
            progress(i, num_files, f"Created {i} files ({elapsed:.1f}s elapsed)")

    elapsed = time.time() - start_time
    log(f"Created {num_files} vault files in {elapsed:.1f}s")

    # Verify
    actual_count = len(list(vault_dir.glob("*.md")))
    assert actual_count == num_files, f"Expected {num_files} files, found {actual_count}"
    log(f"Verified: {actual_count} files in vault/")


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
    # Use longer timeout for init with many files
    claudeconnect("init", cwd=temp_dir, input_text="y\n", timeout=1200)
    email = get_current_email()
    log(f"{account_name} initialized: {email}")
    return email


def sync(account_name: str, temp_dir: Path, timeout: int = 600):
    """Sync an account with extended timeout."""
    log(f"Syncing {account_name}...")
    start_time = time.time()
    claudeconnect("sync", cwd=temp_dir, timeout=timeout)
    elapsed = time.time() - start_time
    log(f"{account_name} synced in {elapsed:.1f}s")


def send_friend_request(temp_dir: Path, to_email: str):
    """Send friend request."""
    log(f"Sending friend request to {to_email}...")
    claudeconnect("friend", to_email, cwd=temp_dir)
    log(f"Friend request sent.")


def accept_friend_request(temp_dir: Path, from_email: str):
    """Accept friend request."""
    log(f"Accepting friend request from {from_email}...")
    claudeconnect("accept-friend", from_email, cwd=temp_dir)
    log("Friend request accepted.")


def start_session(temp_dir: Path, peer_email: str, topic: str):
    """Start a session with peer (1 turn, output streamed live)."""
    log(f"Starting session about {topic}...")
    # Run session with live output (not captured)
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
    )


def pull_and_verify_vault(temp_dir: Path, peer_email: str, expected_files: int = NUM_VAULT_FILES) -> bool:
    """
    Pull peer's context and verify vault files.

    Returns:
        True if vault files were successfully pulled and verified.
    """
    log(f"Pulling {peer_email}'s context (expecting {expected_files} vault files)...")
    start_time = time.time()

    # Pull with extended timeout
    claudeconnect("pull", peer_email, cwd=temp_dir, timeout=1200)

    elapsed = time.time() - start_time
    log(f"Pull completed in {elapsed:.1f}s")

    # Verify vault files
    repo_name = email_to_repo_name(peer_email)
    peer_vault = PEERS_DIR / repo_name / "vault"

    if not peer_vault.exists():
        error(f"Vault directory not found: {peer_vault}")
        return False

    # Count files
    vault_files = list(peer_vault.glob("*.md"))
    actual_count = len(vault_files)

    log(f"Found {actual_count} files in pulled vault")

    if actual_count != expected_files:
        error(f"Expected {expected_files} files, found {actual_count}")
        return False

    # Verify content of a few random files
    test_files = [1, 500, 1000, 1500, 2000]
    for file_num in test_files:
        if file_num > expected_files:
            continue
        file_path = peer_vault / f"{file_num}.md"
        if not file_path.exists():
            error(f"Missing file: {file_num}.md")
            return False

        try:
            content = file_path.read_text()
            if BORGES_LINE not in content:
                error(f"File {file_num}.md missing expected content")
                return False
            if f"File number: {file_num}" not in content:
                error(f"File {file_num}.md has wrong file number")
                return False
        except UnicodeDecodeError:
            error(f"File {file_num}.md appears to still be encrypted")
            return False

    log(f"✓ Verified vault: {actual_count} files with correct content")
    return True


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
        return True
    else:
        error("No transcripts found!")
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
def test_big_vault(temp_dirs):
    """
    Big vault integration test for ClaudeConnect.

    Tests syncing a large collection of files (2000 markdown files):
    1. Account 2 creates 2000 files BEFORE init
    2. Account 2 initializes (should detect and sync all files)
    3. Account 2 syncs to upload all files
    4. Friend request flow
    5. Account 1 pulls all 2000 files
    6. Verify content decryption
    7. Start conversation about the vault

    Run with: pytest tests/test_big_vault.py -s -m integration
    """
    temp1, temp2 = temp_dirs

    # Setup
    clean_server()
    clean_client()

    # ==========================================
    # Account 1: Initial setup
    # ==========================================
    log("")
    log("==========================================")
    log("Account 1: Initial setup")
    log("==========================================")

    login("Account 1", temp1)
    account1_email = init_account("Account 1", temp1)

    # ==========================================
    # Account 2: Create vault BEFORE init
    # ==========================================
    log("")
    log("==========================================")
    log("Account 2: Creating vault files BEFORE init")
    log("==========================================")

    os.chdir(temp2)

    # Create 2000 files BEFORE login/init
    create_vault_files(temp2, NUM_VAULT_FILES)

    # Now login and init (should detect the files)
    login("Account 2", temp2)
    account2_email = init_account("Account 2", temp2)

    # Sync to upload all files
    log("Syncing vault files to server...")
    sync("Account 2", temp2, timeout=1200)

    # Send friend request
    send_friend_request(temp2, account1_email)

    # ==========================================
    # Account 1: Accept and pull vault
    # ==========================================
    log("")
    log("==========================================")
    log("Account 1: Accept friend request and pull vault")
    log("==========================================")

    os.chdir(temp1)
    login("Account 1", temp1)
    init_account("Account 1", temp1)
    sync("Account 1", temp1)
    accept_friend_request(temp1, account2_email)

    # Pull and verify all 2000 files
    vault_success = pull_and_verify_vault(temp1, account2_email, NUM_VAULT_FILES)
    assert vault_success, f"Failed to pull and verify {NUM_VAULT_FILES} vault files"

    # Start conversation about the vault
    start_session(temp1, account2_email, "discuss the Library of Babel vault")
    transcript_success = verify_transcript("Account 1", temp1, account2_email)

    # ==========================================
    # Summary
    # ==========================================
    print()
    log("==========================================")
    log("Big vault test complete!")
    log("==========================================")
    log(f"Account 1: {account1_email}")
    log(f"Account 2: {account2_email}")
    log(f"Vault files created: {NUM_VAULT_FILES}")
    log(f"Vault pull verified: {vault_success}")
    log(f"Transcript created: {transcript_success}")

    assert transcript_success, "Transcript was not created"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # Account 1 setup
        login("Account 1", temp1)
        account1_email = init_account("Account 1", temp1)

        # Account 2: Create vault BEFORE init
        os.chdir(temp2)
        create_vault_files(temp2, NUM_VAULT_FILES)

        login("Account 2", temp2)
        account2_email = init_account("Account 2", temp2)
        sync("Account 2", temp2, timeout=1200)
        send_friend_request(temp2, account1_email)

        # Account 1: Accept and pull
        os.chdir(temp1)
        login("Account 1", temp1)
        init_account("Account 1", temp1)
        sync("Account 1", temp1)
        accept_friend_request(temp1, account2_email)

        vault_success = pull_and_verify_vault(temp1, account2_email, NUM_VAULT_FILES)

        start_session(temp1, account2_email, "discuss the Library of Babel vault")
        verify_transcript("Account 1", temp1, account2_email)

        log("==========================================")
        log("Big vault test complete!")
        log("==========================================")
        log(f"Account 1: {account1_email}")
        log(f"Account 2: {account2_email}")
        log(f"Vault files: {NUM_VAULT_FILES}")
        log(f"Vault verified: {vault_success}")

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
