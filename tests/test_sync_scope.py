"""Unit tests for normal-sync scope filtering (_sync_in_scope) + md_only config.

Normal sync must never upload vendored junk (node_modules, venv, …), and with
md_only it should sync only Markdown — while still carrying the authz file and
the claudeconnect/ system subtree (needed for friends + conversations).
"""

from __future__ import annotations

import claudeconnect.cli as cli
from claudeconnect.config import Config


def test_dependency_dirs_always_skipped():
    for md_only in (False, True):
        assert not cli._sync_in_scope("projects/x/node_modules/p/README.md", md_only)
        assert not cli._sync_in_scope("a/venv/lib/thing.py", md_only)
        assert not cli._sync_in_scope("b/__pycache__/m.pyc", md_only)
        assert not cli._sync_in_scope(".git/config", md_only)


def test_default_keeps_all_non_junk():
    assert cli._sync_in_scope("journal/a.md", False)
    assert cli._sync_in_scope("projects/data.json", False)  # non-md kept when md_only off
    assert cli._sync_in_scope("authz", False)


def test_md_only_restricts_to_markdown_but_keeps_system():
    assert cli._sync_in_scope("journal/a.md", True)
    assert not cli._sync_in_scope("projects/data.json", True)
    # system files carried regardless of extension
    assert cli._sync_in_scope("authz", True)
    assert cli._sync_in_scope("claudeconnect/with-x/transcript.md", True)


def test_config_md_only_roundtrip(tmp_path, monkeypatch):
    import claudeconnect.config as cfg

    monkeypatch.setattr(cfg, "ACCOUNTS_DIR", tmp_path)
    monkeypatch.setattr(cfg, "get_account_dir", lambda email: tmp_path / email)
    c = Config(context_dir="/x", encryption_enabled=True, md_only=True)
    c.save("u@example.com")
    loaded = Config.load("u@example.com")
    assert loaded.md_only is True
