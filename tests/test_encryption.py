"""Tests for client-side hybrid encryption (X25519 + AES-256-GCM)."""

import os
import pytest
from pathlib import Path

# Skip all tests if cryptography is not installed
pytest.importorskip("cryptography")

from claudeconnect.encryption import (
    # Key management
    generate_keypair,
    load_private_key,
    load_public_key,
    get_key_fingerprint,
    # Friend key management
    save_friend_public_key,
    load_friend_public_key,
    list_friends,
    delete_friend_public_key,
    # Encryption/Decryption
    encrypt_file,
    decrypt_file,
    add_recipient_to_file,
    # Utilities
    should_encrypt_file,
    is_encrypted_file,
    is_encryption_available,
    MAGIC_BYTES,
)


@pytest.fixture
def temp_keys_dir(tmp_path):
    """Create a temporary directory for keys."""
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    return keys_dir


@pytest.fixture
def temp_friends_dir(tmp_path):
    """Create a temporary directory for friend keys."""
    friends_dir = tmp_path / "friends"
    friends_dir.mkdir()
    return friends_dir


@pytest.fixture
def alice_keys(tmp_path):
    """Generate Alice's keypair."""
    keys_dir = tmp_path / "alice_keys"
    keys_dir.mkdir()
    private_bytes, public_bytes = generate_keypair(keys_dir)
    return {
        "dir": keys_dir,
        "private": private_bytes,
        "public": public_bytes,
        "email": "alice@example.com",
    }


@pytest.fixture
def bob_keys(tmp_path):
    """Generate Bob's keypair."""
    keys_dir = tmp_path / "bob_keys"
    keys_dir.mkdir()
    private_bytes, public_bytes = generate_keypair(keys_dir)
    return {
        "dir": keys_dir,
        "private": private_bytes,
        "public": public_bytes,
        "email": "bob@example.com",
    }


@pytest.fixture
def carol_keys(tmp_path):
    """Generate Carol's keypair."""
    keys_dir = tmp_path / "carol_keys"
    keys_dir.mkdir()
    private_bytes, public_bytes = generate_keypair(keys_dir)
    return {
        "dir": keys_dir,
        "private": private_bytes,
        "public": public_bytes,
        "email": "carol@example.com",
    }


# =============================================================================
# Key Management Tests
# =============================================================================

class TestKeyManagement:
    """Tests for key generation and loading."""

    def test_is_encryption_available(self):
        """Cryptography library should be available."""
        assert is_encryption_available() is True

    def test_generate_keypair(self, temp_keys_dir):
        """Should generate and save keypair."""
        private_bytes, public_bytes = generate_keypair(temp_keys_dir)

        # Check key sizes
        assert len(private_bytes) == 32
        assert len(public_bytes) == 32

        # Check files were created
        assert (temp_keys_dir / "private.key").exists()
        assert (temp_keys_dir / "public.key").exists()

        # Check private key permissions
        private_mode = (temp_keys_dir / "private.key").stat().st_mode
        assert private_mode & 0o077 == 0  # No group/other permissions

    def test_generate_keypair_already_exists(self, alice_keys):
        """Should raise if keys already exist."""
        with pytest.raises(FileExistsError):
            generate_keypair(alice_keys["dir"])

    def test_load_private_key(self, alice_keys):
        """Should load private key from disk."""
        private_key = load_private_key(alice_keys["dir"])
        assert private_key is not None

    def test_load_private_key_not_found(self, temp_keys_dir):
        """Should raise if private key doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_private_key(temp_keys_dir)

    def test_load_public_key(self, alice_keys):
        """Should load public key bytes from disk."""
        public_bytes = load_public_key(alice_keys["dir"])
        assert public_bytes == alice_keys["public"]
        assert len(public_bytes) == 32

    def test_load_public_key_not_found(self, temp_keys_dir):
        """Should raise if public key doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_public_key(temp_keys_dir)

    def test_get_key_fingerprint(self, alice_keys):
        """Should return a hex fingerprint."""
        fingerprint = get_key_fingerprint(alice_keys["public"])
        assert len(fingerprint) == 16
        assert all(c in "0123456789abcdef" for c in fingerprint)

    def test_different_keys_different_fingerprints(self, alice_keys, bob_keys):
        """Different keys should have different fingerprints."""
        alice_fp = get_key_fingerprint(alice_keys["public"])
        bob_fp = get_key_fingerprint(bob_keys["public"])
        assert alice_fp != bob_fp


# =============================================================================
# Friend Key Management Tests
# =============================================================================

