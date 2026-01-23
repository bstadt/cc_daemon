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

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from conf import ALICE_EMAIL, BOB_EMAIL
from test_utils import (
    # Setup/cleanup
    clean_server,
    clean_client,
    create_temp_dirs,
    # Account operations
    login,
    init_account,
    verify_init_structure,
    # File operations
    create_poetry_files,
    upload_file_to_server,
    # Authz
    update_authz_restrict_secret,
    # Sync
    sync_files,
    # Friend operations
    send_friend_request,
    check_friend_request,
    accept_friend_request,
    # Session
    start_session,
    # Verification
    verify_transcript,
    verify_file_access,
    verify_files_encrypted_on_server,
    verify_transcript_encrypted_on_server,
    pull_and_verify_poetry,
    # Logging
    log,
)


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

    # Alice setup
    login("Alice", temp1)
    alice_email = init_account("Alice", temp1)
    verify_init_structure("Alice", temp1)

    # Bob setup
    os.chdir(temp2)
    login("Bob", temp2)
    bob_email = init_account("Bob", temp2)
    verify_init_structure("Bob", temp2)

    # Bob creates poetry files and restricts access
    create_poetry_files(temp2)
    upload_file_to_server(bob_email, "poetry.md", (temp2 / "poetry.md").read_bytes())
    upload_file_to_server(bob_email, "secret_poetry.md", (temp2 / "secret_poetry.md").read_bytes())
    update_authz_restrict_secret(temp2, bob_email, alice_email)

    send_friend_request(temp2, alice_email)

    # Alice receives and accepts
    os.chdir(temp1)
    login("Alice", temp1)
    init_account("Alice", temp1)
    sync_files(temp1)  # Pull friend request from server
    check_friend_request(temp1, bob_email)
    accept_friend_request(temp1, bob_email)

    # Alice verifies file access permissions before session
    verify_file_access(bob_email, "poetry.md", "secret_poetry.md")

    # Session
    start_session(temp1, bob_email, "talk about poetry!")
    verify_transcript("Alice", temp1, bob_email)
    pull_and_verify_poetry(temp1, bob_email)

    # Bob verifies transcript
    os.chdir(temp2)
    login("Bob", temp2)
    init_account("Bob", temp2)
    success = verify_transcript("Bob", temp2, alice_email)

    # Summary
    print()
    log("==========================================")
    log("Integration test complete!")
    log("==========================================")
    log(f"Alice: {alice_email}")
    log(f"Bob: {bob_email}")

    assert success, "Transcript did not sync to Bob"


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # Alice setup
        login("Alice", temp1)
        alice_email = init_account("Alice", temp1)
        verify_init_structure("Alice", temp1)

        # Bob setup
        os.chdir(temp2)
        login("Bob", temp2)
        bob_email = init_account("Bob", temp2)
        verify_init_structure("Bob", temp2)

        # Bob creates poetry files and restricts access
        create_poetry_files(temp2)
        update_authz_restrict_secret(temp2, bob_email, alice_email)

        # Sync to upload files (with encryption)
        sync_files(temp2)

        # Verify files are encrypted on server
        verify_files_encrypted_on_server(bob_email, ["poetry.md", "secret_poetry.md"])

        send_friend_request(temp2, alice_email)

        # Alice receives and accepts
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)  # Pull friend request from server
        check_friend_request(temp1, bob_email)
        accept_friend_request(temp1, bob_email)

        # Alice verifies file access permissions
        verify_file_access(bob_email, "poetry.md", "secret_poetry.md")

        # Session
        start_session(temp1, bob_email, "poetry")
        verify_transcript("Alice", temp1, bob_email)

        # Verify Alice's transcript is encrypted on server
        verify_transcript_encrypted_on_server(alice_email, bob_email)

        pull_and_verify_poetry(temp1, bob_email)

        # Bob verifies transcript
        os.chdir(temp2)
        login("Bob", temp2)
        init_account("Bob", temp2)
        sync_files(temp2)  # Pull transcript that Alice uploaded to Bob's repo
        verify_transcript("Bob", temp2, alice_email)

        # Verify Bob's copy of transcript is also encrypted on server
        verify_transcript_encrypted_on_server(bob_email, alice_email)

        log("==========================================")
        log("Integration test complete!")
        log("==========================================")
        log(f"Alice: {alice_email}")
        log(f"Bob: {bob_email}")

    except Exception as e:
        from test_utils import error
        error(f"Test failed: {e}")
        raise
    finally:
        if temp1 and temp1.exists():
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            shutil.rmtree(temp2)


if __name__ == "__main__":
    main()
