from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import claudeconnect.terminal_ui as terminal_ui


def _force_tty(monkeypatch) -> None:
    monkeypatch.setattr(terminal_ui, "HAS_PTY", True)
    monkeypatch.setattr(terminal_ui.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal_ui.sys.stdout, "isatty", lambda: True)


def test_persistent_banner_disabled_by_default(monkeypatch):
    _force_tty(monkeypatch)
    monkeypatch.delenv("CC_PERSIST_BANNER", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert terminal_ui.should_use_persistent_banner() is False


def test_persistent_banner_opt_in(monkeypatch):
    _force_tty(monkeypatch)
    monkeypatch.setenv("CC_PERSIST_BANNER", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert terminal_ui.should_use_persistent_banner() is True


def test_soft_banner_enabled_by_default(monkeypatch):
    _force_tty(monkeypatch)
    monkeypatch.delenv("CC_SOFT_BANNER", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert terminal_ui.should_use_soft_banner() is True


def test_soft_banner_disabled_via_env(monkeypatch):
    _force_tty(monkeypatch)
    monkeypatch.setenv("CC_SOFT_BANNER", "0")
    monkeypatch.setenv("TERM", "xterm-256color")
    assert terminal_ui.should_use_soft_banner() is False
