"""
Shared test utilities for ClaudeConnect integration tests.

This is the single source of truth for test fixtures and helpers.
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
from typing import Optional

from conf import ALICE_EMAIL, BOB_EMAIL, SERVER, SERVER_URL, API_BASE_URL, SSH_KEY_PATH

SSH_KEY = Path(SSH_KEY_PATH)
CC_CONFIG_DIR = Path.home() / ".claude-connect"
PEERS_DIR = CC_CONFIG_DIR / "peers"

# Encryption magic bytes
CCENC_MAGIC = b"CCENC"


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


def prompt(msg: str):
    print(f"{Colors.YELLOW}[ACTION REQUIRED]{Colors.RESET} {msg}")


def wait_for_user(msg: str):
    """Prompt user and wait for Enter."""
    prompt(msg)
    input("Press Enter to continue...")


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


def get_id_token() -> str:
    """Get the current id_token for API calls."""
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    with open(tokens_file) as f:
        return json.load(f)["id_token"]


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name format."""
    return email.replace("@", "-").replace(".", "-").lower()


# ===========================================
# Setup / Cleanup
# ===========================================

def clean_server():
    """Remove test account data on v2s server."""
    log("Cleaning test account data on v2s server...")
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


# ===========================================
# Account Operations
# ===========================================

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


def init_account_timed(account_name: str, temp_dir: Path) -> tuple[str, float]:
    """Initialize an account. Returns (email, time_taken)."""
    log(f"Initializing {account_name}...")
    wait_for_user(f"{account_name} logged in. Ready to init.")
    start = time.time()
    claudeconnect("init", cwd=temp_dir, input_text="y\n")
    elapsed = time.time() - start
    email = get_current_email()
    log(f"{account_name} initialized: {email}")
    timing(f"{account_name} init", elapsed)
    return email, elapsed


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


# ===========================================
# File Operations
# ===========================================

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


# ===========================================
# Authz Operations
# ===========================================

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


def update_authz_add_philosophy(temp_dir: Path, owner_email: str, friend_email: str):
    """Update authz to also allow friend access to philosophy.md."""
    log(f"Updating authz to allow {friend_email} access to philosophy.md...")

    authz_file = temp_dir / "authz"
    content = authz_file.read_text()

    new_rule = f"""
# Allow {friend_email} to read philosophy.md
[/philosophy.md]
{owner_email} = rw
{friend_email} = r
"""

    updated_content = content.rstrip() + "\n" + new_rule
    authz_file.write_text(updated_content)
    log("  Updated local authz")

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


# ===========================================
# Sync Operations
# ===========================================

def sync_files(temp_dir: Path):
    """Sync files with server."""
    log("Syncing files with server...")
    claudeconnect("sync", cwd=temp_dir)
    log("Sync complete.")


def sync_files_named(account_name: str, temp_dir: Path):
    """Sync files with server (with account name in log)."""
    log(f"Syncing {account_name}...")
    claudeconnect("sync", cwd=temp_dir)
    log(f"{account_name} synced.")


def sync_files_timed(account_name: str, temp_dir: Path) -> float:
    """Sync files with server. Returns time taken."""
    log(f"Syncing {account_name}...")
    start = time.time()
    claudeconnect("sync", cwd=temp_dir)
    elapsed = time.time() - start
    timing(f"{account_name} sync", elapsed)
    return elapsed


# ===========================================
# Friend Operations
# ===========================================

def send_friend_request(temp_dir: Path, to_email: str):
    """Send friend request."""
    log(f"Sending friend request to {to_email}...")
    claudeconnect("friend", to_email, cwd=temp_dir)
    log(f"Friend request sent.")


def send_friend_request_timed(temp_dir: Path, to_email: str) -> float:
    """Send friend request. Returns time taken."""
    log(f"Sending friend request to {to_email}...")
    start = time.time()
    claudeconnect("friend", to_email, cwd=temp_dir)
    elapsed = time.time() - start
    timing("Friend request sent", elapsed)
    return elapsed


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


def accept_friend_request_timed(temp_dir: Path, from_email: str) -> float:
    """Accept friend request. Returns time taken."""
    log(f"Accepting friend request from {from_email}...")
    start = time.time()
    claudeconnect("accept-friend", from_email, cwd=temp_dir)
    elapsed = time.time() - start
    timing("Friend accepted", elapsed)
    return elapsed


# ===========================================
# Session Operations
# ===========================================

def start_session(temp_dir: Path, peer_email: str, topic: str):
    """Start a session with peer (1 turn, output streamed live)."""
    log(f"Starting session about {topic}...")
    # Run session with live output (not captured)
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
    )


def start_session_timed(temp_dir: Path, peer_email: str, topic: str) -> float:
    """Start a session with peer. Returns time taken."""
    log(f"Starting session about {topic}...")
    start = time.time()
    subprocess.run(
        ["claudeconnect", "session", peer_email, "-t", topic, "--turns", "1"],
        cwd=temp_dir,
    )
    elapsed = time.time() - start
    timing("Session completed", elapsed)
    return elapsed


# ===========================================
# Verification
# ===========================================

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


def pull_peer_context(temp_dir: Path, peer_email: str):
    """Pull peer's context."""
    log(f"Pulling {peer_email}'s context...")
    claudeconnect("pull", peer_email, cwd=temp_dir)


def pull_peer_context_timed(temp_dir: Path, peer_email: str) -> float:
    """Pull peer's context. Returns time taken."""
    log(f"Pulling {peer_email}'s context...")
    start = time.time()
    claudeconnect("pull", peer_email, cwd=temp_dir)
    elapsed = time.time() - start
    timing(f"Pull {peer_email}", elapsed)
    return elapsed


def verify_poetry(peer_email: str, expected_content: str = "widening gyre") -> bool:
    """Verify poetry.md was pulled correctly."""
    log("Verifying poetry.md...")

    repo_name = email_to_repo_name(peer_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if not peer_poetry.exists():
        error(f"poetry.md not found: {peer_poetry}")
        return False

    content = peer_poetry.read_text()
    if expected_content in content:
        log(f"poetry.md verified - '{expected_content}' found!")
        return True
    else:
        error(f"poetry.md verification failed - '{expected_content}' not found")
        log(f"Content: {content[:200]}")
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
