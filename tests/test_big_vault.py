#!/usr/bin/env python3
"""
ClaudeConnect Big Vault Integration Test (HTTP Sync)

Tests syncing large document collections using HTTP-based sync:
1. Bob creates 500 markdown files BEFORE init
2. Bob initializes and syncs (tests batch upload)
3. Friend request flow
4. Alice pulls all 500 files
5. Conversation about the vault

Includes detailed timing information for all operations.

Run with: pytest tests/test_big_vault.py -s -m integration
Or standalone: python tests/test_big_vault.py
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import shutil
import time
from pathlib import Path
from dataclasses import dataclass

import pytest

from conf import ALICE_EMAIL, BOB_EMAIL
from test_utils import (
    log, warn, error, timing,
    clean_server, clean_client, claudeconnect,
    get_current_email, email_to_repo_name, wait_for_user,
    CC_CONFIG_DIR, PEERS_DIR, Colors,
)

# Test config
NUM_VAULT_FILES = 500
BORGES_LINE = "The universe (which others call the Library) is composed of an indefinite, perhaps infinite number of hexagonal galleries"


@dataclass
class TimingStats:
    """Track timing for various operations."""
    vault_creation: float = 0.0
    account1_init: float = 0.0
    account2_init: float = 0.0
    account2_sync: float = 0.0
    friend_request: float = 0.0
    account1_sync: float = 0.0
    accept_friend: float = 0.0
    pull_vault: float = 0.0
    session: float = 0.0
    total: float = 0.0

    # File stats
    files_uploaded: int = 0
    files_downloaded: int = 0

    def print_summary(self):
        """Print timing summary."""
        print("\n" + "=" * 50)
        print("TIMING SUMMARY")
        print("=" * 50)
        print(f"  Vault creation ({NUM_VAULT_FILES} files): {self.vault_creation:.1f}s")
        print(f"  Alice init:                  {self.account1_init:.1f}s")
        print(f"  Bob init:                  {self.account2_init:.1f}s")
        print(f"  Bob sync (upload):         {self.account2_sync:.1f}s")
        if self.account2_sync > 0:
            rate = NUM_VAULT_FILES / self.account2_sync
            print(f"    Upload rate:                   {rate:.1f} files/sec")
        print(f"  Friend request:                  {self.friend_request:.1f}s")
        print(f"  Alice sync:                  {self.account1_sync:.1f}s")
        print(f"  Accept friend:                   {self.accept_friend:.1f}s")
        print(f"  Pull vault (download):           {self.pull_vault:.1f}s")
        if self.pull_vault > 0:
            rate = NUM_VAULT_FILES / self.pull_vault
            print(f"    Download rate:                 {rate:.1f} files/sec")
        print(f"  Session:                         {self.session:.1f}s")
        print("-" * 50)
        print(f"  TOTAL:                           {self.total:.1f}s")
        print("=" * 50)


def progress(current: int, total: int, msg: str):
    """Print progress indicator."""
    print(f"{Colors.CYAN}[{current}/{total}]{Colors.RESET} {msg}")


def create_temp_dirs() -> tuple[Path, Path]:
    """Create temp directories for both accounts."""
    log("Creating temp directories...")
    temp1 = Path(tempfile.mkdtemp(prefix="cc_bigvault_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_bigvault_bob_"))
    log(f"Created {temp1} (Alice)")
    log(f"Created {temp2} (Bob)")
    return temp1, temp2


def create_vault_files(temp_dir: Path, num_files: int = NUM_VAULT_FILES) -> float:
    """
    Create a large number of markdown files BEFORE initialization.

    Returns time taken in seconds.
    """
    log(f"Creating {num_files} vault files...")
    vault_dir = temp_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    for i in range(1, num_files + 1):
        file_path = vault_dir / f"{i}.md"
        content = f"# File {i}\n\n{BORGES_LINE}\n\nFile number: {i}\n"
        file_path.write_text(content)

        # Progress every 100 files
        if i % 100 == 0:
            elapsed = time.time() - start_time
            progress(i, num_files, f"Created {i} files ({elapsed:.1f}s elapsed)")

    elapsed = time.time() - start_time
    timing(f"Created {num_files} vault files", elapsed)

    # Verify
    actual_count = len(list(vault_dir.glob("*.md")))
    assert actual_count == num_files, f"Expected {num_files} files, found {actual_count}"
    log(f"Verified: {actual_count} files in vault/")

    return elapsed


def login(account_name: str, temp_dir: Path):
    """Login to an account."""
    log(f"Logging in as {account_name}...")
    os.chdir(temp_dir)
    wait_for_user(f"Please login with {account_name}")
    claudeconnect("login", cwd=temp_dir)


def init_account(account_name: str, temp_dir: Path, expected_email: str) -> float:
    """Initialize an account. Returns time_taken."""
    log(f"Initializing {account_name} ({expected_email})...")
    wait_for_user(f"{account_name} logged in. Ready to init.")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "init"],
        cwd=temp_dir,
        input="y\n",
        text=True,
        timeout=1200,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        raise RuntimeError(f"Init failed for {account_name}")

    email = get_current_email()
    if email != expected_email:
        warn(f"Expected {expected_email}, got {email}")
    timing(f"{account_name} initialized", elapsed)
    return elapsed


def sync_files(account_name: str, temp_dir: Path, timeout: int = 1200) -> float:
    """Sync files with server. Returns time taken."""
    log(f"Syncing {account_name}...")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "sync"],
        cwd=temp_dir,
        timeout=timeout,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        raise RuntimeError(f"Sync failed for {account_name}")

    timing(f"{account_name} sync completed", elapsed)
    return elapsed


def send_friend_request(temp_dir: Path, to_email: str) -> float:
    """Send friend request. Returns time taken."""
    log(f"Sending friend request to {to_email}...")

    start_time = time.time()
    claudeconnect("friend", to_email, cwd=temp_dir)
    elapsed = time.time() - start_time

    timing("Friend request sent", elapsed)
    return elapsed


def accept_friend_request(temp_dir: Path, from_email: str) -> float:
    """Accept friend request. Returns time taken."""
    log(f"Accepting friend request from {from_email}...")

    start_time = time.time()
    claudeconnect("accept-friend", from_email, cwd=temp_dir)
    elapsed = time.time() - start_time

    timing("Friend request accepted", elapsed)
    return elapsed


def pull_peer_context(temp_dir: Path, peer_email: str, timeout: int = 1200) -> float:
    """Pull peer's context. Returns time taken."""
    log(f"Pulling {peer_email}'s context...")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "pull", peer_email],
        cwd=temp_dir,
        timeout=timeout,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        raise RuntimeError(f"Pull failed for {peer_email}")

    timing(f"Pull {peer_email} completed", elapsed)
    return elapsed


