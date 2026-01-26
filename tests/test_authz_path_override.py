#!/usr/bin/env python3
"""
Integration test for authz path override (Issue #82).

Tests that specific path sections override parent permissions:
- Bob has read access at `/` from Alice
- Alice has `/relationships` section with only owner access
- Bob should be DENIED access to `/relationships/*` files

Run with: pytest tests/test_authz_path_override.py -s -m integration
"""

from __future__ import annotations

import os
import tempfile
import shutil
from pathlib import Path

import pytest
import httpx

from conf import ALICE_EMAIL, BOB_EMAIL, API_BASE_URL
from test_utils import (
    clean_server,
    clean_client,
    log,
    error,
    wait_for_user,
    login,
    init_account,
    sync_files,
    send_friend_request,
    accept_friend_request,
    get_id_token,
    get_current_email,
)


def create_test_files(temp_dir: Path):
    """Create test files in Alice's context."""
    log("Creating test files...")

    # Public file at root - Bob should be able to read
    public_file = temp_dir / "public_notes.md"
    public_file.write_text(
        "# Public Notes\n\n"
        "This file is at the root level.\n"
        "Friends with read access on / should see this.\n"
    )
    log("  Created public_notes.md")

    # Private relationships directory - Bob should NOT be able to read
    relationships_dir = temp_dir / "relationships"
    relationships_dir.mkdir(exist_ok=True)

    private_file = relationships_dir / "romantic.md"
    private_file.write_text(
        "# Private Relationships\n\n"
        "This is private relationship info.\n"
        "Friends should NOT see this even if they have / access.\n"
    )
    log("  Created relationships/romantic.md")

    family_file = relationships_dir / "family.md"
    family_file.write_text(
        "# Family Info\n\n"
        "Private family details.\n"
    )
    log("  Created relationships/family.md")


def setup_authz_with_private_section(temp_dir: Path, alice_email: str, bob_email: str):
    """
    Set up Alice's authz with:
    - Bob has read access at /
    - /relationships section only grants owner access (implicit deny for Bob)
    """
    log("Setting up authz with private /relationships section...")

    authz_file = temp_dir / "authz"
    content = authz_file.read_text()

    # Add Bob's read access at / and private /relationships section
    new_rules = f"""
# Bob can read most context
[/]
{alice_email} = rw
{bob_email} = r

# Relationships are private - only owner
[/relationships]
{alice_email} = rw

# Conversations for ClaudeConnect
[/claudeconnect/conversations]
{alice_email} = rw
{bob_email} = rw
"""

    # Find end of public key line and replace everything after
    lines = content.split('\n')
    # Keep only the public key line
    public_key_line = lines[0] if lines[0].startswith('# Public') else None
    if not public_key_line:
        raise RuntimeError("Could not find public key line in authz")

    updated_content = public_key_line + "\n" + new_rules
    authz_file.write_text(updated_content)
    log("  Updated local authz")
    log("  Authz content:")
    for line in updated_content.split('\n'):
        log(f"    {line}")

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
            log(f"  ✓ Can access {path} (expected)")
            return True
        else:
            error(f"  ✗ Cannot access {path} (expected access, got {response.status_code})")
            return False
    else:
        if response.status_code == 403:
            log(f"  ✓ Denied access to {path} (expected - authz path override working!)")
            return True
        elif response.status_code == 200:
            error(f"  ✗ Can access {path} (BUG: expected denial due to /relationships section)")
            return False
        else:
            error(f"  ✗ Unexpected status {response.status_code} for {path}")
            return False


@pytest.fixture
def temp_dirs():
    """Create temp directories for Alice and Bob."""
    alice_temp = Path(tempfile.mkdtemp(prefix="cc_authz_override_alice_"))
    bob_temp = Path(tempfile.mkdtemp(prefix="cc_authz_override_bob_"))
    log(f"Created {alice_temp} (Alice)")
    log(f"Created {bob_temp} (Bob)")

    yield alice_temp, bob_temp

    if alice_temp.exists():
        log(f"Cleaning up {alice_temp}")
        shutil.rmtree(alice_temp)
    if bob_temp.exists():
        log(f"Cleaning up {bob_temp}")
        shutil.rmtree(bob_temp)


