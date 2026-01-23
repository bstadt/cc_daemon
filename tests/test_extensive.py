#!/usr/bin/env python3
"""
ClaudeConnect Extensive Integration Test

Comprehensive test covering:
1. Base flow (login, init, friend request, session)
2. File updates (modify existing, add new)
3. Alice adds files and shares with Bob
4. Authz modification (Bob grants access to previously restricted file)
5. File deletion (Bob deletes a file)

At every step, verify system state is as expected.

Run with: pytest tests/test_extensive.py -s -m integration
Or standalone: python tests/test_extensive.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import httpx
from pathlib import Path
from dataclasses import dataclass, field

import pytest

from conf import ALICE_EMAIL, BOB_EMAIL, API_BASE_URL
from test_utils import (
    # Setup/cleanup
    clean_server,
    clean_client,
    create_temp_dirs,
    CC_CONFIG_DIR,
    PEERS_DIR,
    # Account operations
    login,
    init_account,
    verify_init_structure,
    get_id_token,
    get_current_email,
    email_to_repo_name,
    # File operations
    create_poetry_files,
    update_poetry_file,
    create_philosophy_file,
    # Authz
    update_authz_restrict_secret,
    update_authz_add_philosophy,
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
    verify_poetry,
    verify_philosophy,
    pull_peer_context,
    # Logging
    log,
    warn,
    error,
    timing,
)


@dataclass
class TestState:
    """Track expected state throughout the test."""
    # Bob's files that Alice should be able to access
    bob_accessible_to_alice: list[str] = field(default_factory=list)
    # Bob's files that Alice should NOT be able to access
    bob_restricted_from_alice: list[str] = field(default_factory=list)
    # Alice's files that Bob should be able to access
    alice_accessible_to_bob: list[str] = field(default_factory=list)
    # Alice's files that Bob should NOT be able to access
    alice_restricted_from_bob: list[str] = field(default_factory=list)
    # Files that should exist in Alice's peer cache for Bob
    alice_has_from_bob: list[str] = field(default_factory=list)
    # Files that should exist in Bob's peer cache for Alice
    bob_has_from_alice: list[str] = field(default_factory=list)


def verify_api_access(owner_email: str, path: str, should_succeed: bool) -> bool:
    """Verify API access to a file. Returns True if access matches expectation."""
    token = get_id_token()
    response = httpx.get(
        f"{API_BASE_URL}/files/{owner_email}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    if should_succeed:
        if response.status_code == 200:
            log(f"    ✓ Can access {path} (expected)")
            return True
        else:
            error(f"    ✗ Cannot access {path} (expected access, got {response.status_code})")
            return False
    else:
        if response.status_code == 403:
            log(f"    ✓ Denied access to {path} (expected)")
            return True
        elif response.status_code == 200:
            error(f"    ✗ Can access {path} (expected denial)")
            return False
        else:
            error(f"    ✗ Unexpected status {response.status_code} for {path}")
            return False


def verify_peer_file_exists(peer_email: str, filename: str) -> bool:
    """Verify a file exists in the peer cache."""
    repo_name = email_to_repo_name(peer_email)
    file_path = PEERS_DIR / repo_name / filename

    if file_path.exists():
        log(f"    ✓ {filename} exists in peer cache")
        return True
    else:
        error(f"    ✗ {filename} NOT found in peer cache at {file_path}")
        return False


def verify_peer_file_missing(peer_email: str, filename: str) -> bool:
    """Verify a file does NOT exist in the peer cache."""
    repo_name = email_to_repo_name(peer_email)
    file_path = PEERS_DIR / repo_name / filename

    if not file_path.exists():
        log(f"    ✓ {filename} correctly absent from peer cache")
        return True
    else:
        error(f"    ✗ {filename} should NOT exist but found at {file_path}")
        return False


def verify_state(state: TestState, alice_email: str, bob_email: str, perspective: str):
    """
    Verify the current state matches expectations.

    perspective: "alice" or "bob" - who is currently logged in
    """
    log(f"Verifying state from {perspective}'s perspective...")
    all_passed = True

    if perspective == "alice":
        # Alice checking access to Bob's files
        log(f"  Checking Alice's access to Bob's files...")
        for path in state.bob_accessible_to_alice:
            if not verify_api_access(bob_email, path, should_succeed=True):
                all_passed = False
        for path in state.bob_restricted_from_alice:
            if not verify_api_access(bob_email, path, should_succeed=False):
                all_passed = False

        # Alice checking her peer cache for Bob's files
        log(f"  Checking Alice's peer cache for Bob's files...")
        for filename in state.alice_has_from_bob:
            if not verify_peer_file_exists(bob_email, filename):
                all_passed = False

    elif perspective == "bob":
        # Bob checking access to Alice's files
        log(f"  Checking Bob's access to Alice's files...")
        for path in state.alice_accessible_to_bob:
            if not verify_api_access(alice_email, path, should_succeed=True):
                all_passed = False
        for path in state.alice_restricted_from_bob:
            if not verify_api_access(alice_email, path, should_succeed=False):
                all_passed = False

        # Bob checking his peer cache for Alice's files
        log(f"  Checking Bob's peer cache for Alice's files...")
        for filename in state.bob_has_from_alice:
            if not verify_peer_file_exists(alice_email, filename):
                all_passed = False

    if all_passed:
        log(f"State verification PASSED for {perspective}")
    else:
        error(f"State verification FAILED for {perspective}")

    return all_passed


def create_alice_files(temp_dir: Path):
    """Create Alice's files: notes.md (shareable) and projects.md (private)."""
    log("Creating Alice's files...")

    notes_file = temp_dir / "notes.md"
    notes_file.write_text(
        "# Alice's Notes\n\n"
        "These are my shared notes that friends can read.\n"
        "Contains thoughts on poetry and philosophy discussions.\n"
    )
    log("  Created notes.md")

    projects_file = temp_dir / "projects.md"
    projects_file.write_text(
        "# Alice's Private Projects\n\n"
        "SECRET: Working on a surprise project.\n"
        "This should not be visible to friends.\n"
    )
    log("  Created projects.md")


