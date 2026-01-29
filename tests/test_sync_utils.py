from __future__ import annotations

from pathlib import Path

from claudeconnect.sync_utils import write_context_if_decryptable


def is_encrypted_stub(data: bytes) -> bool:
    return data.startswith(b"ENC:")


def decrypt_stub(data: bytes) -> bytes:
    if not data.startswith(b"ENC:"):
        raise ValueError("not encrypted")
    return data[4:]


def test_write_context_plaintext(tmp_path: Path) -> None:
    context_path = tmp_path / "notes.md"
    content = b"plain text"

    wrote = write_context_if_decryptable(
        encrypted_content=content,
        context_path=context_path,
        path="notes.md",
        can_decrypt=True,
        decrypt_fn=decrypt_stub,
        is_encrypted_fn=is_encrypted_stub,
        error_prefix="test",
    )

    assert wrote is True
    assert context_path.read_bytes() == content


def test_write_context_skips_without_key(tmp_path: Path) -> None:
    context_path = tmp_path / "secret.md"
    content = b"ENC:secret"

    wrote = write_context_if_decryptable(
        encrypted_content=content,
        context_path=context_path,
        path="secret.md",
        can_decrypt=False,
        decrypt_fn=None,
        is_encrypted_fn=is_encrypted_stub,
        error_prefix="test",
    )

    assert wrote is False
    assert not context_path.exists()


def test_write_context_skips_on_decrypt_error(tmp_path: Path) -> None:
    context_path = tmp_path / "secret.md"
    content = b"ENC:secret"

    def decrypt_raises(_: bytes) -> bytes:
        raise ValueError("boom")

    wrote = write_context_if_decryptable(
        encrypted_content=content,
        context_path=context_path,
        path="secret.md",
        can_decrypt=True,
        decrypt_fn=decrypt_raises,
        is_encrypted_fn=is_encrypted_stub,
        error_prefix="test",
    )

    assert wrote is False
    assert not context_path.exists()


def test_write_context_decrypts(tmp_path: Path) -> None:
    context_path = tmp_path / "secret.md"
    content = b"ENC:secret"

    wrote = write_context_if_decryptable(
        encrypted_content=content,
        context_path=context_path,
        path="secret.md",
        can_decrypt=True,
        decrypt_fn=decrypt_stub,
        is_encrypted_fn=is_encrypted_stub,
        error_prefix="test",
    )

    assert wrote is True
    assert context_path.read_bytes() == b"secret"
