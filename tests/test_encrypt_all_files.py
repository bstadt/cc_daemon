#!/usr/bin/env python3
"""
Integration test for encrypting all file types (Issue #98).

Verifies that ALL files (not just .md) are encrypted on the server,
except for files explicitly in PLAINTEXT_FILES (authz, .keep).

Run with: pytest tests/test_encrypt_all_files.py -s -m integration
"""

from __future__ import annotations

import os
import tempfile
import shutil
import subprocess
from pathlib import Path

import pytest

from conf import ALICE_EMAIL, SERVER, SSH_KEY_PATH
from test_utils import (
    clean_server,
    clean_client,
    log,
    error,
    login,
    init_account,
    sync_files,
    get_id_token,
    get_current_email,
    CCENC_MAGIC,
)


# Default SSH key path - can be overridden via environment variable
DEFAULT_SSH_KEY = os.environ.get("CC_SSH_KEY", SSH_KEY_PATH)


def ssh_read_file_bytes(ssh_key: str, user_email: str, path: str, server: str = SERVER) -> bytes:
    """
    SSH into the server and read raw bytes of a file.

    Args:
        ssh_key: Path to SSH private key
        user_email: User email (used to find data directory)
        path: Path relative to user's files directory
        server: Server hostname

    Returns:
        Raw bytes of the file
    """
    # Sanitize email for filesystem
    safe_email = user_email.replace("/", "_").replace("\\", "_")
    server_path = f"/data/users/{safe_email}/files/{path}"

    result = subprocess.run(
        ["ssh", "-i", ssh_key, f"ubuntu@{server}", f"cat {server_path}"],
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"SSH cat failed for {server_path}: {result.stderr.decode()}")

    return result.stdout


def verify_file_encrypted_via_ssh(ssh_key: str, user_email: str, path: str) -> bool:
    """
    Verify a file is encrypted on the server by checking for CCENC magic header.

    Returns True if file is encrypted, False otherwise.
    """
    try:
        raw_bytes = ssh_read_file_bytes(ssh_key, user_email, path)
        is_encrypted = raw_bytes[:5] == CCENC_MAGIC
        if is_encrypted:
            log(f"  ✓ {path} is encrypted (CCENC header found)")
        else:
            error(f"  ✗ {path} is NOT encrypted! First bytes: {raw_bytes[:20]}")
        return is_encrypted
    except Exception as e:
        error(f"  ✗ Failed to check {path}: {e}")
        return False


def verify_file_plaintext_via_ssh(ssh_key: str, user_email: str, path: str, expected_content: bytes = None) -> bool:
    """
    Verify a file is NOT encrypted (plaintext) on the server.

    Returns True if file is plaintext, False if encrypted.
    """
    try:
        raw_bytes = ssh_read_file_bytes(ssh_key, user_email, path)
        is_plaintext = raw_bytes[:5] != CCENC_MAGIC
        if is_plaintext:
            log(f"  ✓ {path} is plaintext (as expected)")
            if expected_content and not raw_bytes.startswith(expected_content[:20]):
                error(f"  ✗ {path} content doesn't match expected")
                return False
        else:
            error(f"  ✗ {path} is encrypted (should be plaintext)")
        return is_plaintext
    except Exception as e:
        error(f"  ✗ Failed to check {path}: {e}")
        return False


def create_test_files(temp_dir: Path):
    """Create test files of various types."""
    log("Creating test files of various types...")

    # Text file
    txt_file = temp_dir / "notes.txt"
    txt_file.write_text("This is a plain text file.\nIt should be encrypted.\n")
    log("  Created notes.txt")

    # RTF file (rich text format)
    rtf_file = temp_dir / "document.rtf"
    rtf_content = r"{\rtf1\ansi This is an RTF document. It should be encrypted.}"
    rtf_file.write_text(rtf_content)
    log("  Created document.rtf")

    # JSON file
    json_file = temp_dir / "config.json"
    json_file.write_text('{"setting": "value", "secret": "should_be_encrypted"}')
    log("  Created config.json")

    # Binary file (fake PNG - just a header + some bytes)
    png_file = temp_dir / "image.png"
    # PNG magic bytes + some fake data
    png_data = b"\x89PNG\r\n\x1a\n" + b"fake image data for testing"
    png_file.write_bytes(png_data)
    log("  Created image.png")

    # YAML file
    yaml_file = temp_dir / "settings.yaml"
    yaml_file.write_text("key: value\nsecret: should_be_encrypted\n")
    log("  Created settings.yaml")

    # Markdown file (should still work)
    md_file = temp_dir / "readme.md"
    md_file.write_text("# README\n\nThis markdown file should also be encrypted.\n")
    log("  Created readme.md")

    # Python file
    py_file = temp_dir / "script.py"
    py_file.write_text("#!/usr/bin/env python3\nprint('Hello')\n")
    log("  Created script.py")


