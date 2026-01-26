#!/usr/bin/env python3
"""
ClaudeConnect Delete API Integration Tests

Tests recursive directory delete and cross-user delete permissions.
Run with: pytest tests/test_delete_api.py -s -m integration
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

from conf import API_BASE_URL
from test_utils import (
    clean_server,
    clean_client,
    email_to_repo_name,
    get_id_token,
    log,
    login,
    init_account,
    upload_file_to_server,
)


@pytest.fixture
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_account1_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_account2_"))
    log(f"Created {temp1} (Account 1)")
    log(f"Created {temp2} (Account 2)")

    yield temp1, temp2

    if temp1.exists():
        shutil.rmtree(temp1)
    if temp2.exists():
        shutil.rmtree(temp2)


@pytest.mark.integration
def test_recursive_delete_and_permissions(temp_dirs):
    """
    - Owner can delete a directory recursively
    - Non-owner cannot delete other user's files/dirs
    """
    temp1, temp2 = temp_dirs

    clean_server()
    clean_client()

    # Alice setup
    os.chdir(temp1)
    login("Alice", temp1)
    alice_email = init_account("Alice", temp1)

    # Bob setup
    os.chdir(temp2)
    login("Bob", temp2)
    bob_email = init_account("Bob", temp2)

    # Alice creates a directory with multiple files
    os.chdir(temp1)
    login("Alice", temp1)
    friend_dir = f"claudeconnect/with-{email_to_repo_name(bob_email)}"
    upload_file_to_server(alice_email, f"{friend_dir}/one.md", b"# one\n")
    upload_file_to_server(alice_email, f"{friend_dir}/nested/two.md", b"# two\n")

    # Explicitly grant Bob write access to the conversation folder
    authz_path = temp1 / "authz"
    authz_content = authz_path.read_text()
    with_section = f"[/claudeconnect/with-{email_to_repo_name(bob_email)}]"
    if with_section not in authz_content:
        authz_content = (
            authz_content.rstrip()
            + "\n\n"
            + f"# Allow {bob_email} to write conversations\n"
            + f"{with_section}\n"
            + f"{alice_email} = rw\n"
            + f"{bob_email} = rw\n"
        )
        authz_path.write_text(authz_content)
        alice_token = get_id_token()
        response = httpx.put(
            f"{API_BASE_URL}/files/{alice_email}/authz",
            content=authz_content.encode(),
            headers={"Authorization": f"Bearer {alice_token}"},
            timeout=30,
        )
        assert response.status_code == 200, f"Failed to upload authz: {response.text}"

    # Bob cannot delete Alice's directory
    os.chdir(temp2)
    login("Bob", temp2)
    bob_token = get_id_token()
    response = httpx.delete(
        f"{API_BASE_URL}/files/{alice_email}/{friend_dir}/one.md",
        headers={"Authorization": f"Bearer {bob_token}"},
        timeout=30,
    )
    assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    response = httpx.delete(
        f"{API_BASE_URL}/files/{alice_email}/{friend_dir}",
        headers={"Authorization": f"Bearer {bob_token}"},
        params={"recursive": "true"},
        timeout=30,
    )
    assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"

    # Alice cannot delete directory without recursive flag
    os.chdir(temp1)
    login("Alice", temp1)
    alice_token = get_id_token()
    response = httpx.delete(
        f"{API_BASE_URL}/files/{alice_email}/{friend_dir}",
        headers={"Authorization": f"Bearer {alice_token}"},
        timeout=30,
    )
    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"

    # Alice can delete directory recursively
    response = httpx.delete(
        f"{API_BASE_URL}/files/{alice_email}/{friend_dir}",
        headers={"Authorization": f"Bearer {alice_token}"},
        params={"recursive": "true"},
        timeout=30,
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    deleted = response.json().get("deleted", 0)
    assert deleted == 2, f"Expected 2 files deleted, got {deleted}"

    # Verify file is gone
    response = httpx.get(
        f"{API_BASE_URL}/files/{alice_email}/{friend_dir}/one.md",
        headers={"Authorization": f"Bearer {alice_token}"},
        timeout=30,
    )
    assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
