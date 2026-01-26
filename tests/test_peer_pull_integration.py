#!/usr/bin/env python3
"""
Peer Pull Optimization Integration Test

Validates that peer pulls only download changed files by checking
shadow/peer cache mtimes across pulls.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from claudeconnect.config import email_to_repo_name, get_peers_dir, get_shadow_dir
from conf import ALICE_EMAIL, BOB_EMAIL
from test_utils import (
    clean_client,
    clean_server,
    login,
    init_account,
    verify_init_structure,
    sync_files,
    send_friend_request,
    check_friend_request,
    accept_friend_request,
    pull_peer_context,
    log,
)


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


@pytest.mark.integration
def test_peer_pull_only_downloads_changes(temp_dirs):
    """Ensure peer pulls only download changed files."""
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
