"""Sync utilities for handling encrypted content safely."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def write_shadow_file(shadow_path: Path, content: bytes) -> None:
    """Write encrypted content to the shadow cache."""
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    shadow_path.write_bytes(content)


def write_context_if_decryptable(
    *,
    encrypted_content: bytes,
    context_path: Path,
    path: str,
    can_decrypt: bool,
    decrypt_fn: Callable[[bytes], bytes] | None,
    is_encrypted_fn: Callable[[bytes], bool],
    error_prefix: str,
    emit_error: bool = True,
) -> bool:
    """Write plaintext to context if possible, never ciphertext."""
    if not is_encrypted_fn(encrypted_content):
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_bytes(encrypted_content)
        return True

    if not can_decrypt or decrypt_fn is None:
        if emit_error:
            print(f"  Error: Could not decrypt {path} ({error_prefix}): missing master key")
        return False

    try:
        plaintext = decrypt_fn(encrypted_content)
    except Exception as exc:
        if emit_error:
            print(f"  Error: Could not decrypt {path} ({error_prefix}): {exc}")
        return False

    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_bytes(plaintext)
    return True
