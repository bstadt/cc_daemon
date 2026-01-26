from claudeconnect.session import _compute_peer_pull_plan


def test_peer_pull_plan_detects_changes():
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
    server_files = {
        "misc.md": {"path": "misc.md", "size": 12, "mtime": 3.0},
    }
    cached_files = {
        "misc.md": {"path": "misc.md", "size": 12, "mtime": 3.0},
    }

    to_download, removed = _compute_peer_pull_plan(server_files, cached_files)

    assert to_download == []
    assert removed == []