@pytest.fixture
def temp_dir():
    """Create temp directory for test."""
    temp = Path(tempfile.mkdtemp(prefix="cc_encrypt_all_"))
    log(f"Created {temp}")

    yield temp

    if temp.exists():
        log(f"Cleaning up {temp}")
        shutil.rmtree(temp)


@pytest.fixture
def ssh_key():
    """Get SSH key path for server access."""
    key_path = DEFAULT_SSH_KEY
    if not Path(key_path).exists():
        pytest.skip(f"SSH key not found at {key_path}")
    return key_path


@pytest.mark.integration
def test_all_file_types_encrypted(temp_dir, ssh_key):
    """
    Test that all file types are encrypted on the server (Issue #98).

    Files that SHOULD be encrypted:
    - .txt, .rtf, .json, .png, .yaml, .md, .py, etc.

    Files that should remain PLAINTEXT:
    - authz (required for access control)

    Run with: pytest tests/test_encrypt_all_files.py -s -m integration
    """
    log("=" * 60)
    log("Issue #98: Encrypt All File Types Integration Test")
    log("=" * 60)

    # Step 1: Clean slate
    log("\n[1/5] Cleaning server and client...")
    clean_server()
    clean_client()

    # Step 2: Setup account
    log("\n[2/5] Setting up test account...")
    os.chdir(temp_dir)
    login("Alice", temp_dir)
    user_email = init_account("Alice", temp_dir)

    # Step 3: Create test files
    log("\n[3/5] Creating test files of various types...")
    create_test_files(temp_dir)

    # Step 4: Sync to server
    log("\n[4/5] Syncing files to server...")
    sync_files(temp_dir)

    # Step 5: Verify encryption via SSH
    log("\n[5/5] Verifying encryption on server via SSH...")

    all_passed = True

    # Files that should be ENCRYPTED
    encrypted_files = [
        "notes.txt",
        "document.rtf",
        "config.json",
        "image.png",
        "settings.yaml",
        "readme.md",
        "script.py",
    ]

    log("\nFiles that should be ENCRYPTED:")
    for filename in encrypted_files:
        if not verify_file_encrypted_via_ssh(ssh_key, user_email, filename):
            all_passed = False

    # Files that should remain PLAINTEXT
    log("\nFiles that should be PLAINTEXT:")
    if not verify_file_plaintext_via_ssh(ssh_key, user_email, "authz"):
        all_passed = False

    # Results
    log("\n" + "=" * 60)
    if all_passed:
        log("✓ TEST PASSED: All file types encrypted correctly!")
        log("  - txt, rtf, json, png, yaml, md, py all encrypted")
        log("  - authz correctly kept as plaintext")
    else:
        error("✗ TEST FAILED: Some files not encrypted correctly!")
        error("  Check output above for details")
    log("=" * 60)

    assert all_passed, "Encryption test failed - see errors above"


@pytest.mark.integration
def test_encryption_unit_check():
    """
    Unit test to verify should_encrypt_file() behavior (Issue #98).

    This doesn't require server access - just tests the local logic.
    """
    from claudeconnect.encryption import should_encrypt_file, PLAINTEXT_FILES

    log("Testing should_encrypt_file() function...")

    # Files that SHOULD be encrypted
    should_encrypt = [
        "notes.txt",
        "document.rtf",
        "config.json",
        "image.png",
        "settings.yaml",
        "readme.md",
        "script.py",
        "data.csv",
        "archive.zip",
        "nested/path/file.txt",
    ]

    for filename in should_encrypt:
        result = should_encrypt_file(Path(filename))
        assert result is True, f"should_encrypt_file('{filename}') returned False, expected True"
        log(f"  ✓ {filename} -> encrypt")

    # Files that should NOT be encrypted (only authz)
    should_not_encrypt = list(PLAINTEXT_FILES)  # authz

    for filename in should_not_encrypt:
        result = should_encrypt_file(Path(filename))
        assert result is False, f"should_encrypt_file('{filename}') returned True, expected False"
        log(f"  ✓ {filename} -> plaintext")

    log("All should_encrypt_file() checks passed!")


def main():
    """Run as standalone script."""
    temp = Path(tempfile.mkdtemp(prefix="cc_encrypt_all_"))

    try:
        ssh_key = DEFAULT_SSH_KEY
        if not Path(ssh_key).exists():
            print(f"SSH key not found at {ssh_key}")
            print("Set CC_SSH_KEY environment variable to override")
            return

        test_all_file_types_encrypted(temp, ssh_key)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


if __name__ == "__main__":
    main()
