#!/usr/bin/env python3
"""
ClaudeConnect Authz Sync Test

Tests the authz upload and validation flow against the new v2s HTTP API:
1. Purge test users from server
2. Login and init to create local authz file
3. Upload authz via HTTP API
4. Verify it parses (via /api/keys endpoint)
5. Make a malformed edit
6. Try to upload and expect failure

Run with: pytest tests/test_authz_sync.py -s
(the -s flag is required for interactive prompts)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

import pytest
import httpx

from conf import ALICE_EMAIL, BOB_EMAIL, SERVER, SERVER_URL, SSH_KEY_PATH

SSH_KEY = Path(SSH_KEY_PATH)
CC_CONFIG_DIR = Path.home() / ".claude-connect"

# Test accounts to purge
TEST_ACCOUNTS = [ALICE_EMAIL, BOB_EMAIL]


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


def get_id_token() -> str:
    """Get the current id_token for API calls."""
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    with open(tokens_file) as f:
        return json.load(f)["id_token"]


def wait_for_user(msg: str):
    """Prompt user and wait for Enter."""
    prompt(msg)
    input("Press Enter to continue...")


def clean_server():
    """Remove test account data on v2s server."""
    log("Cleaning test account data on v2s server...")
    for email in TEST_ACCOUNTS:
        # Sanitize email for filesystem path
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


def upload_file(user_email: str, path: str, content: bytes, token: str) -> httpx.Response:
    """Upload a file to user_email's storage on v2s server."""
    headers = {"Authorization": f"Bearer {token}"}
    response = httpx.put(
        f"{SERVER_URL}/api/files/{user_email}/{path}",
        content=content,
        headers=headers,
        timeout=30,
    )
    return response


def get_public_key(email: str) -> httpx.Response:
    """Get a user's public key from the server."""
    response = httpx.get(
        f"{SERVER_URL}/api/keys/{email}",
        timeout=30,
    )
    return response


@pytest.fixture
def temp_dir():
    """Create and cleanup temp directory."""
    temp = Path(tempfile.mkdtemp(prefix="cc_authz_test_"))
    log(f"Created temp dir: {temp}")
    yield temp
    if temp.exists():
        log(f"Cleaning up {temp}")
        shutil.rmtree(temp)


@pytest.mark.integration
def test_authz_sync(temp_dir):
    """
    Test authz upload and validation flow.

    Run with: pytest tests/test_authz_sync.py -s -m integration
    """
    # Step 1: Clean server and client
    log("=" * 50)
    log("Step 1: Cleaning server and client")
    log("=" * 50)
    clean_server()
    clean_client()

    # Step 2: Login and init
    log("=" * 50)
    log("Step 2: Login and init")
    log("=" * 50)
    os.chdir(temp_dir)
    wait_for_user("Please login with a test account")
    claudeconnect("login", cwd=temp_dir)

    email = get_current_email()
    log(f"Logged in as: {email}")

    wait_for_user("Ready to init")
    claudeconnect("init", cwd=temp_dir, input_text="y\n")

    # Verify authz was created locally
    authz_file = temp_dir / "authz"
    assert authz_file.exists(), "authz file not created by init"
    authz_content = authz_file.read_text()
    log(f"Local authz created ({len(authz_content)} bytes)")
    log("=== AUTHZ CONTENTS ===")
    log(authz_content)
    log("=== END AUTHZ ===")

    # Step 3: Upload authz via HTTP API
    log("=" * 50)
    log("Step 3: Upload authz via HTTP API")
    log("=" * 50)
    token = get_id_token()
    response = upload_file(email, "authz", authz_content.encode(), token)

    log(f"Upload response: {response.status_code}")
    if response.status_code != 200:
        error(f"Upload failed: {response.text}")
    assert response.status_code == 200, f"Upload failed: {response.text}"
    log(f"Upload successful: {response.json()}")

    # Step 4: Verify authz parses via /api/keys
    log("=" * 50)
    log("Step 4: Verify authz parses via /api/keys")
    log("=" * 50)
    response = get_public_key(email)

    log(f"Public key response: {response.status_code}")
    assert response.status_code == 200, f"Failed to get public key: {response.text}"

    key_data = response.json()
    log(f"Public key retrieved: {key_data['public_key'][:16]}...")
    assert key_data["user"] == email
    assert len(key_data["public_key"]) == 64, "Public key should be 64 hex chars"

    # Step 5: Make a malformed edit to authz
    log("=" * 50)
    log("Step 5: Make malformed edit to authz")
    log("=" * 50)

    # Remove the public key line (first line)
    lines = authz_content.split("\n")
    malformed_content = "\n".join(lines[1:])  # Skip first line
    log("Malformed authz (removed public key line):")
    log(f"  First line now: {malformed_content.split(chr(10))[0][:50]}...")

    # Step 6: Try to upload malformed authz - should fail
    log("=" * 50)
    log("Step 6: Upload malformed authz (expect failure)")
    log("=" * 50)
    response = upload_file(email, "authz", malformed_content.encode(), token)

    log(f"Malformed upload response: {response.status_code}")
    log(f"Error message: {response.text}")

    assert response.status_code == 400, f"Expected 400 for malformed authz, got {response.status_code}"
    assert "Invalid authz" in response.text or "Public key" in response.text, \
        f"Expected error about invalid authz, got: {response.text}"

    log("=" * 50)
    log("SUCCESS: Malformed authz was rejected!")
    log("=" * 50)

    # Verify original authz is still intact
    log("Verifying original authz is still valid...")
    response = get_public_key(email)
    assert response.status_code == 200, "Original authz should still be valid"
    log("Original authz still valid!")

    log("=" * 50)
    log("TEST PASSED!")
    log("=" * 50)


def main():
    """Run as standalone script."""
    temp = Path(tempfile.mkdtemp(prefix="cc_authz_test_"))
    log(f"Created temp dir: {temp}")
    try:
        test_authz_sync(temp)
    finally:
        if temp.exists():
            log(f"Cleaning up {temp}")
            shutil.rmtree(temp)


if __name__ == "__main__":
    main()