class TestFriendKeyManagement:
    """Tests for managing friend public keys."""

    def test_save_friend_public_key(self, temp_friends_dir, bob_keys):
        """Should save friend's public key."""
        path = save_friend_public_key(
            bob_keys["email"],
            bob_keys["public"],
            temp_friends_dir,
        )
        assert path.exists()
        assert path.read_bytes() == bob_keys["public"]

    def test_load_friend_public_key(self, temp_friends_dir, bob_keys):
        """Should load friend's public key."""
        save_friend_public_key(
            bob_keys["email"],
            bob_keys["public"],
            temp_friends_dir,
        )
        loaded = load_friend_public_key(bob_keys["email"], temp_friends_dir)
        assert loaded == bob_keys["public"]

    def test_load_friend_public_key_not_found(self, temp_friends_dir):
        """Should raise if friend key doesn't exist."""
        with pytest.raises(FileNotFoundError):
            load_friend_public_key("nobody@example.com", temp_friends_dir)

    def test_list_friends_empty(self, temp_friends_dir):
        """Should return empty list when no friends."""
        assert list_friends(temp_friends_dir) == []

    def test_list_friends(self, temp_friends_dir, bob_keys, carol_keys):
        """Should list all friends."""
        save_friend_public_key(bob_keys["email"], bob_keys["public"], temp_friends_dir)
        save_friend_public_key(carol_keys["email"], carol_keys["public"], temp_friends_dir)

        friends = list_friends(temp_friends_dir)
        assert len(friends) == 2

    def test_delete_friend_public_key(self, temp_friends_dir, bob_keys):
        """Should delete friend's key."""
        save_friend_public_key(bob_keys["email"], bob_keys["public"], temp_friends_dir)
        assert delete_friend_public_key(bob_keys["email"], temp_friends_dir) is True
        assert delete_friend_public_key(bob_keys["email"], temp_friends_dir) is False

    def test_email_sanitization(self, temp_friends_dir, bob_keys):
        """Email should be sanitized for filename."""
        path = save_friend_public_key(
            "Bob.Smith@Example.COM",
            bob_keys["public"],
            temp_friends_dir,
        )
        assert "@" not in path.name
        assert "." not in path.stem  # dots in stem, but .pub extension ok


# =============================================================================
# Encryption/Decryption Tests
# =============================================================================

class TestEncryption:
    """Tests for file encryption and decryption."""

    def test_encrypt_single_recipient(self, alice_keys):
        """Should encrypt for a single recipient."""
        plaintext = b"Hello, World!"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        assert ciphertext.startswith(MAGIC_BYTES)
        assert plaintext not in ciphertext
        assert len(ciphertext) > len(plaintext)

    def test_decrypt_single_recipient(self, alice_keys):
        """Should decrypt as the recipient."""
        plaintext = b"Secret message"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])
        decrypted = decrypt_file(ciphertext, alice_keys["email"], alice_keys["dir"])

        assert decrypted == plaintext

    def test_encrypt_multiple_recipients(self, alice_keys, bob_keys):
        """Should encrypt for multiple recipients."""
        plaintext = b"Shared secret"
        recipients = {
            alice_keys["email"]: alice_keys["public"],
            bob_keys["email"]: bob_keys["public"],
        }

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        # Both should be able to decrypt
        assert decrypt_file(ciphertext, alice_keys["email"], alice_keys["dir"]) == plaintext
        assert decrypt_file(ciphertext, bob_keys["email"], bob_keys["dir"]) == plaintext

    def test_decrypt_wrong_recipient(self, alice_keys, bob_keys):
        """Should fail if not a recipient."""
        plaintext = b"Only for Alice"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        with pytest.raises(ValueError, match="Not a recipient"):
            decrypt_file(ciphertext, bob_keys["email"], bob_keys["dir"])

    def test_encrypt_no_recipients(self, alice_keys):
        """Should fail with no recipients."""
        with pytest.raises(ValueError, match="At least one recipient"):
            encrypt_file(b"test", {}, alice_keys["dir"])

    def test_decrypt_invalid_magic(self, alice_keys):
        """Should fail with invalid magic bytes."""
        with pytest.raises(ValueError, match="Not a ClaudeConnect"):
            decrypt_file(b"not encrypted", alice_keys["email"], alice_keys["dir"])

    def test_decrypt_too_short(self, alice_keys):
        """Should fail with too-short file."""
        with pytest.raises(ValueError, match="too short"):
            decrypt_file(b"CCE", alice_keys["email"], alice_keys["dir"])

    def test_large_file_encryption(self, alice_keys):
        """Should handle large files."""
        plaintext = os.urandom(1024 * 1024)  # 1 MB
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])
        decrypted = decrypt_file(ciphertext, alice_keys["email"], alice_keys["dir"])

        assert decrypted == plaintext

    def test_empty_file_encryption(self, alice_keys):
        """Should handle empty files."""
        plaintext = b""
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])
        decrypted = decrypt_file(ciphertext, alice_keys["email"], alice_keys["dir"])

        assert decrypted == plaintext

    def test_unicode_content(self, alice_keys):
        """Should handle unicode content."""
        plaintext = "Hello, World! \u2603 \U0001F600".encode("utf-8")
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])
        decrypted = decrypt_file(ciphertext, alice_keys["email"], alice_keys["dir"])

        assert decrypted == plaintext