@pytest.mark.integration
def test_authz_path_override(temp_dirs):
    """
    Test that specific authz sections override parent permissions (Issue #82).

    Scenario:
    - Alice gives Bob read access at /
    - Alice has /relationships section with only owner access
    - Bob tries to read /relationships/romantic.md
    - Expected: Bob is DENIED (the specific section overrides / permission)

    Run with: pytest tests/test_authz_path_override.py -s -m integration
    """
    alice_temp, bob_temp = temp_dirs

    log("=" * 60)
    log("Issue #82: Authz Path Override Integration Test")
    log("=" * 60)

    # Step 1: Clean slate
    log("\n[1/8] Cleaning server and client...")
    clean_server()
    clean_client()

    # Step 2: Alice setup
    log("\n[2/8] Setting up Alice's account...")
    os.chdir(alice_temp)
    login("Alice", alice_temp)
    alice_email = init_account("Alice", alice_temp)

    # Step 3: Bob setup
    log("\n[3/8] Setting up Bob's account...")
    os.chdir(bob_temp)
    login("Bob", bob_temp)
    bob_email = init_account("Bob", bob_temp)

    # Step 4: Friend request flow
    log("\n[4/8] Friend request flow...")
    send_friend_request(bob_temp, alice_email)

    os.chdir(alice_temp)
    login("Alice", alice_temp)
    sync_files(alice_temp)
    accept_friend_request(alice_temp, bob_email)

    # Step 5: Alice creates files and sets up authz
    log("\n[5/8] Alice creating files and authz...")
    create_test_files(alice_temp)
    setup_authz_with_private_section(alice_temp, alice_email, bob_email)

    # Step 6: Alice syncs files to server
    log("\n[6/8] Alice syncing files to server...")
    sync_files(alice_temp)

    # Step 7: Switch to Bob and verify access
    log("\n[7/8] Switching to Bob to test access...")
    os.chdir(bob_temp)
    login("Bob", bob_temp)

    log("\nTesting Bob's access to Alice's files:")
    all_passed = True

    # Bob SHOULD be able to read root-level files
    log("\n  Files Bob SHOULD be able to access (covered by [/]):")
    if not verify_api_access(alice_email, "public_notes.md", should_succeed=True):
        all_passed = False

    # Bob should NOT be able to read /relationships files
    # This is the key test for Issue #82!
    log("\n  Files Bob should NOT access (covered by [/relationships]):")
    if not verify_api_access(alice_email, "relationships/romantic.md", should_succeed=False):
        all_passed = False
    if not verify_api_access(alice_email, "relationships/family.md", should_succeed=False):
        all_passed = False

    # Step 8: Results
    log("\n[8/8] Results...")
    log("=" * 60)
    if all_passed:
        log("✓ TEST PASSED: Authz path override working correctly!")
        log("  Bob was denied access to /relationships/* despite having / read access")
    else:
        error("✗ TEST FAILED: Authz path override NOT working!")
        error("  Bob could access files he shouldn't - Issue #82 regression")
    log("=" * 60)

    assert all_passed, "Authz path override test failed - see errors above"


def main():
    """Run as standalone script."""
    alice_temp = Path(tempfile.mkdtemp(prefix="cc_authz_override_alice_"))
    bob_temp = Path(tempfile.mkdtemp(prefix="cc_authz_override_bob_"))

    try:
        test_authz_path_override((alice_temp, bob_temp))
    finally:
        if alice_temp.exists():
            shutil.rmtree(alice_temp)
        if bob_temp.exists():
            shutil.rmtree(bob_temp)


if __name__ == "__main__":
    main()
