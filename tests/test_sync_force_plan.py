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
    # A real markdown design note inside projects/ (user content -> in scope)
    (tmp_path / "projects" / "spike").mkdir(parents=True)
    (tmp_path / "projects" / "spike" / "design.md").write_text("notes")
    # Out of scope
    (tmp_path / "authz").write_text("[/]\nu = rw\n")
    (tmp_path / "claudeconnect" / "with-x").mkdir(parents=True)
    (tmp_path / "claudeconnect" / "with-x" / "msg.md").write_text("conversation")
    (tmp_path / ".secret").write_text("hidden")
    # Dependency junk (vendored) -> never in scope, even the .md READMEs
    (tmp_path / "projects" / "spike" / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "projects" / "spike" / "node_modules" / "pkg" / "README.md").write_text("vendored")
    (tmp_path / "projects" / "spike" / "node_modules" / "pkg" / "index.js").write_text("code")
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "thing.md").write_text("vendored")
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


def test_dependency_dirs_excluded(context, monkeypatch):
    """node_modules / venv are never in scope, even their .md READMEs."""
    _patch_manifest(monkeypatch, [])
    plan = cli.compute_force_plan(context, "u@example.com", "tok")
    up = plan["to_upload"]
    # real project design note IS included
    assert "projects/spike/design.md" in up
    # vendored junk is NOT
    assert not any("node_modules" in p for p in up)
    assert not any(p.startswith("venv/") for p in up)


def test_md_only_restricts_to_markdown(context, monkeypatch):
    (context / "data.json").write_text("{}")
    _patch_manifest(monkeypatch, [])
    plan = cli.compute_force_plan(context, "u@example.com", "tok", md_only=True)
    up = plan["to_upload"]
    assert all(p.endswith(".md") for p in up)
    assert "journal/a.md" in up and "projects/spike/design.md" in up
    assert "data.json" not in up
    # still excludes vendored .md
    assert not any("node_modules" in p for p in up)


def test_no_changes_when_server_matches(context, monkeypatch):
    in_scope = ["journal/a.md", "profile.md", "projects/spike/design.md"]
    _patch_manifest(monkeypatch, in_scope)
    plan = cli.compute_force_plan(context, "u@example.com", "tok")
    assert plan["to_delete"] == []
    assert set(plan["to_upload"]) == set(in_scope)