# =============================================================================
# Add Recipient Tests
# =============================================================================

class TestAddRecipient:
    """Tests for adding recipients to encrypted files."""

    def test_add_recipient(self, alice_keys, bob_keys):
        """Should add a new recipient."""
        plaintext = b"Initially just for Alice"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        # Encrypt for Alice only
        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        # Bob can't decrypt yet
        with pytest.raises(ValueError):
            decrypt_file(ciphertext, bob_keys["email"], bob_keys["dir"])

        # Add Bob as recipient
        updated = add_recipient_to_file(
            ciphertext,
            bob_keys["email"],
            bob_keys["public"],
            alice_keys["email"],
            alice_keys["dir"],
        )

        # Now Bob can decrypt
        assert decrypt_file(updated, bob_keys["email"], bob_keys["dir"]) == plaintext

        # Alice can still decrypt
        assert decrypt_file(updated, alice_keys["email"], alice_keys["dir"]) == plaintext

    def test_add_recipient_not_owner(self, alice_keys, bob_keys, carol_keys):
        """Non-recipient can't add others."""
        plaintext = b"For Alice only"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        # Bob tries to add Carol but Bob isn't a recipient
        with pytest.raises(ValueError, match="Not a recipient"):
            add_recipient_to_file(
                ciphertext,
                carol_keys["email"],
                carol_keys["public"],
                bob_keys["email"],
                bob_keys["dir"],
            )


# =============================================================================
# File Filtering Tests
# =============================================================================

class TestFileFiltering:
    """Tests for file type filtering."""

    def test_should_encrypt_markdown(self):
        """Should encrypt .md files."""
        assert should_encrypt_file(Path("notes.md")) is True
        assert should_encrypt_file(Path("path/to/file.md")) is True

    def test_should_not_encrypt_authz(self):
        """Should not encrypt authz file."""
        assert should_encrypt_file(Path("authz")) is False

    def test_should_not_encrypt_keep(self):
        """Should not encrypt .keep file."""
        assert should_encrypt_file(Path(".keep")) is False

    def test_should_not_encrypt_other(self):
        """Should not encrypt non-markdown files."""
        assert should_encrypt_file(Path("file.txt")) is False
        assert should_encrypt_file(Path("file.py")) is False
        assert should_encrypt_file(Path("file.json")) is False

    def test_is_encrypted_file(self, alice_keys):
        """Should detect encrypted files."""
        plaintext = b"test"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        assert is_encrypted_file(ciphertext) is True
        assert is_encrypted_file(plaintext) is False
        assert is_encrypted_file(b"") is False
        assert is_encrypted_file(b"CCEN") is False  # Partial magic


# =============================================================================
# Security Tests
# =============================================================================

class TestSecurity:
    """Security-focused tests."""

    def test_different_ciphertext_each_time(self, alice_keys):
        """Same plaintext should produce different ciphertext."""
        plaintext = b"Same message"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext1 = encrypt_file(plaintext, recipients, alice_keys["dir"])
        ciphertext2 = encrypt_file(plaintext, recipients, alice_keys["dir"])

        # Nonces and ephemeral keys should differ
        assert ciphertext1 != ciphertext2

    def test_tampered_ciphertext_fails(self, alice_keys):
        """Should detect tampering."""
        plaintext = b"Original message"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = bytearray(encrypt_file(plaintext, recipients, alice_keys["dir"]))

        # Tamper with the encrypted content (last byte)
        ciphertext[-1] ^= 0xFF

        with pytest.raises(Exception):  # GCM authentication will fail
            decrypt_file(bytes(ciphertext), alice_keys["email"], alice_keys["dir"])

    def test_private_key_never_in_ciphertext(self, alice_keys):
        """Private key should never appear in ciphertext."""
        plaintext = b"Secret"
        recipients = {alice_keys["email"]: alice_keys["public"]}

        ciphertext = encrypt_file(plaintext, recipients, alice_keys["dir"])

        assert alice_keys["private"] not in ciphertext
