#!/usr/bin/env python3
"""
ClaudeConnect File Operations Integration Test (HTTP Sync)

Tests file add/sync operations using HTTP-based sync:
1. Full base flow (login, init, friend request, session)
2. Bob adds philosophy.md and syncs
3. Alice pulls and verifies philosophy.md content
4. Session about philosophy

Includes detailed timing information for all operations.

Run with: pytest tests/test_fileops.py -s -m integration
Or standalone: python tests/test_fileops.py
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
import time
import httpx
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import pytest

from conf import ALICE_EMAIL, BOB_EMAIL, SERVER, API_BASE_URL, SSH_KEY_PATH

# Config directories
CC_CONFIG_DIR = Path.home() / ".claude-connect"
PEERS_DIR = CC_CONFIG_DIR / "peers"


@dataclass
class TimingStats:
    """Track timing for various operations."""
    alice_init: float = 0.0
    bob_init: float = 0.0
    bob_sync: float = 0.0
    friend_request: float = 0.0
    alice_sync: float = 0.0
    accept_friend: float = 0.0
    pull_context: float = 0.0
    poetry_session: float = 0.0
    philosophy_add: float = 0.0
    philosophy_sync: float = 0.0
    philosophy_pull: float = 0.0
    philosophy_session: float = 0.0
    total: float = 0.0

    def print_summary(self):
        """Print timing summary."""
        print("\n" + "=" * 50)
        print("TIMING SUMMARY")
        print("=" * 50)
        print(f"  Alice init:              {self.alice_init:.1f}s")
        print(f"  Bob init:                {self.bob_init:.1f}s")
        print(f"  Bob sync:                {self.bob_sync:.1f}s")
        print(f"  Friend request:          {self.friend_request:.1f}s")
        print(f"  Alice sync:              {self.alice_sync:.1f}s")
        print(f"  Accept friend:           {self.accept_friend:.1f}s")
        print(f"  Pull context:            {self.pull_context:.1f}s")
        print(f"  Poetry session:          {self.poetry_session:.1f}s")
        print(f"  Philosophy add:          {self.philosophy_add:.1f}s")
        print(f"  Philosophy sync:         {self.philosophy_sync:.1f}s")
        print(f"  Philosophy pull:         {self.philosophy_pull:.1f}s")
        print(f"  Philosophy session:      {self.philosophy_session:.1f}s")
        print("-" * 50)
        print(f"  TOTAL:                   {self.total:.1f}s")
        print("=" * 50)


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


def timing(msg: str, elapsed: float):
    print(f"{Colors.CYAN}[TIMING]{Colors.RESET} {msg}: {elapsed:.2f}s")


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name format."""
    return email.replace("@", "-").replace(".", "-").lower()


def clean_server():
    """Remove test account data on server via SSH."""
    log("Cleaning test account data on server...")
    test_repos = [
        email_to_repo_name(ALICE_EMAIL),
        email_to_repo_name(BOB_EMAIL),
    ]
    for repo in test_repos:
        try:
            # Clean both SVN repos and HTTP storage
            cmd = f"sudo rm -rf /var/svn/repos/{repo} /var/claudeconnect/storage/{repo}"
            subprocess.run(
                ["ssh", "-i", str(SSH_KEY_PATH), f"ubuntu@{SERVER}", cmd],
                capture_output=True,
                timeout=30,
            )
        except Exception as e:
            warn(f"Could not clean {repo}: {e}")
    log("Server cleanup complete")


def clean_client():
    """Remove local ~/.claude-connect."""
    log("Removing local ~/.claude-connect...")
    if CC_CONFIG_DIR.exists():
        shutil.rmtree(CC_CONFIG_DIR)
    log("Local config removed")


