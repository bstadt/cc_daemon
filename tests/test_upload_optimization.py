from __future__ import annotations

import json
import os
import time

from claudeconnect.cli import Config, compute_file_sha256, sync_http


def test_sync_http_skips_upload_when_content_unchanged(monkeypatch, tmp_path):
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
