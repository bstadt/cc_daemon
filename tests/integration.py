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
import httpx

from conf import ALICE_EMAIL, BOB_EMAIL, SERVER, SERVER_URL, API_BASE_URL, SSH_KEY_PATH

SSH_KEY = Path(SSH_KEY_PATH)
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
    """Remove test account data on v2s server."""
    log("Cleaning test account data on v2s server...")
    # Sanitize emails for filesystem paths
    test_accounts = [ALICE_EMAIL, BOB_EMAIL]
    for email in test_accounts:
        safe_email = email.replace("/", "_").replace("\\", "_")
        ssh_server(f"sudo rm -rf /data/users/{safe_email}")
        log(f"  Removed /data/users/{safe_email}")
    log("Test account data cleaned.")


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


def create_poetry_files(temp_dir: Path):
    """Create poetry.md and secret_poetry.md in context."""
    log("Creating poetry.md...")
    poetry_file = temp_dir / "poetry.md"
    poetry_file.write_text("# Poetry Collection\n\nturning and turning in the widening gyre\n")
    log("Created poetry.md")

    log("Creating secret_poetry.md...")
    secret_file = temp_dir / "secret_poetry.md"
    secret_file.write_text("# Secret Poetry\n\ntheres a bluebird in my heart that wants to get out but im too tough for him, i say stay in there, im not going to let anybody see you.\n")
    log("Created secret_poetry.md")


def get_id_token() -> str:
    """Get the current id_token for API calls."""
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    with open(tokens_file) as f:
        return json.load(f)["id_token"]


def update_authz_restrict_secret(temp_dir: Path, owner_email: str, friend_email: str):
    """
    Update authz to allow friend access to poetry.md but NOT secret_poetry.md.

    Adds:
    - [/poetry.md] friend = r
    - [/secret_poetry.md] (owner only, no friend access)
    """
    log(f"Updating authz to restrict secret_poetry.md from {friend_email}...")

    authz_file = temp_dir / "authz"
    content = authz_file.read_text()

    # Add permission rules
    new_rules = f"""
# Allow {friend_email} to read poetry.md
[/poetry.md]
{owner_email} = rw
{friend_email} = r

# Restrict secret_poetry.md to owner only
[/secret_poetry.md]
{owner_email} = rw
"""

    updated_content = content.rstrip() + "\n" + new_rules
    authz_file.write_text(updated_content)
    log("  Updated local authz")

    # Upload updated authz to server
    token = get_id_token()
    response = httpx.put(
        f"{API_BASE_URL}/files/{owner_email}/authz",
        content=updated_content.encode(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 200:
        log("  Uploaded authz to server")
    else:
        raise RuntimeError(f"Failed to upload authz: {response.text}")


def upload_file_to_server(owner_email: str, path: str, content: bytes):
    """Upload a file to the server."""
    token = get_id_token()
    response = httpx.put(
        f"{API_BASE_URL}/files/{owner_email}/{path}",
        content=content,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 200:
        log(f"  Uploaded {path} to server")
    else:
        raise RuntimeError(f"Failed to upload {path}: {response.text}")


def verify_file_access(owner_email: str, accessible_path: str, restricted_path: str):
    """
    Verify that current user can access accessible_path but NOT restricted_path.
    """
    token = get_id_token()
    current_email = get_current_email()

    log(f"Verifying file access permissions for {current_email}...")

    # Should be able to read accessible_path
    log(f"  Testing access to {accessible_path}...")
    response = httpx.get(
        f"{API_BASE_URL}/files/{owner_email}/{accessible_path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert response.status_code == 200, f"Should have access to {accessible_path}, got {response.status_code}: {response.text}"
    log(f"    ✓ Can read {accessible_path}")

    # Should NOT be able to read restricted_path
    log(f"  Testing access to {restricted_path}...")
    response = httpx.get(
        f"{API_BASE_URL}/files/{owner_email}/{restricted_path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert response.status_code == 403, f"Should NOT have access to {restricted_path}, got {response.status_code}"
    log(f"    ✓ Correctly denied access to {restricted_path}")

    log("File access permissions verified!")


# Encryption magic bytes
CCENC_MAGIC = b"CCENC"


def verify_file_encrypted_on_server(owner_email: str, path: str):
    """
    Verify that a file stored on the server is encrypted.

    Fetches the raw file bytes and checks for CCENC magic header.
    """
    token = get_id_token()

    log(f"Verifying {path} is encrypted on server...")
    response = httpx.get(
        f"{API_BASE_URL}/files/{owner_email}/{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert response.status_code == 200, f"Failed to fetch {path}: {response.status_code}"

    content = response.content
    assert content[:5] == CCENC_MAGIC, f"File {path} is NOT encrypted! First 5 bytes: {content[:5]}"
    log(f"  ✓ {path} is encrypted (CCENC header found)")


def verify_files_encrypted_on_server(owner_email: str, paths: list[str]):
    """Verify multiple files are encrypted on the server."""
    log(f"Verifying encryption for {len(paths)} file(s) on server...")
    for path in paths:
        verify_file_encrypted_on_server(owner_email, path)
    log("All files verified as encrypted!")


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


def sync_files(temp_dir: Path):
    """Sync files with server."""
    log("Syncing files with server...")
    claudeconnect("sync", cwd=temp_dir)
    log("Sync complete.")


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


def verify_transcript_encrypted_on_server(owner_email: str, peer_email: str):
    """
    Verify that transcripts for a peer are encrypted on the server.

    Fetches files from claudeconnect/with-{peer}/ and checks they're encrypted.
    """
    token = get_id_token()
    peer_dir_name = f"with-{email_to_repo_name(peer_email)}"

    log(f"Verifying transcript encryption for {owner_email}'s conversations with {peer_email}...")

    # Get manifest to find transcript files
    response = httpx.get(
        f"{API_BASE_URL}/manifest/{owner_email}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    assert response.status_code == 200, f"Failed to get manifest: {response.status_code}"

    manifest = response.json()
    transcript_paths = [
        f["path"] for f in manifest.get("files", [])
        if f["path"].startswith(f"claudeconnect/{peer_dir_name}/")
        and f["path"].endswith(".md")
        and "friend-request" not in f["path"]
    ]

    if not transcript_paths:
        error(f"No transcripts found on server for {owner_email} with {peer_email}")
        return

    for path in transcript_paths:
        verify_file_encrypted_on_server(owner_email, path)

    log(f"All {len(transcript_paths)} transcript(s) verified as encrypted!")


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
        error(f"Test failed: {e}")
        raise
    finally:
        if temp1 and temp1.exists():
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            shutil.rmtree(temp2)


if __name__ == "__main__":
    main()
