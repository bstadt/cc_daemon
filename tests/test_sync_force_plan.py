"""Unit tests for `sync force` plan computation (server-mirrors-local).

Locks in the scoping rules: the force-push touches regular content only and
never the authz file, the claudeconnect/ mailbox subtree, hidden files, or
files larger than 1MB.
"""

from __future__ import annotations

import httpx
import pytest

import claudeconnect.cli as cli


@pytest.fixture
def context(tmp_path):
    # In-scope content
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "a.md").write_text("hello")
    (tmp_path / "profile.md").write_text("me")
    # Out of scope
    (tmp_path / "authz").write_text("[/]\nu = rw\n")
    (tmp_path / "claudeconnect" / "with-x").mkdir(parents=True)
    (tmp_path / "claudeconnect" / "with-x" / "msg.md").write_text("conversation")
    (tmp_path / ".secret").write_text("hidden")
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * (1_000_001))
    return tmp_path


def _patch_manifest(monkeypatch, server_paths):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"files": [{"path": p} for p in server_paths]}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())


def test_upload_set_excludes_out_of_scope(context, monkeypatch):
    _patch_manifest(monkeypatch, [])
    plan = cli.compute_force_plan(context, "u@example.com", "tok")

    assert "journal/a.md" in plan["to_upload"]
    assert "profile.md" in plan["to_upload"]
    # excluded from upload
    assert "authz" not in plan["to_upload"]
    assert all(not p.startswith("claudeconnect/") for p in plan["to_upload"])
    assert ".secret" not in plan["to_upload"]
    # >1MB file is skipped, not uploaded
    assert "big.bin" in plan["skipped_large"]
    assert "big.bin" not in plan["to_upload"]


def test_delete_set_is_stale_in_scope_server_files_only(context, monkeypatch):
    _patch_manifest(
        monkeypatch,
        [
            "journal/a.md",                 # present locally -> keep
            "journal/old.md",               # stale -> DELETE
            "profile.md",                   # present locally -> keep
            "authz",                        # never delete
            "claudeconnect/with-x/gone.md",  # mailbox -> never delete
        ],
    )
    plan = cli.compute_force_plan(context, "u@example.com", "tok")
    assert plan["to_delete"] == ["journal/old.md"]


def test_no_changes_when_server_matches(context, monkeypatch):
    _patch_manifest(monkeypatch, ["journal/a.md", "profile.md"])
    plan = cli.compute_force_plan(context, "u@example.com", "tok")
    assert plan["to_delete"] == []
    assert set(plan["to_upload"]) == {"journal/a.md", "profile.md"}
