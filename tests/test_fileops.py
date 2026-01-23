#!/usr/bin/env python3
"""
ClaudeConnect File Operations Integration Test (HTTP Sync)

Tests file add/update operations after initial setup:
1. Full base flow (same as integration.py)
2. After Bob verifies transcript, Bob modifies poetry.md and adds philosophy.md
3. Alice re-pulls and verifies both the new file and the update

Run with: pytest tests/test_fileops.py -s -m integration
Or standalone: python tests/test_fileops.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass

import pytest

from conf import ALICE_EMAIL, BOB_EMAIL
from test_utils import (
    # Setup/cleanup
    clean_server,
    clean_client,
    create_temp_dirs,
    # Account operations
    login,
    init_account_timed,
    verify_init_structure,
    # File operations
    create_poetry_files,
    update_poetry_file,
    create_philosophy_file,
    # Authz
    update_authz_restrict_secret,
    update_authz_add_philosophy,
    # Sync
    sync_files_timed,
    # Friend operations
    send_friend_request_timed,
    check_friend_request,
    accept_friend_request_timed,
    # Session
    start_session_timed,
    # Verification
    verify_transcript,
    verify_file_access,
    verify_poetry,
    verify_philosophy,
    pull_peer_context_timed,
    # Logging
    log,
    error,
    timing,
)


@dataclass
class TimingStats:
    """Track timing for various operations."""
    alice_init: float = 0.0
    bob_init: float = 0.0
    bob_sync: float = 0.0
    friend_request: float = 0.0
    alice_sync: float = 0.0
    accept_friend: float = 0.0
    poetry_session: float = 0.0
    alice_pull: float = 0.0
    bob_resync: float = 0.0
    bob_transcript_verify: float = 0.0
    # Phase 2
    bob_file_update: float = 0.0
    bob_sync_update: float = 0.0
    alice_repull: float = 0.0
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
        print(f"  Poetry session:          {self.poetry_session:.1f}s")
        print(f"  Alice pull:              {self.alice_pull:.1f}s")
        print(f"  Bob resync:              {self.bob_resync:.1f}s")
        print(f"  Bob transcript verify:   {self.bob_transcript_verify:.1f}s")
        print("-" * 50)
        print(f"  Bob file update:         {self.bob_file_update:.1f}s")
        print(f"  Bob sync update:         {self.bob_sync_update:.1f}s")
        print(f"  Alice repull:            {self.alice_repull:.1f}s")
        print("-" * 50)
        print(f"  TOTAL:                   {self.total:.1f}s")
        print("=" * 50)


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_bob_"))
    log(f"Created {temp1} (Alice)")
    log(f"Created {temp2} (Bob)")

    yield temp1, temp2

    if temp1.exists():
        shutil.rmtree(temp1)
    if temp2.exists():
        shutil.rmtree(temp2)


@pytest.mark.integration
def test_fileops_flow(temp_dirs):
    """
    File operations integration test for ClaudeConnect.

    Tests adding/updating files after initial setup:
    1. Base flow: login, init, friend request, poetry session (same as integration.py)
    2. Bob verifies transcript receipt
    3. Bob modifies poetry.md and adds philosophy.md
    4. Alice re-pulls and verifies both the update and new file

    Run with: pytest tests/test_fileops.py -s -m integration
    """
    temp1, temp2 = temp_dirs
    stats = TimingStats()
    overall_start = time.time()

    clean_server()
    clean_client()

    # ==========================================
    # PHASE 1: Base flow (same as integration.py)
    # ==========================================
    log("=" * 50)
    log("PHASE 1: Base flow")
    log("=" * 50)

    # Alice setup
    login("Alice", temp1)
    alice_email, stats.alice_init = init_account_timed("Alice", temp1)
    verify_init_structure("Alice", temp1)

    # Bob setup
    os.chdir(temp2)
    login("Bob", temp2)
    bob_email, stats.bob_init = init_account_timed("Bob", temp2)
    verify_init_structure("Bob", temp2)

    # Bob creates poetry files and restricts access
    create_poetry_files(temp2)
    update_authz_restrict_secret(temp2, bob_email, alice_email)
    stats.bob_sync = sync_files_timed("Bob", temp2)
    stats.friend_request = send_friend_request_timed(temp2, alice_email)

    # Alice receives and accepts
    os.chdir(temp1)
    login("Alice", temp1)
    init_account_timed("Alice", temp1)
    stats.alice_sync = sync_files_timed("Alice", temp1)
    check_friend_request(temp1, bob_email)
    stats.accept_friend = accept_friend_request_timed(temp1, bob_email)

    # Verify file permissions
    verify_file_access(bob_email, "poetry.md", "secret_poetry.md")

    # Session
    stats.poetry_session = start_session_timed(temp1, bob_email, "talk about poetry!")
    assert verify_transcript("Alice", temp1, bob_email), "Alice transcript not found"
    stats.alice_pull = pull_peer_context_timed(temp1, bob_email)
    assert verify_poetry(bob_email, "widening gyre"), "Poetry verification failed"

    # Bob verifies transcript
    os.chdir(temp2)
    login("Bob", temp2)
    init_account_timed("Bob", temp2)
    start = time.time()
    sync_files_timed("Bob", temp2)
    assert verify_transcript("Bob", temp2, alice_email), "Bob transcript not found"
    stats.bob_transcript_verify = time.time() - start

    log("")
    log("=" * 50)
    log("PHASE 1 COMPLETE: Bob has received the transcript")
    log("=" * 50)

    # ==========================================
    # PHASE 2: Bob modifies poetry.md and adds philosophy.md
    # ==========================================
    log("")
    log("=" * 50)
    log("PHASE 2: File updates - modify poetry.md, add philosophy.md")
    log("=" * 50)

    # Bob updates files (still logged in as Bob from phase 1)
    start = time.time()
    update_poetry_file(temp2)
    create_philosophy_file(temp2)
    update_authz_add_philosophy(temp2, bob_email, alice_email)
    stats.bob_file_update = time.time() - start
    timing("Bob file updates", stats.bob_file_update)

    # Bob syncs the changes
    stats.bob_sync_update = sync_files_timed("Bob", temp2)

    # Alice re-pulls and verifies
    os.chdir(temp1)
    login("Alice", temp1)
    init_account_timed("Alice", temp1)
    sync_files_timed("Alice", temp1)
    stats.alice_repull = pull_peer_context_timed(temp1, bob_email)

    # Verify the updated poetry.md
    poetry_updated = verify_poetry(bob_email, "springtime of mind")
    assert poetry_updated, "poetry.md was not updated!"

    # Verify the new philosophy.md
    philosophy_exists = verify_philosophy(bob_email)
    assert philosophy_exists, "philosophy.md was not found!"

    # ==========================================
    # Summary
    # ==========================================
    stats.total = time.time() - overall_start
    stats.print_summary()

    log("")
    log("=" * 50)
    log("TEST RESULTS")
    log("=" * 50)
    log(f"Alice: {alice_email}")
    log(f"Bob: {bob_email}")
    log(f"Poetry updated: {poetry_updated}")
    log(f"Philosophy added: {philosophy_exists}")
    log("=" * 50)
    log("File operations test PASSED!")


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None
    stats = TimingStats()
    overall_start = time.time()

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # ==========================================
        # PHASE 1: Base flow
        # ==========================================
        log("=" * 50)
        log("PHASE 1: Base flow")
        log("=" * 50)

        # Alice setup
        login("Alice", temp1)
        alice_email, stats.alice_init = init_account_timed("Alice", temp1)
        verify_init_structure("Alice", temp1)

        # Bob setup
        os.chdir(temp2)
        login("Bob", temp2)
        bob_email, stats.bob_init = init_account_timed("Bob", temp2)
        verify_init_structure("Bob", temp2)

        # Bob creates poetry files and restricts access
        create_poetry_files(temp2)
        update_authz_restrict_secret(temp2, bob_email, alice_email)
        stats.bob_sync = sync_files_timed("Bob", temp2)
        stats.friend_request = send_friend_request_timed(temp2, alice_email)

        # Alice receives and accepts
        os.chdir(temp1)
        login("Alice", temp1)
        init_account_timed("Alice", temp1)
        stats.alice_sync = sync_files_timed("Alice", temp1)
        check_friend_request(temp1, bob_email)
        stats.accept_friend = accept_friend_request_timed(temp1, bob_email)

        # Verify file permissions
        verify_file_access(bob_email, "poetry.md", "secret_poetry.md")

        # Session
        stats.poetry_session = start_session_timed(temp1, bob_email, "talk about poetry!")
        verify_transcript("Alice", temp1, bob_email)
        stats.alice_pull = pull_peer_context_timed(temp1, bob_email)
        verify_poetry(bob_email, "widening gyre")

        # Bob verifies transcript
        os.chdir(temp2)
        login("Bob", temp2)
        init_account_timed("Bob", temp2)
        start = time.time()
        sync_files_timed("Bob", temp2)
        verify_transcript("Bob", temp2, alice_email)
        stats.bob_transcript_verify = time.time() - start

        log("")
        log("=" * 50)
        log("PHASE 1 COMPLETE: Bob has received the transcript")
        log("=" * 50)

        # ==========================================
        # PHASE 2: Bob modifies poetry.md and adds philosophy.md
        # ==========================================
        log("")
        log("=" * 50)
        log("PHASE 2: File updates - modify poetry.md, add philosophy.md")
        log("=" * 50)

        # Bob updates files
        start = time.time()
        update_poetry_file(temp2)
        create_philosophy_file(temp2)
        update_authz_add_philosophy(temp2, bob_email, alice_email)
        stats.bob_file_update = time.time() - start
        timing("Bob file updates", stats.bob_file_update)

        # Bob syncs the changes
        stats.bob_sync_update = sync_files_timed("Bob", temp2)

        # Alice re-pulls and verifies
        os.chdir(temp1)
        login("Alice", temp1)
        init_account_timed("Alice", temp1)
        sync_files_timed("Alice", temp1)
        stats.alice_repull = pull_peer_context_timed(temp1, bob_email)

        # Verify the updated poetry.md
        poetry_updated = verify_poetry(bob_email, "springtime of mind")
        if not poetry_updated:
            raise RuntimeError("poetry.md was not updated!")

        # Verify the new philosophy.md
        philosophy_exists = verify_philosophy(bob_email)
        if not philosophy_exists:
            raise RuntimeError("philosophy.md was not found!")

        # ==========================================
        # Summary
        # ==========================================
        stats.total = time.time() - overall_start
        stats.print_summary()

        log("")
        log("=" * 50)
        log("TEST RESULTS")
        log("=" * 50)
        log(f"Alice: {alice_email}")
        log(f"Bob: {bob_email}")
        log(f"Poetry updated: {poetry_updated}")
        log(f"Philosophy added: {philosophy_exists}")
        log("=" * 50)
        log("File operations test PASSED!")

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