def setup_alice_authz(temp_dir: Path, alice_email: str, bob_email: str):
    """Set up Alice's authz to share notes.md but not projects.md."""
    log(f"Setting up Alice's authz...")

    authz_file = temp_dir / "authz"
    content = authz_file.read_text()

    new_rules = f"""
# Allow {bob_email} to read notes.md
[/notes.md]
{alice_email} = rw
{bob_email} = r

# Restrict projects.md to Alice only
[/projects.md]
{alice_email} = rw
"""

    updated_content = content.rstrip() + "\n" + new_rules
    authz_file.write_text(updated_content)
    log("  Updated local authz")

    # Upload to server
    token = get_id_token()
    response = httpx.put(
        f"{API_BASE_URL}/files/{alice_email}/authz",
        content=updated_content.encode(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 200:
        log("  Uploaded authz to server")
    else:
        raise RuntimeError(f"Failed to upload authz: {response.text}")


def update_bob_authz_grant_secret(temp_dir: Path, bob_email: str, alice_email: str):
    """Update Bob's authz to NOW grant Alice access to secret_poetry.md."""
    log(f"Updating Bob's authz to grant access to secret_poetry.md...")

    authz_file = temp_dir / "authz"
    content = authz_file.read_text()

    # Replace the restrictive rule with a permissive one
    # Find and replace the secret_poetry section
    lines = content.split('\n')
    new_lines = []
    skip_until_next_section = False

    for line in lines:
        if '[/secret_poetry.md]' in line:
            # Replace this section
            new_lines.append(f"# Now allowing {alice_email} to read secret_poetry.md")
            new_lines.append("[/secret_poetry.md]")
            new_lines.append(f"{bob_email} = rw")
            new_lines.append(f"{alice_email} = r")
            skip_until_next_section = True
            continue

        if skip_until_next_section:
            if line.startswith('[') or line.strip() == '':
                skip_until_next_section = False
                if line.strip():
                    new_lines.append(line)
            continue

        new_lines.append(line)

    updated_content = '\n'.join(new_lines)
    authz_file.write_text(updated_content)
    log("  Updated local authz")

    # Upload to server
    token = get_id_token()
    response = httpx.put(
        f"{API_BASE_URL}/files/{bob_email}/authz",
        content=updated_content.encode(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 200:
        log("  Uploaded authz to server")
    else:
        raise RuntimeError(f"Failed to upload authz: {response.text}")


def delete_file_local(temp_dir: Path, filename: str):
    """Delete a file from the local context directory."""
    file_path = temp_dir / filename
    if file_path.exists():
        file_path.unlink()
        log(f"  Deleted local {filename}")
    else:
        warn(f"  {filename} not found locally")


def delete_file_from_server(owner_email: str, path: str):
    """Delete a file from the server."""
    token = get_id_token()
    response = httpx.delete(
        f"{API_BASE_URL}/files/{owner_email}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 200:
        log(f"  Deleted {path} from server")
    else:
        raise RuntimeError(f"Failed to delete {path}: {response.text}")


def main():
    """Run the extensive integration test."""
    temp1 = None  # Alice
    temp2 = None  # Bob
    state = TestState()

    try:
        clean_server()
        clean_client()
        temp1, temp2 = create_temp_dirs()

        # ==========================================
        # PHASE 1: Base setup
        # ==========================================
        log("")
        log("=" * 60)
        log("PHASE 1: Base setup (login, init, friend request, session)")
        log("=" * 60)

        # Alice setup
        login("Alice", temp1)
        alice_email = init_account("Alice", temp1)
        verify_init_structure("Alice", temp1)

        # Bob setup
        os.chdir(temp2)
        login("Bob", temp2)
        bob_email = init_account("Bob", temp2)
        verify_init_structure("Bob", temp2)

        # Bob creates poetry files
        create_poetry_files(temp2)
        update_authz_restrict_secret(temp2, bob_email, alice_email)
        sync_files(temp2)

        # Update expected state
        state.bob_accessible_to_alice = ["poetry.md"]
        state.bob_restricted_from_alice = ["secret_poetry.md"]

        send_friend_request(temp2, alice_email)

        # Alice accepts friend request
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)
        check_friend_request(temp1, bob_email)
        accept_friend_request(temp1, bob_email)

        # Verify state after friend acceptance
        log("")
        log("--- Verifying state after friend acceptance ---")
        assert verify_state(state, alice_email, bob_email, "alice"), "State verification failed"

        # Session
        start_session(temp1, bob_email, "talk about poetry!")
        assert verify_transcript("Alice", temp1, bob_email), "Alice transcript not found"

        # Pull Bob's context
        pull_peer_context(temp1, bob_email)
        state.alice_has_from_bob = ["poetry.md"]

        log("")
        log("--- Verifying state after pull ---")
        assert verify_state(state, alice_email, bob_email, "alice"), "State verification failed"

        # Bob verifies transcript
        os.chdir(temp2)
        login("Bob", temp2)
        init_account("Bob", temp2)
        sync_files(temp2)
        assert verify_transcript("Bob", temp2, alice_email), "Bob transcript not found"

        log("")
        log("PHASE 1 COMPLETE")

        # ==========================================
        # PHASE 2: Bob updates files
        # ==========================================
        log("")
        log("=" * 60)
        log("PHASE 2: Bob modifies poetry.md and adds philosophy.md")
        log("=" * 60)

        # Bob is still logged in
        update_poetry_file(temp2)
        create_philosophy_file(temp2)
        update_authz_add_philosophy(temp2, bob_email, alice_email)
        sync_files(temp2)

        # Update expected state
        state.bob_accessible_to_alice = ["poetry.md", "philosophy.md"]

        # Alice re-pulls and verifies
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)
        pull_peer_context(temp1, bob_email)

        state.alice_has_from_bob = ["poetry.md", "philosophy.md"]

        log("")
        log("--- Verifying state after Bob's file updates ---")
        assert verify_state(state, alice_email, bob_email, "alice"), "State verification failed"
        assert verify_poetry(bob_email, "springtime of mind"), "Poetry update verification failed"
        assert verify_philosophy(bob_email), "Philosophy verification failed"

        log("")
        log("PHASE 2 COMPLETE")

        # ==========================================
        # PHASE 3: Alice adds files
        # ==========================================
        log("")
        log("=" * 60)
        log("PHASE 3: Alice creates files and shares with Bob")
        log("=" * 60)

        # Alice is still logged in
        create_alice_files(temp1)
        setup_alice_authz(temp1, alice_email, bob_email)
        sync_files(temp1)

        # Update expected state
        state.alice_accessible_to_bob = ["notes.md"]
        state.alice_restricted_from_bob = ["projects.md"]

        # Bob pulls Alice's context and verifies
        os.chdir(temp2)
        login("Bob", temp2)
        init_account("Bob", temp2)
        sync_files(temp2)
        pull_peer_context(temp2, alice_email)

        state.bob_has_from_alice = ["notes.md"]

        log("")
        log("--- Verifying state after Alice's file additions ---")
        assert verify_state(state, alice_email, bob_email, "bob"), "State verification failed"

        # Verify Bob can read notes.md content
        repo_name = email_to_repo_name(alice_email)
        notes_path = PEERS_DIR / repo_name / "notes.md"
        assert notes_path.exists(), "notes.md not found in Bob's peer cache"
        content = notes_path.read_text()
        assert "shared notes" in content, "notes.md content verification failed"
        log("  ✓ notes.md content verified")

        log("")
        log("PHASE 3 COMPLETE")

        # ==========================================
        # PHASE 4: Bob modifies authz to grant secret access
        # ==========================================
        log("")
        log("=" * 60)
        log("PHASE 4: Bob grants Alice access to secret_poetry.md")
        log("=" * 60)

        # Bob is still logged in
        update_bob_authz_grant_secret(temp2, bob_email, alice_email)
        sync_files(temp2)

        # Update expected state - secret_poetry is now accessible
        state.bob_accessible_to_alice = ["poetry.md", "philosophy.md", "secret_poetry.md"]
        state.bob_restricted_from_alice = []

        # Alice re-pulls and verifies
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)
        pull_peer_context(temp1, bob_email)

        state.alice_has_from_bob = ["poetry.md", "philosophy.md", "secret_poetry.md"]

        log("")
        log("--- Verifying state after authz modification ---")
        assert verify_state(state, alice_email, bob_email, "alice"), "State verification failed"

        # Verify Alice can now read secret_poetry.md
        repo_name = email_to_repo_name(bob_email)
        secret_path = PEERS_DIR / repo_name / "secret_poetry.md"
        assert secret_path.exists(), "secret_poetry.md not found after authz change"
        content = secret_path.read_text()
        assert "bluebird" in content, "secret_poetry.md content verification failed"
        log("  ✓ secret_poetry.md content verified (bluebird found)")

        log("")
        log("PHASE 4 COMPLETE")

        # ==========================================
        # PHASE 5: Bob deletes philosophy.md
        # ==========================================
        log("")
        log("=" * 60)
        log("PHASE 5: Bob deletes philosophy.md")
        log("=" * 60)

        os.chdir(temp2)
        login("Bob", temp2)
        init_account("Bob", temp2)

        # Delete locally and from server
        delete_file_local(temp2, "philosophy.md")
        delete_file_from_server(bob_email, "philosophy.md")
        sync_files(temp2)

        # Update expected state
        state.bob_accessible_to_alice = ["poetry.md", "secret_poetry.md"]

        # Alice re-pulls and verifies philosophy.md is gone
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)
        pull_peer_context(temp1, bob_email)

        # Note: The file might still be in Alice's cache until a full re-sync
        # But API access should fail
        log("")
        log("--- Verifying state after file deletion ---")

        # Verify API access fails for deleted file
        token = get_id_token()
        response = httpx.get(
            f"{API_BASE_URL}/files/{bob_email}/philosophy.md",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert response.status_code == 404, f"philosophy.md should be 404, got {response.status_code}"
        log("  ✓ philosophy.md returns 404 (correctly deleted)")

        # Verify other files still accessible
        assert verify_api_access(bob_email, "poetry.md", should_succeed=True), "poetry.md should still be accessible"
        assert verify_api_access(bob_email, "secret_poetry.md", should_succeed=True), "secret_poetry.md should still be accessible"

        log("")
        log("PHASE 5 COMPLETE")

        # ==========================================
        # Final Summary
        # ==========================================
        log("")
        log("=" * 60)
        log("EXTENSIVE INTEGRATION TEST COMPLETE")
        log("=" * 60)
        log(f"Alice: {alice_email}")
        log(f"Bob: {bob_email}")
        log("")
        log("All phases passed:")
        log("  ✓ Phase 1: Base setup (login, init, friend, session)")
        log("  ✓ Phase 2: File updates (modify + add)")
        log("  ✓ Phase 3: Alice adds files with authz")
        log("  ✓ Phase 4: Authz modification (grant secret access)")
        log("  ✓ Phase 5: File deletion")
        log("")
        log("TEST PASSED!")

    except Exception as e:
        error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        if temp1 and temp1.exists():
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            shutil.rmtree(temp2)


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
def test_extensive_flow(temp_dirs):
    """
    Extensive integration test for ClaudeConnect.

    Run with: pytest tests/test_extensive.py -s -m integration
    """
    # For pytest, we just call main() which handles everything
    # This is a simplified approach - in production you'd refactor
    # to use the temp_dirs fixture properly
    main()


if __name__ == "__main__":
    main()
