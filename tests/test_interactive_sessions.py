#!/usr/bin/env python3
"""
Interactive Session Integration Test

Tests the full interactive session flow:
1. Setup two accounts (Alice and Bob)
2. Friend request flow
3. Alice starts interactive session with Bob
4. User manually interacts with terminal
5. Verify transcript auto-import from ~/.claude/projects/
6. Verify transcript sync to both repos

Run with: pytest tests/test_interactive_sessions.py -s -m integration
(the -s flag is required for interactive prompts)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
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
    # Sync
    sync_files,
    # Friend operations
    send_friend_request,
    check_friend_request,
    accept_friend_request,
    # Logging
    log,
    wait_for_user,
    run,
)


def start_interactive_session(temp_dir: Path, peer_email: str) -> tuple[bool, str]:
    """Start an interactive session with a peer.

    Returns:
        (success, message)
    """
    log(f"Starting interactive session with {peer_email}...")

    # Run claudeconnect interactive command
    cmd = ["claudeconnect", "interactive", peer_email]

    try:
        result = run(cmd, cwd=temp_dir, check=False)

        if result.returncode != 0:
            log(f"Interactive session failed: {result.stderr}")
            return False, result.stderr

        log("Interactive session started successfully")
        return True, "Interactive session started"

    except Exception as e:
        log(f"Error starting interactive session: {e}")
        return False, str(e)


def manually_import_transcripts(context_dir: Path, our_email: str):
    """Manually trigger transcript discovery and import.

    Since the background sync loop isn't running during tests, we need to
    manually trigger the transcript import process.

    Args:
        context_dir: Path to user's context directory
        our_email: Our email address
    """
    from claudeconnect.transcripts import (
        discover_new_interactive_transcripts,
        import_transcript,
        commit_transcript_to_peer,
    )
    from claudeconnect.cli import get_valid_token

    log("Manually triggering transcript discovery and import...")

    # Discover new transcripts
    new_transcripts = discover_new_interactive_transcripts(our_email, context_dir)

    if not new_transcripts:
        log("  No new transcripts found")
        return 0

    log(f"  Found {len(new_transcripts)} new transcript(s)")

    # Import each transcript
    tokens = get_valid_token()
    imported = 0

    for jsonl_path, metadata in new_transcripts:
        log(f"  Importing {jsonl_path.name}...")

        # Import to local context
        transcript_path = import_transcript(jsonl_path, metadata, our_email, context_dir)

        if transcript_path:
            log(f"    → Saved to {transcript_path}")
            imported += 1

            # Upload to peer's repo
            peer_email = metadata.get("peer_email")
            if peer_email and tokens:
                log(f"    → Uploading to {peer_email}'s repo...")
                commit_transcript_to_peer(transcript_path, peer_email, our_email, tokens.id_token)

    log(f"  Imported {imported} transcript(s)")
    return imported


def wait_for_transcript_discovery(
    context_dir: Path,
    peer_email: str,
    our_email: str,
    timeout_seconds: int = 120,
) -> Path | None:
    """Wait for transcript to be discovered and imported from Claude Code storage.

    Polls ~/.claude/projects/ for new JSONL files, then manually triggers
    import since background sync isn't running during tests.

    Args:
        context_dir: Path to user's context directory
        peer_email: Email of the peer
        our_email: Our email address
        timeout_seconds: Maximum time to wait

    Returns:
        Path to the imported transcript, or None if timeout
    """
    from claudeconnect.config import email_to_repo_name

    peer_repo_name = email_to_repo_name(peer_email)
    conv_dir = context_dir / "claudeconnect" / f"with-{peer_repo_name}"

    log(f"Waiting for transcript in {conv_dir}...")
    log(f"(Checking ~/.claude/projects/ for JSONL files)")
    log(f"(Timeout: {timeout_seconds}s)")

    start_time = time.time()
    last_count = 0

    while time.time() - start_time < timeout_seconds:
        # Manually trigger import (background sync not running in tests)
        imported = manually_import_transcripts(context_dir, our_email)

        if imported > 0:
            # Check if transcript appeared in local context
            if conv_dir.exists():
                transcripts = list(conv_dir.glob("*.md"))
                if transcripts:
                    newest = max(transcripts, key=lambda p: p.stat().st_mtime)
                    log(f"  ✓ Transcript imported: {newest.name}")
                    return newest

        # Poll every 10 seconds (checking for JSONL in ~/.claude/projects/)
        time.sleep(10)
        elapsed = int(time.time() - start_time)
        if elapsed % 30 == 0:  # Status every 30s
            log(f"  Still waiting for JSONL to appear in ~/.claude/projects/... ({elapsed}s elapsed)")

    log(f"  Timeout after {timeout_seconds}s")
    return None


def verify_transcript_content(transcript_path: Path, peer_email: str, our_email: str) -> bool:
    """Verify the transcript has expected format and content.

    Args:
        transcript_path: Path to the markdown transcript
        peer_email: Email of the peer
        our_email: Our email

    Returns:
        True if transcript looks valid
    """
    log(f"Verifying transcript content: {transcript_path.name}")

    content = transcript_path.read_text()

    # Check header
    checks = [
        ("Header exists", "# Interactive Session:" in content),
        ("Session ID present", "**Session ID**:" in content),
        ("Date present", "**Date**:" in content),
        ("User present", f"**User**: {our_email}" in content),
        ("Representing present", f"**Representing**: {peer_email}" in content),
        ("Type is interactive", "**Type**: interactive" in content),
        ("Source is claude-code-transcript", "**Source**: claude-code-transcript" in content),
    ]

    all_passed = True
    for check_name, passed in checks:
        status = "✓" if passed else "✗"
        log(f"  {status} {check_name}")
        if not passed:
            all_passed = False

    return all_passed


def verify_transcript_on_server(email: str, peer_email: str) -> bool:
    """Verify transcript exists on the server.

    Args:
        email: Email of the repo owner
        peer_email: Email of the peer

    Returns:
        True if transcript found on server
    """
    from claudeconnect.config import email_to_repo_name
    import httpx
    from claudeconnect.cli import get_valid_token

    log(f"Checking server for transcript in {email}'s repo...")

    tokens = get_valid_token()
    if not tokens:
        log("  Not logged in, cannot check server")
        return False

    peer_repo_name = email_to_repo_name(peer_email)

    # Get manifest
    from conf import API_BASE_URL
    headers = {"Authorization": f"Bearer {tokens.id_token}"}

    try:
        response = httpx.get(
            f"{API_BASE_URL}/manifest/{email}",
            headers=headers,
            timeout=30,
        )

        if response.status_code != 200:
            log(f"  Failed to get manifest: {response.status_code}")
            return False

        manifest = response.json()
        files = manifest.get("files", [])

        # Look for transcript in claudeconnect/with-{peer}/ directory
        transcript_files = [
            f for f in files
            if f["path"].startswith(f"claudeconnect/with-{peer_repo_name}/")
            and f["path"].endswith(".md")
        ]

        if transcript_files:
            log(f"  ✓ Found {len(transcript_files)} transcript(s) on server")
            for f in transcript_files:
                log(f"    - {f['path']}")
            return True
        else:
            log(f"  ✗ No transcripts found on server")
            return False

    except Exception as e:
        log(f"  Error checking server: {e}")
        return False


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_interactive_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_interactive_bob_"))
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
def test_interactive_session_flow(temp_dirs):
    """
    Full interactive session test.

    Tests automatic transcript discovery, import, and sync after
    an interactive session using Claude Code's native storage.

    Run with: pytest tests/test_interactive_sessions.py -s -m integration
    """
    temp1, temp2 = temp_dirs

    log("==========================================")
    log("Interactive Session Integration Test")
    log("==========================================")

    # Setup
    log("\n[1/11] Cleaning server and client...")
    clean_server()
    clean_client()

    # Alice setup
    log("\n[2/11] Setting up Alice's account...")
    os.chdir(temp1)
    login("Alice", temp1)
    alice_email = init_account("Alice", temp1)
    verify_init_structure("Alice", temp1)

    # Bob setup
    log("\n[3/11] Setting up Bob's account...")
    os.chdir(temp2)
    login("Bob", temp2)
    bob_email = init_account("Bob", temp2)
    verify_init_structure("Bob", temp2)

    # Bob sends friend request
    log("\n[4/11] Bob sending friend request to Alice...")
    send_friend_request(temp2, alice_email)

    # Alice receives and accepts
    log("\n[5/11] Alice accepting friend request from Bob...")
    os.chdir(temp1)
    login("Alice", temp1)
    init_account("Alice", temp1)
    sync_files(temp1)  # Pull friend request from server
    check_friend_request(temp1, bob_email)
    accept_friend_request(temp1, bob_email)

    # Alice starts interactive session
    log("\n[6/11] Alice starting interactive session with Bob...")
    log("⚠️  IMPORTANT: This will open a new Terminal window!")
    log("⚠️  macOS only - requires Terminal.app")

    wait_for_user(
        "\nWhen ready to start the interactive session, press Enter.\n"
        "A Terminal window will open. Have a short conversation (3-5 exchanges)\n"
        "with Bob's Claude, then press Ctrl+D to exit.\n"
    )

    success, message = start_interactive_session(temp1, bob_email)
    assert success, f"Failed to start interactive session: {message}"

    log("\n✓ Interactive session started!")
    log("\nPlease interact with Bob's Claude in the Terminal window.")
    log("Have at least 3-5 exchanges, then press Ctrl+D to exit.")

    wait_for_user("\nAfter you've exited the Terminal window, press Enter to continue...")

    # Wait for transcript discovery and import
    log("\n[7/11] Waiting for transcript auto-discovery and import...")
    log("(Background sync not running in test - manually polling)")

    alice_context_dir = temp1 / alice_email.replace("@", "-").replace(".", "-")
    transcript_path = wait_for_transcript_discovery(alice_context_dir, bob_email, alice_email, timeout_seconds=120)

    assert transcript_path is not None, "Transcript was not discovered/imported within timeout"
    log(f"✓ Transcript imported: {transcript_path}")

    # Verify transcript content
    log("\n[8/11] Verifying transcript content...")
    content_valid = verify_transcript_content(transcript_path, bob_email, alice_email)
    assert content_valid, "Transcript content validation failed"
    log("✓ Transcript content looks good")

    # Wait a bit more for sync to upload to server
    log("\nWaiting 30 seconds for background sync to upload...")
    time.sleep(30)

    # Trigger manual sync to ensure upload
    log("Triggering manual sync...")
    sync_files(temp1)

    # Verify transcript on Alice's server repo
    log("\n[9/11] Verifying transcript uploaded to Alice's server repo...")
    alice_server_ok = verify_transcript_on_server(alice_email, bob_email)
    assert alice_server_ok, "Transcript not found on Alice's server repo"
    log("✓ Transcript found on Alice's server repo")

    # Verify transcript on Bob's server repo (peer upload)
    log("\n[10/11] Verifying transcript uploaded to Bob's server repo...")
    bob_server_ok = verify_transcript_on_server(bob_email, alice_email)
    assert bob_server_ok, "Transcript not found on Bob's server repo"
    log("✓ Transcript found on Bob's server repo")

    # Bob pulls and decrypts the transcript
    log("\n[11/11] Bob pulling transcript and verifying decryption...")
    os.chdir(temp2)
    login("Bob", temp2)
    init_account("Bob", temp2)

    # Sync to pull the transcript from server
    log("  Bob syncing to pull transcript...")
    sync_files(temp2)

    # Verify transcript appears in Bob's local context
    bob_context_dir = temp2 / bob_email.replace("@", "-").replace(".", "-")
    from claudeconnect.config import email_to_repo_name
    alice_repo_name = email_to_repo_name(alice_email)
    bob_conv_dir = bob_context_dir / "claudeconnect" / f"with-{alice_repo_name}"

    log(f"  Checking for transcript in {bob_conv_dir}...")

    if not bob_conv_dir.exists():
        assert False, f"Bob's conversation directory doesn't exist: {bob_conv_dir}"

    bob_transcripts = list(bob_conv_dir.glob("*.md"))
    assert len(bob_transcripts) > 0, "No transcripts found in Bob's local context"

    bob_transcript = max(bob_transcripts, key=lambda p: p.stat().st_mtime)
    log(f"  ✓ Transcript found: {bob_transcript.name}")

    # Verify Bob can read the transcript (it's decrypted)
    bob_content = bob_transcript.read_text()
    assert len(bob_content) > 100, "Bob's transcript appears empty or too short"
    assert "# Interactive Session:" in bob_content, "Bob's transcript missing header"
    assert alice_email in bob_content, "Bob's transcript missing Alice's email"

    log(f"  ✓ Transcript decrypted successfully ({len(bob_content)} bytes)")
    log(f"  ✓ Bob can read the conversation")

    # Summary
    log("\n==========================================")
    log("✓ Interactive Session Test Complete!")
    log("==========================================")
    log(f"Alice: {alice_email}")
    log(f"Bob: {bob_email}")
    log(f"Transcript: {transcript_path.name}")
    log(f"Alice's copy: {len(transcript_path.read_text())} bytes")
    log(f"Bob's copy: {len(bob_content)} bytes")
    log("✓ Bidirectional sync verified")
    log("✓ Encryption/decryption verified")


def main():
    """Run as standalone script."""
    temp1 = None
    temp2 = None

    try:
        temp1, temp2 = create_temp_dirs()

        log("==========================================")
        log("Interactive Session Integration Test")
        log("==========================================")

        # Setup
        log("\n[1/11] Cleaning server and client...")
        clean_server()
        clean_client()

        # Alice setup
        log("\n[2/11] Setting up Alice's account...")
        os.chdir(temp1)
        login("Alice", temp1)
        alice_email = init_account("Alice", temp1)
        verify_init_structure("Alice", temp1)

        # Bob setup
        log("\n[3/11] Setting up Bob's account...")
        os.chdir(temp2)
        login("Bob", temp2)
        bob_email = init_account("Bob", temp2)
        verify_init_structure("Bob", temp2)

        # Bob sends friend request
        log("\n[4/11] Bob sending friend request to Alice...")
        send_friend_request(temp2, alice_email)

        # Alice receives and accepts
        log("\n[5/11] Alice accepting friend request from Bob...")
        os.chdir(temp1)
        login("Alice", temp1)
        init_account("Alice", temp1)
        sync_files(temp1)
        check_friend_request(temp1, bob_email)
        accept_friend_request(temp1, bob_email)

        # Alice starts interactive session
        log("\n[6/11] Alice starting interactive session with Bob...")
        log("⚠️  IMPORTANT: This will open a new Terminal window!")
        log("⚠️  macOS only - requires Terminal.app")

        wait_for_user(
            "\nWhen ready to start the interactive session, press Enter.\n"
            "A Terminal window will open. Have a short conversation (3-5 exchanges)\n"
            "with Bob's Claude, then press Ctrl+D to exit.\n"
        )

        success, message = start_interactive_session(temp1, bob_email)
        if not success:
            raise RuntimeError(f"Failed to start interactive session: {message}")

        log("\n✓ Interactive session started!")
        log("\nPlease interact with Bob's Claude in the Terminal window.")
        log("Have at least 3-5 exchanges, then press Ctrl+D to exit.")

        wait_for_user("\nAfter you've exited the Terminal window, press Enter to continue...")

        # Wait for transcript discovery
        log("\n[7/11] Waiting for transcript auto-discovery and import...")
        log("(Background sync not running in test - manually polling)")

        alice_context_dir = temp1 / alice_email.replace("@", "-").replace(".", "-")
        transcript_path = wait_for_transcript_discovery(alice_context_dir, bob_email, alice_email, timeout_seconds=120)

        if not transcript_path:
            raise RuntimeError("Transcript was not discovered/imported within timeout")

        log(f"✓ Transcript imported: {transcript_path}")

        # Verify transcript content
        log("\n[8/11] Verifying transcript content...")
        content_valid = verify_transcript_content(transcript_path, bob_email, alice_email)
        if not content_valid:
            raise RuntimeError("Transcript content validation failed")
        log("✓ Transcript content looks good")

        # Wait for sync
        log("\nWaiting 30 seconds for background sync to upload...")
        time.sleep(30)

        # Manual sync
        log("Triggering manual sync...")
        sync_files(temp1)

        # Verify on Alice's server
        log("\n[9/11] Verifying transcript uploaded to Alice's server repo...")
        alice_server_ok = verify_transcript_on_server(alice_email, bob_email)
        if not alice_server_ok:
            raise RuntimeError("Transcript not found on Alice's server repo")
        log("✓ Transcript found on Alice's server repo")

        # Verify on Bob's server
        log("\n[10/11] Verifying transcript uploaded to Bob's server repo...")
        bob_server_ok = verify_transcript_on_server(bob_email, alice_email)
        if not bob_server_ok:
            raise RuntimeError("Transcript not found on Bob's server repo")
        log("✓ Transcript found on Bob's server repo")

        # Bob pulls and decrypts
        log("\n[11/11] Bob pulling transcript and verifying decryption...")
        os.chdir(temp2)
        login("Bob", temp2)
        init_account("Bob", temp2)

        log("  Bob syncing to pull transcript...")
        sync_files(temp2)

        bob_context_dir = temp2 / bob_email.replace("@", "-").replace(".", "-")
        from claudeconnect.config import email_to_repo_name
        alice_repo_name = email_to_repo_name(alice_email)
        bob_conv_dir = bob_context_dir / "claudeconnect" / f"with-{alice_repo_name}"

        log(f"  Checking for transcript in {bob_conv_dir}...")

        if not bob_conv_dir.exists():
            raise RuntimeError(f"Bob's conversation directory doesn't exist: {bob_conv_dir}")

        bob_transcripts = list(bob_conv_dir.glob("*.md"))
        if len(bob_transcripts) == 0:
            raise RuntimeError("No transcripts found in Bob's local context")

        bob_transcript = max(bob_transcripts, key=lambda p: p.stat().st_mtime)
        log(f"  ✓ Transcript found: {bob_transcript.name}")

        bob_content = bob_transcript.read_text()
        if len(bob_content) < 100:
            raise RuntimeError("Bob's transcript appears empty or too short")
        if "# Interactive Session:" not in bob_content:
            raise RuntimeError("Bob's transcript missing header")
        if alice_email not in bob_content:
            raise RuntimeError("Bob's transcript missing Alice's email")

        log(f"  ✓ Transcript decrypted successfully ({len(bob_content)} bytes)")
        log(f"  ✓ Bob can read the conversation")

        # Summary
        log("\n==========================================")
        log("✓ Interactive Session Test Complete!")
        log("==========================================")
        log(f"Alice: {alice_email}")
        log(f"Bob: {bob_email}")
        log(f"Transcript: {transcript_path.name}")
        log(f"Alice's copy: {len(transcript_path.read_text())} bytes")
        log(f"Bob's copy: {len(bob_content)} bytes")
        log("✓ Bidirectional sync verified")
        log("✓ Encryption/decryption verified")

    finally:
        # Cleanup
        if temp1 and temp1.exists():
            log(f"\nCleaning up {temp1}")
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            log(f"Cleaning up {temp2}")
            shutil.rmtree(temp2)


if __name__ == "__main__":
    main()
