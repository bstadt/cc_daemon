#!/usr/bin/env python3
"""
Sync Only Changed Files Tests

Covers peer pull planning, peer pull integration, and upload optimization.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from claudeconnect.cli import Config, compute_file_sha256, sync_http
from claudeconnect.config import email_to_repo_name, get_peers_dir, get_shadow_dir
from claudeconnect.session import _compute_peer_pull_plan
from conf import ALICE_EMAIL, BOB_EMAIL, API_BASE_URL
from test_utils import (
    accept_friend_request,
    check_friend_request,
    clean_client,
    clean_server,
    get_id_token,
    init_account,
    log,
    login,
    pull_peer_context,
    send_friend_request,
    sync_files,
    verify_init_structure,
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
def temp_dirs():
    """Create and cleanup temp directories for both accounts."""
    temp1 = Path(tempfile.mkdtemp(prefix="cc_peer_pull_alice_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_peer_pull_bob_"))
    log(f"Created {temp1} (Alice)")
    log(f"Created {temp2} (Bob)")

    yield temp1, temp2

    if temp1.exists():
        log(f"Cleaning up {temp1}")
        shutil.rmtree(temp1)
    if temp2.exists():
        log(f"Cleaning up {temp2}")
        shutil.rmtree(temp2)


@pytest.fixture
def temp_dir():
    """Create and cleanup temp directory."""
    temp = Path(tempfile.mkdtemp(prefix="cc_upload_opt_"))
    log(f"Created {temp}")
    yield temp
    if temp.exists():
        log(f"Cleaning up {temp}")
        shutil.rmtree(temp)


# ===========================================
# Peer pull plan (unit)
# ===========================================

def test_peer_pull_plan_detects_changes():
    log("Peer pull plan: detects changed and removed files based on hashes.")
    server_files = {
        "notes.md": {"path": "notes.md", "sha256": "aaa", "size": 10, "mtime": 1.0},
        "tasks.md": {"path": "tasks.md", "sha256": "bbb", "size": 20, "mtime": 2.0},
    }
    cached_files = {
        "notes.md": {"path": "notes.md", "sha256": "aaa", "size": 10, "mtime": 1.0},
        "tasks.md": {"path": "tasks.md", "sha256": "old", "size": 20, "mtime": 2.0},
        "old.md": {"path": "old.md", "sha256": "ccc", "size": 5, "mtime": 0.5},
    }

    to_download, removed = _compute_peer_pull_plan(server_files, cached_files)

    assert "notes.md" not in to_download
    assert "tasks.md" in to_download
    assert "old.md" in removed


def test_peer_pull_plan_falls_back_to_mtime_size():
    log("Peer pull plan: falls back to mtime/size when hashes are missing.")
    server_files = {
        "misc.md": {"path": "misc.md", "size": 12, "mtime": 3.0},
    }
    cached_files = {
        "misc.md": {"path": "misc.md", "size": 12, "mtime": 3.0},
    }

    to_download, removed = _compute_peer_pull_plan(server_files, cached_files)

    assert to_download == []
    assert removed == []


# ===========================================
# Peer pull integration
# ===========================================

@pytest.mark.integration
def test_peer_pull_only_downloads_changes(temp_dirs):
    """Ensure peer pulls only download changed files."""
    log("Peer pull integration: only changed files are re-downloaded.")
    temp1, temp2 = temp_dirs

    clean_server()
    clean_client()

    # Bob setup
    os.chdir(temp2)
    login("Alice", temp2)
    alice_email = init_account("Alice", temp2)
    verify_init_structure("Alice", temp2)

    # Bob setup
    os.chdir(temp2)
    login("Bob", temp2)
    bob_email = init_account("Bob", temp2)
    verify_init_structure("Bob", temp2)

    # Bob creates two files
    (temp2 / "notes.md").write_text("notes v1")
    (temp2 / "tasks.md").write_text("tasks v1")
    sync_files(temp2)

    # Bob sends friend request to Alice (grants read access)
    send_friend_request(temp2, ALICE_EMAIL)

    # Alice setup + accept
    os.chdir(temp1)
    login("Alice", temp1)
    alice_email = init_account("Alice", temp1)
    verify_init_structure("Alice", temp1)
    sync_files(temp1)
    check_friend_request(temp1, bob_email)
    accept_friend_request(temp1, bob_email)

    # Initial pull
    pull_peer_context(temp1, bob_email)

    bob_repo = email_to_repo_name(bob_email)
    alice_peer_dir = get_peers_dir(alice_email) / bob_repo
    alice_shadow_dir = get_shadow_dir(alice_email) / "peers" / bob_repo

    notes_peer = alice_peer_dir / "notes.md"
    tasks_peer = alice_peer_dir / "tasks.md"
    notes_shadow = alice_shadow_dir / "notes.md"
    tasks_shadow = alice_shadow_dir / "tasks.md"

    assert notes_peer.exists() and tasks_peer.exists(), "Peer files missing after pull"
    assert notes_shadow.exists() and tasks_shadow.exists(), "Shadow files missing after pull"

    notes_shadow_hash_1 = hashlib.sha256(notes_shadow.read_bytes()).hexdigest()
    tasks_shadow_hash_1 = hashlib.sha256(tasks_shadow.read_bytes()).hexdigest()
    tasks_peer_content_1 = tasks_peer.read_text()

    # Update only notes.md on Bob's side
    time.sleep(1.1)
    os.chdir(temp2)
    login("Bob", temp2)
    (temp2 / "notes.md").write_text("notes v2")
    sync_files(temp2)

    # Second pull
    os.chdir(temp1)
    login("Alice", temp1)
    pull_peer_context(temp1, bob_email)

    # notes should update, tasks should not
    notes_shadow_hash_2 = hashlib.sha256(notes_shadow.read_bytes()).hexdigest()
    tasks_shadow_hash_2 = hashlib.sha256(tasks_shadow.read_bytes()).hexdigest()

    assert notes_peer.read_text() == "notes v2"
    assert tasks_peer.read_text() == tasks_peer_content_1
    assert notes_shadow_hash_2 != notes_shadow_hash_1
    assert tasks_shadow_hash_2 == tasks_shadow_hash_1


# ===========================================
# Upload optimization (unit)
# ===========================================

def test_sync_http_skips_upload_when_content_unchanged(monkeypatch, tmp_path):
    log("Upload unit: mtime-only changes do not trigger uploads.")
    context_dir = tmp_path / "context"
    shadow_dir = tmp_path / "shadow"
    context_dir.mkdir()
    shadow_dir.mkdir()

    email = "user@example.com"
    rel_path = "notes.md"
    context_path = context_dir / rel_path
    context_path.write_text("notes v1")

    shadow_path = shadow_dir / rel_path
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    shadow_path.write_bytes(context_path.read_bytes())

    old_time = time.time() - 10
    os.utime(shadow_path, (old_time, old_time))
    os.utime(context_path, (old_time + 5, old_time + 5))

    manifest = {
        "files": [
            {
                "path": rel_path,
                "sha256": compute_file_sha256(shadow_path),
                "mtime": shadow_path.stat().st_mtime,
            }
        ]
    }

    class DummyResponse:
        def __init__(self, status_code: int, json_data=None, content: bytes = b""):
            self.status_code = status_code
            self._json = json_data
            self.content = content
            self.text = json.dumps(json_data) if json_data is not None else ""

        def json(self):
            return self._json

    put_calls: list[str] = []

    def fake_get(url, headers=None, timeout=None):
        if "/manifest/" in url:
            return DummyResponse(200, manifest)
        raise AssertionError(f"Unexpected GET {url}")

    def fake_put(url, headers=None, content=None, timeout=None):
        put_calls.append(url)
        return DummyResponse(200)

    monkeypatch.setattr("claudeconnect.cli.httpx.get", fake_get)
    monkeypatch.setattr("claudeconnect.cli.httpx.put", fake_put)
    monkeypatch.setattr("claudeconnect.cli.get_shadow_dir", lambda _: shadow_dir)
    monkeypatch.setattr(
        "claudeconnect.cli.get_config",
        lambda _: Config(context_dir=str(context_dir), encryption_enabled=False),
    )

    assert sync_http(context_dir, email, "token", max_workers=1) is True
    assert put_calls == []


# ===========================================
# Upload optimization (integration)
# ===========================================

@pytest.mark.integration
def test_sync_skips_upload_when_only_one_file_changes(temp_dir: Path):
    log("Upload integration: only the changed file is uploaded; others remain untouched.")
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