def verify_vault_files(peer_email: str, expected_files: int = NUM_VAULT_FILES) -> bool:
    """Verify vault files were pulled correctly."""
    log(f"Verifying {expected_files} vault files...")

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

    # Verify content of sample files
    test_files = [1, 100, 250, 400, 500]
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


def start_session(temp_dir: Path, peer_email: str, topic: str) -> float:
    """Start a session with peer. Returns time taken."""
    log(f"Starting session about {topic}...")

    start_time = time.time()
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
    )
    elapsed = time.time() - start_time

    timing("Session completed", elapsed)
    return elapsed


def verify_transcript(account_name: str, temp_dir: Path, peer_email: str) -> bool:
    """Verify transcript exists in with-<peer> directory."""
    log(f"Verifying transcript for {account_name}...")
    peer_dir_name = f"with-{email_to_repo_name(peer_email)}"
    conv_dir = temp_dir / "claudeconnect" / peer_dir_name

    if not conv_dir.exists():
        error(f"Peer directory not found: {conv_dir}")
        return False

    # Look for transcript files (exclude README and friend-request files)
    transcripts = [f for f in conv_dir.glob("*.md")
                   if "friend-request" not in f.name and f.name != "README.md"]
    if transcripts:
        log(f"✓ Found {len(transcripts)} transcript(s)")
        return True
    else:
        error("No transcripts found!")
        return False


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_bigvault_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_bigvault_bob_"))
    log(f"Created {temp1} (Alice)")
    log(f"Created {temp2} (Bob)")

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
    Big vault integration test for ClaudeConnect (HTTP sync).

    Tests syncing a large collection of files:
    1. Bob creates files BEFORE init
    2. Bob initializes and syncs (batch upload)
    3. Friend request flow
    4. Alice pulls all files
    5. Verify content decryption
    6. Start conversation about the vault

    Run with: pytest tests/test_big_vault.py -s -m integration
    """
    temp1, temp2 = temp_dirs
    stats = TimingStats()
    total_start = time.time()

    # Setup
    clean_server()
    clean_client()

    # ==========================================
    # Alice: Initial setup
    # ==========================================
    log("")
    log("=" * 50)
    log("Alice: Initial setup")
    log("=" * 50)

    login("Alice", temp1)
    stats.account1_init = init_account("Alice", temp1, ALICE_EMAIL)

    # ==========================================
    # Bob: Create vault BEFORE init
    # ==========================================
    log("")
    log("=" * 50)
    log("Bob: Creating vault files BEFORE init")
    log("=" * 50)

    os.chdir(temp2)
    stats.vault_creation = create_vault_files(temp2, NUM_VAULT_FILES)

    # Login and init
    login("Bob", temp2)
    stats.account2_init = init_account("Bob", temp2, BOB_EMAIL)

    # Sync to upload all files
    log("")
    log("=" * 50)
    log("Bob: Uploading vault to server")
    log("=" * 50)
    stats.account2_sync = sync_files("Bob", temp2, timeout=1200)

    # Send friend request
    stats.friend_request = send_friend_request(temp2, ALICE_EMAIL)

    # ==========================================
    # Alice: Accept and pull vault
    # ==========================================
    log("")
    log("=" * 50)
    log("Alice: Accept friend request and pull vault")
    log("=" * 50)

    os.chdir(temp1)
    login("Alice", temp1)
    init_account("Alice", temp1, ALICE_EMAIL)
    stats.account1_sync = sync_files("Alice", temp1)
    stats.accept_friend = accept_friend_request(temp1, BOB_EMAIL)

    # Pull and verify all files
    stats.pull_vault = pull_peer_context(temp1, BOB_EMAIL, timeout=1200)
    vault_success = verify_vault_files(BOB_EMAIL, NUM_VAULT_FILES)
    assert vault_success, f"Failed to pull and verify {NUM_VAULT_FILES} vault files"

    # Start conversation
    stats.session = start_session(temp1, BOB_EMAIL, "discuss the Library of Babel vault")
    transcript_success = verify_transcript("Alice", temp1, BOB_EMAIL)

    # ==========================================
    # Summary
    # ==========================================
    stats.total = time.time() - total_start
    stats.print_summary()

    print()
    log("=" * 50)
    log("Big vault test complete!")
    log("=" * 50)
    log(f"Alice: {ALICE_EMAIL}")
    log(f"Bob: {BOB_EMAIL}")
    log(f"Vault files: {NUM_VAULT_FILES}")
    log(f"Vault verified: {vault_success}")
    log(f"Transcript created: {transcript_success}")

    assert transcript_success, "Transcript was not created"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None
    stats = TimingStats()
    total_start = time.time()

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # Alice setup
        login("Alice", temp1)
        stats.account1_init = init_account("Alice", temp1, ALICE_EMAIL)

        # Bob: Create vault BEFORE init
        os.chdir(temp2)
        stats.vault_creation = create_vault_files(temp2, NUM_VAULT_FILES)

        login("Bob", temp2)
        stats.account2_init = init_account("Bob", temp2, BOB_EMAIL)

        log("")
        log("=" * 50)
        log("Bob: Uploading vault to server")
        log("=" * 50)
        stats.account2_sync = sync_files("Bob", temp2, timeout=1200)

        stats.friend_request = send_friend_request(temp2, ALICE_EMAIL)

        # Alice: Accept and pull
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1, ALICE_EMAIL)
        stats.account1_sync = sync_files("Alice", temp1)
        stats.accept_friend = accept_friend_request(temp1, BOB_EMAIL)

        stats.pull_vault = pull_peer_context(temp1, BOB_EMAIL, timeout=1200)
        vault_success = verify_vault_files(BOB_EMAIL, NUM_VAULT_FILES)

        stats.session = start_session(temp1, BOB_EMAIL, "discuss the Library of Babel vault")
        transcript_success = verify_transcript("Alice", temp1, BOB_EMAIL)

        # Summary
        stats.total = time.time() - total_start
        stats.print_summary()

        log("=" * 50)
        log("Big vault test complete!")
        log("=" * 50)
        log(f"Alice: {ALICE_EMAIL}")
        log(f"Bob: {BOB_EMAIL}")
        log(f"Vault files: {NUM_VAULT_FILES}")
        log(f"Vault verified: {vault_success}")
        log(f"Transcript created: {transcript_success}")

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