def setup_test_user(email: str) -> dict:
    """Set up test user credentials via API."""
    log(f"Setting up test user: {email}")
    response = httpx.post(
        f"{API_BASE_URL}/test/create-user",
        json={"email": email},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Failed to create test user: {response.text}")
    return response.json()


def save_tokens(email: str, id_token: str, refresh_token: str = "test-refresh"):
    """Save tokens to config directory."""
    CC_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    tokens_file.write_text(json.dumps({
        "email": email,
        "id_token": id_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + 3600,
    }))


def init_account(name: str, temp_dir: Path, email: str) -> float:
    """Initialize an account. Returns time taken."""
    log(f"Initializing {name} ({email})...")

    # Set up test user and save tokens
    creds = setup_test_user(email)
    save_tokens(email, creds["id_token"])

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "init"],
        cwd=temp_dir,
        input="y\n",
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        error(f"Init failed: {result.stderr}")
        raise RuntimeError(f"Init failed for {name}")

    timing(f"{name} init completed", elapsed)
    return elapsed


def sync_files(name: str, temp_dir: Path, timeout: int = 300) -> float:
    """Sync files. Returns time taken."""
    log(f"Syncing {name}...")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "sync"],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        error(f"Sync failed: {result.stderr}")
        raise RuntimeError(f"Sync failed for {name}")

    timing(f"{name} sync completed", elapsed)
    return elapsed


def send_friend_request(temp_dir: Path, to_email: str) -> float:
    """Send friend request. Returns time taken."""
    log(f"Sending friend request to {to_email}...")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "friend", to_email],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        error(f"Friend request failed: {result.stderr}")
        raise RuntimeError(f"Friend request failed")

    timing("Friend request sent", elapsed)
    return elapsed


def accept_friend_request(temp_dir: Path, from_email: str) -> float:
    """Accept friend request. Returns time taken."""
    log(f"Accepting friend request from {from_email}...")

    start_time = time.time()
    result = subprocess.run(
        ["claudeconnect", "accept-friend", from_email],
        cwd=temp_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    elapsed = time.time() - start_time

    if result.returncode != 0:
        error(f"Accept friend failed: {result.stderr}")
        raise RuntimeError(f"Accept friend failed")

    timing("Friend request accepted", elapsed)
    return elapsed


def pull_peer_context(temp_dir: Path, peer_email: str, timeout: int = 300) -> float:
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


def start_session(temp_dir: Path, peer_email: str, topic: str, timeout: int = 600) -> float:
    """Start a session with peer. Returns time taken."""
    log(f"Starting session about {topic}...")

    start_time = time.time()
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
        timeout=timeout,
    )
    elapsed = time.time() - start_time

    timing(f"Session completed", elapsed)
    return elapsed


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


def verify_transcript(name: str, temp_dir: Path, peer_email: str) -> bool:
    """Verify transcript exists in with-<peer> directory."""
    log(f"Verifying transcript for {name}...")
    peer_dir_name = f"with-{email_to_repo_name(peer_email)}"
    conv_dir = temp_dir / "claudeconnect" / peer_dir_name

    if not conv_dir.exists():
        error(f"Peer directory not found: {conv_dir}")
        return False

    transcripts = [f for f in conv_dir.glob("*.md") if "friend-request" not in f.name and "README" not in f.name]
    if transcripts:
        log(f"Found {len(transcripts)} transcript(s)")
        return True
    else:
        error("No transcripts found!")
        return False


