#!/usr/bin/env python3
"""
Upload Optimization Integration Test

Validates that sync avoids uploading when local mtime changes but content does not.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from conf import API_BASE_URL
from test_utils import (
    clean_client,
    clean_server,
    get_id_token,
    init_account,
    login,
    sync_files,
    verify_init_structure,
    log,
)


def get_manifest_entry(email: str, path: str) -> dict:
    token = get_id_token()
    response = httpx.get(
        f"{API_BASE_URL}/manifest/{email}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    assert response.status_code == 200, f"Failed to get manifest: {response.status_code}"
    manifest = response.json()
    for entry in manifest.get("files", []):
        if entry.get("path") == path:
            return entry
    raise AssertionError(f"Path {path} not found in manifest")


@pytest.fixture
def temp_dir():
    """Create and cleanup temp directory."""
    temp = Path(tempfile.mkdtemp(prefix="cc_upload_opt_"))
    log(f"Created {temp}")
    yield temp
    if temp.exists():
        log(f"Cleaning up {temp}")
        shutil.rmtree(temp)


@pytest.mark.integration
def test_sync_skips_upload_when_content_unchanged(temp_dir: Path):
    clean_server()
    clean_client()

    os.chdir(temp_dir)
    login("Alice", temp_dir)
    alice_email = init_account("Alice", temp_dir)
    verify_init_structure("Alice", temp_dir)

    notes_path = temp_dir / "notes.md"
    tasks_path = temp_dir / "tasks.md"
    notes_path.write_text("notes v1")
    tasks_path.write_text("tasks v1")
    sync_files(temp_dir)

    notes_entry_1 = get_manifest_entry(alice_email, "notes.md")
    tasks_entry_1 = get_manifest_entry(alice_email, "tasks.md")
    notes_mtime_1 = notes_entry_1.get("mtime")
    tasks_mtime_1 = tasks_entry_1.get("mtime")

    time.sleep(1.1)
    notes_path.write_text("notes v2")
    sync_files(temp_dir)

    notes_entry_2 = get_manifest_entry(alice_email, "notes.md")
    tasks_entry_2 = get_manifest_entry(alice_email, "tasks.md")
    notes_mtime_2 = notes_entry_2.get("mtime")
    tasks_mtime_2 = tasks_entry_2.get("mtime")

    assert notes_entry_2.get("sha256") != notes_entry_1.get("sha256")
    assert tasks_entry_2.get("sha256") == tasks_entry_1.get("sha256")
    assert notes_mtime_2 != notes_mtime_1
    assert tasks_mtime_2 == tasks_mtime_1
