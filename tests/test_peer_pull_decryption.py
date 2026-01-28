from __future__ import annotations

from pathlib import Path

from claudeconnect import session


class FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, content: bytes | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content or b""
        self.text = text

    def json(self) -> dict:
        if self._json_data is None:
            raise ValueError("No JSON data")
        return self._json_data


def test_peer_pull_skips_encrypted_context_without_key(monkeypatch, tmp_path: Path) -> None:
    peer_email = "peer@example.com"
    our_email = "me@example.com"

    peers_dir = tmp_path / "peers"
    shadow_root = tmp_path / "shadow"

    monkeypatch.setattr(session, "get_peers_dir", lambda _: peers_dir)
    monkeypatch.setattr(session, "get_shadow_dir", lambda _: shadow_root)
    monkeypatch.setattr(session, "HAS_ENCRYPTION", True)
    monkeypatch.setattr(session, "is_encrypted_file", lambda _: True)
    monkeypatch.setattr(session, "has_friend_master_key", lambda *_: False)

    def decrypt_should_not_run(*_args, **_kwargs) -> bytes:
        raise AssertionError("decrypt should not run without key")

    monkeypatch.setattr(session, "decrypt_file_with_friend_master_key", decrypt_should_not_run)

    def fake_get(url: str, headers: dict | None = None, timeout: int | None = None) -> FakeResponse:
        if "/manifest/" in url:
            return FakeResponse(
                200,
                json_data={"files": [{"path": "notes.md", "sha256": "abc", "mtime": 1.0, "size": 10}]},
            )
        if "/files/" in url:
            return FakeResponse(200, content=b"CCENC" + b"ciphertext")
        return FakeResponse(404, text="not found")

    monkeypatch.setattr(session.httpx, "get", fake_get)

    peer_dir = session.pull_peer_context_http(peer_email, "token", our_email, max_workers=1)
    assert peer_dir is not None

    peer_name = session.email_to_repo_name(peer_email)
    peer_shadow_path = shadow_root / "peers" / peer_name / "notes.md"
    peer_context_path = peers_dir / peer_name / "notes.md"

    assert peer_shadow_path.exists()
    assert not peer_context_path.exists()