def verify_poetry(peer_email: str) -> bool:
    """Verify poetry.md was pulled correctly."""
    log("Verifying poetry.md...")

    repo_name = email_to_repo_name(peer_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if not peer_poetry.exists():
        error(f"poetry.md not found: {peer_poetry}")
        return False

    content = peer_poetry.read_text()
    if "widening gyre" in content:
        log("poetry.md verified - 'widening gyre' found!")
        return True
    else:
        error("poetry.md verification failed")
        return False


def verify_philosophy(peer_email: str) -> bool:
    """Verify philosophy.md was pulled correctly."""
    log("Verifying philosophy.md...")

    repo_name = email_to_repo_name(peer_email)
    peer_philosophy = PEERS_DIR / repo_name / "philosophy.md"

    if not peer_philosophy.exists():
        error(f"philosophy.md not found: {peer_philosophy}")
        return False

    content = peer_philosophy.read_text()
    if "ineffability" in content:
        log("philosophy.md verified - 'ineffability' found!")
        return True
    else:
        error("philosophy.md verification failed - 'ineffability' not found")
        return False


def verify_updated_poetry(peer_email: str) -> bool:
    """Verify poetry.md was updated correctly."""
    log("Verifying updated poetry.md...")

    repo_name = email_to_repo_name(peer_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if not peer_poetry.exists():
        error(f"poetry.md not found: {peer_poetry}")
        return False

    try:
        content = peer_poetry.read_text()
        if "primate" in content and "springtime of mind" in content:
            log("Updated poetry.md verified!")
            return True
        elif "widening gyre" in content:
            error("poetry.md still has OLD content")
            return False
        else:
            error("poetry.md has unexpected content")
            return False
    except UnicodeDecodeError:
        error("poetry.md appears to still be encrypted")
        return False


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_bob_"))
    log(f"Created {temp1} (Alice)")
    log(f"Created {temp2} (Bob)")

    yield temp1, temp2

    # Cleanup
    if temp1.exists():
        shutil.rmtree(temp1)
    if temp2.exists():
        shutil.rmtree(temp2)


@pytest.mark.integration
def test_fileops_flow(temp_dirs):
    """
    File operations integration test for ClaudeConnect.

    Tests adding files after initial setup:
    1. Base flow: login, init, friend request, poetry session
    2. Bob adds philosophy.md and syncs
    3. Alice pulls and verifies philosophy.md
    4. Alice starts philosophy session

    Run with: pytest tests/test_fileops.py -s -m integration
    """
    temp1, temp2 = temp_dirs
    stats = TimingStats()
    overall_start = time.time()

    # Setup
    clean_server()
    clean_client()

    # ==========================================
    # PHASE 1: Base flow
    # ==========================================
    log("=" * 50)
    log("PHASE 1: Base flow")
    log("=" * 50)

    # Bob setup (creates poetry.md first)
    stats.bob_init = init_account("Bob", temp2, BOB_EMAIL)
    create_poetry_file(temp2)
    stats.bob_sync = sync_files("Bob", temp2)
    stats.friend_request = send_friend_request(temp2, ALICE_EMAIL)

    # Alice setup and accepts friend
    stats.alice_init = init_account("Alice", temp1, ALICE_EMAIL)
    stats.alice_sync = sync_files("Alice", temp1)
    stats.accept_friend = accept_friend_request(temp1, BOB_EMAIL)

    # Pull and verify poetry
    stats.pull_context = pull_peer_context(temp1, BOB_EMAIL)
    poetry_success = verify_poetry(BOB_EMAIL)
    assert poetry_success, "Failed to verify poetry.md"

    # Poetry session
    stats.poetry_session = start_session(temp1, BOB_EMAIL, "talk about poetry!")
    transcript_success = verify_transcript("Alice", temp1, BOB_EMAIL)
    assert transcript_success, "Poetry transcript not found"

    # ==========================================
    # PHASE 2: Add philosophy.md
    # ==========================================
    log("")
    log("=" * 50)
    log("PHASE 2: Adding philosophy.md + updating poetry.md")
    log("=" * 50)

    # Bob adds philosophy.md and updates poetry.md
    start_time = time.time()
    create_philosophy_file(temp2)
    update_poetry_file(temp2)
    stats.philosophy_add = time.time() - start_time

    # Bob syncs changes
    # Re-setup Bob's tokens
    creds = setup_test_user(BOB_EMAIL)
    save_tokens(BOB_EMAIL, creds["id_token"])
    stats.philosophy_sync = sync_files("Bob", temp2)

    # Alice pulls and verifies
    # Re-setup Alice's tokens
    creds = setup_test_user(ALICE_EMAIL)
    save_tokens(ALICE_EMAIL, creds["id_token"])
    stats.alice_sync = sync_files("Alice", temp1)
    stats.philosophy_pull = pull_peer_context(temp1, BOB_EMAIL)

    philosophy_success = verify_philosophy(BOB_EMAIL)
    assert philosophy_success, "Failed to verify philosophy.md"

    poetry_update_success = verify_updated_poetry(BOB_EMAIL)
    assert poetry_update_success, "Failed to verify updated poetry.md"

    # Philosophy session
    stats.philosophy_session = start_session(temp1, BOB_EMAIL, "talk about philosophy!")
    philosophy_transcript = verify_transcript("Alice", temp1, BOB_EMAIL)

    # ==========================================
    # Summary
    # ==========================================
    stats.total = time.time() - overall_start
    stats.print_summary()

    log("")
    log("=" * 50)
    log("TEST RESULTS")
    log("=" * 50)
    log(f"Alice: {ALICE_EMAIL}")
    log(f"Bob: {BOB_EMAIL}")
    log(f"Poetry verified: {poetry_success}")
    log(f"Poetry transcript: {transcript_success}")
    log(f"Philosophy verified: {philosophy_success}")
    log(f"Poetry update verified: {poetry_update_success}")
    log(f"Philosophy transcript: {philosophy_transcript}")

    assert philosophy_transcript, "Philosophy transcript not found"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None
    stats = TimingStats()
    overall_start = time.time()

    try:
        clean_server()
        clean_client()

        temp1 = Path(tempfile.mkdtemp(prefix="cc_test_alice_"))
        temp2 = Path(tempfile.mkdtemp(prefix="cc_test_bob_"))
        log(f"Created {temp1} (Alice)")
        log(f"Created {temp2} (Bob)")

        # Phase 1: Base flow
        log("=" * 50)
        log("PHASE 1: Base flow")
        log("=" * 50)

        stats.bob_init = init_account("Bob", temp2, BOB_EMAIL)
        create_poetry_file(temp2)
        stats.bob_sync = sync_files("Bob", temp2)
        stats.friend_request = send_friend_request(temp2, ALICE_EMAIL)

        stats.alice_init = init_account("Alice", temp1, ALICE_EMAIL)
        stats.alice_sync = sync_files("Alice", temp1)
        stats.accept_friend = accept_friend_request(temp1, BOB_EMAIL)

        stats.pull_context = pull_peer_context(temp1, BOB_EMAIL)
        verify_poetry(BOB_EMAIL)

        stats.poetry_session = start_session(temp1, BOB_EMAIL, "poetry")
        verify_transcript("Alice", temp1, BOB_EMAIL)

        # Phase 2: Add philosophy.md
        log("")
        log("=" * 50)
        log("PHASE 2: Adding philosophy.md + updating poetry.md")
        log("=" * 50)

        start_time = time.time()
        create_philosophy_file(temp2)
        update_poetry_file(temp2)
        stats.philosophy_add = time.time() - start_time

        creds = setup_test_user(BOB_EMAIL)
        save_tokens(BOB_EMAIL, creds["id_token"])
        stats.philosophy_sync = sync_files("Bob", temp2)

        creds = setup_test_user(ALICE_EMAIL)
        save_tokens(ALICE_EMAIL, creds["id_token"])
        stats.alice_sync = sync_files("Alice", temp1)
        stats.philosophy_pull = pull_peer_context(temp1, BOB_EMAIL)

        verify_philosophy(BOB_EMAIL)
        verify_updated_poetry(BOB_EMAIL)

        stats.philosophy_session = start_session(temp1, BOB_EMAIL, "philosophy")
        verify_transcript("Alice", temp1, BOB_EMAIL)

        stats.total = time.time() - overall_start
        stats.print_summary()

        log("")
        log("=" * 50)
        log("File operations test complete!")
        log("=" * 50)
        log(f"Alice: {ALICE_EMAIL}")
        log(f"Bob: {BOB_EMAIL}")

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
