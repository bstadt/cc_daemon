"""Tests for key export/import functionality."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from claudeconnect.encryption import (
    generate_keypair,
    generate_master_key,
    load_public_key,
    load_private_key,
    load_master_key,
    get_key_fingerprint,
    export_keys,
    import_keys,
    verify_export_file,
    derive_key_from_password,
    KEYS_EXPORT_MAGIC,
)


@pytest.fixture
def temp_keys_dir():
    """Create temporary keys directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_email():
    return "test@example.com"


class TestDeriveKeyFromPassword:
    def test_derives_32_byte_key(self, temp_keys_dir):
        """derive_key_from_password returns a 32-byte key."""
        salt = b"0123456789abcdef"
        key = derive_key_from_password("testpassword", salt)
        assert len(key) == 32
        assert isinstance(key, bytes)

    def test_same_password_same_salt_same_key(self, temp_keys_dir):
        """Same password and salt produce same key."""
        salt = b"0123456789abcdef"
        key1 = derive_key_from_password("testpassword", salt)
        key2 = derive_key_from_password("testpassword", salt)
        assert key1 == key2

    def test_different_password_different_key(self, temp_keys_dir):
        """Different passwords produce different keys."""
        salt = b"0123456789abcdef"
        key1 = derive_key_from_password("password1", salt)
        key2 = derive_key_from_password("password2", salt)
        assert key1 != key2

    def test_different_salt_different_key(self, temp_keys_dir):
        """Different salts produce different keys."""
        key1 = derive_key_from_password("testpassword", b"salt1___________")
        key2 = derive_key_from_password("testpassword", b"salt2___________")
        assert key1 != key2


class TestExportKeys:
    def test_export_unencrypted(self, temp_keys_dir, test_email):
        """Export keys without password."""
        # Generate keys
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)

        # Export
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        assert export["format"] == KEYS_EXPORT_MAGIC
        assert export["version"] == 1
        assert export["email"] == test_email
        assert "fingerprint" in export
        assert "created_at" in export
        assert "keys" in export
        assert "encrypted_keys" not in export
        assert "private_key" in export["keys"]
        assert "public_key" in export["keys"]
        assert "master_key" in export["keys"]

    def test_export_encrypted(self, temp_keys_dir, test_email):
        """Export keys with password."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)

        export = export_keys(test_email, password="testpass123", keys_dir=temp_keys_dir)

        assert "encrypted_keys" in export
        assert "keys" not in export
        assert "salt" in export
        assert "nonce" in export
        assert export["format"] == KEYS_EXPORT_MAGIC

    def test_export_without_master_key(self, temp_keys_dir, test_email):
        """Export works without master key."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        # No master key generated

        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        assert export["keys"]["master_key"] is None
        assert export["keys"]["private_key"] is not None
        assert export["keys"]["public_key"] is not None

    def test_export_missing_keys_raises(self, temp_keys_dir, test_email):
        """Export raises FileNotFoundError if no keys exist."""
        with pytest.raises(FileNotFoundError):
            export_keys(test_email, password=None, keys_dir=temp_keys_dir)


class TestImportKeys:
    def test_import_roundtrip_unencrypted(self, temp_keys_dir, test_email):
        """Export and reimport keys without encryption."""
        # Generate original keys
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        original_fingerprint = get_key_fingerprint(load_public_key(test_email, temp_keys_dir))
        original_master = load_master_key(test_email, temp_keys_dir)

        # Export
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        # Delete keys
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()
        (temp_keys_dir / "master.key").unlink()

        # Import
        email, fingerprint, friend_count = import_keys(export, password=None, keys_dir=temp_keys_dir)

        assert email == test_email
        assert fingerprint == original_fingerprint

        # Verify keys are correct
        imported_master = load_master_key(test_email, temp_keys_dir)
        assert imported_master == original_master

    def test_import_roundtrip_encrypted(self, temp_keys_dir, test_email):
        """Export and reimport keys with encryption."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        original_fingerprint = get_key_fingerprint(load_public_key(test_email, temp_keys_dir))

        # Export with password
        export = export_keys(test_email, password="mypassword", keys_dir=temp_keys_dir)

        # Delete keys
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()
        (temp_keys_dir / "master.key").unlink()

        # Import with correct password
        email, fingerprint, friend_count = import_keys(export, password="mypassword", keys_dir=temp_keys_dir)

        assert email == test_email
        assert fingerprint == original_fingerprint

    def test_import_wrong_password(self, temp_keys_dir, test_email):
        """Import with wrong password fails."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password="correct", keys_dir=temp_keys_dir)

        # Delete keys so import doesn't hit FileExistsError first
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()

        with pytest.raises(ValueError, match="wrong password"):
            import_keys(export, password="wrong", keys_dir=temp_keys_dir)

    def test_import_refuses_overwrite(self, temp_keys_dir, test_email):
        """Import fails if keys exist and force=False."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        with pytest.raises(FileExistsError):
            import_keys(export, password=None, keys_dir=temp_keys_dir, force=False)

    def test_import_force_overwrites(self, temp_keys_dir, test_email):
        """Import with force=True overwrites existing keys."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        # Should not raise
        email, fingerprint, friend_count = import_keys(
            export, password=None, keys_dir=temp_keys_dir, force=True
        )
        assert email == test_email

    def test_import_invalid_format(self, temp_keys_dir, test_email):
        """Import rejects invalid format."""
        with pytest.raises(ValueError, match="Invalid export file format"):
            import_keys({"format": "wrong"}, keys_dir=temp_keys_dir)

    def test_import_email_mismatch(self, temp_keys_dir, test_email):
        """Import rejects if email doesn't match expected."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        with pytest.raises(ValueError, match="Email mismatch"):
            import_keys(
                export,
                password=None,
                email="different@example.com",
                keys_dir=temp_keys_dir,
            )

    def test_import_sets_permissions(self, temp_keys_dir, test_email):
        """Import sets correct file permissions."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        # Delete and reimport
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()
        (temp_keys_dir / "master.key").unlink()

        import_keys(export, password=None, keys_dir=temp_keys_dir)

        # Check permissions (0o600 = owner read/write only)
        private_mode = (temp_keys_dir / "private.key").stat().st_mode & 0o777
        master_mode = (temp_keys_dir / "master.key").stat().st_mode & 0o777
        assert private_mode == 0o600
        assert master_mode == 0o600


class TestVerifyExportFile:
    def test_verify_unencrypted(self, temp_keys_dir, test_email):
        """Verify unencrypted export file."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        info = verify_export_file(export)

        assert info["email"] == test_email
        assert info["encrypted"] is False
        assert info["has_master_key"] is True
        assert "fingerprint" in info
        assert "created_at" in info

    def test_verify_encrypted_with_password(self, temp_keys_dir, test_email):
        """Verify encrypted export file with correct password."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password="testpass", keys_dir=temp_keys_dir)

        info = verify_export_file(export, password="testpass")

        assert info["email"] == test_email
        assert info["encrypted"] is True
        assert info["has_master_key"] is True

    def test_verify_encrypted_without_password(self, temp_keys_dir, test_email):
        """Verify encrypted file without password returns partial info."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password="testpass", keys_dir=temp_keys_dir)

        info = verify_export_file(export, password=None)

        assert info["email"] == test_email
        assert info["encrypted"] is True
        assert info["has_master_key"] is None  # Unknown without decryption

    def test_verify_wrong_password(self, temp_keys_dir, test_email):
        """Verify with wrong password raises error."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        export = export_keys(test_email, password="correct", keys_dir=temp_keys_dir)

        with pytest.raises(ValueError, match="wrong password"):
            verify_export_file(export, password="wrong")

    def test_verify_invalid_format(self, temp_keys_dir):
        """Verify rejects invalid format."""
        with pytest.raises(ValueError, match="Invalid export file format"):
            verify_export_file({"format": "invalid"})

    def test_verify_without_master_key(self, temp_keys_dir, test_email):
        """Verify correctly reports missing master key."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        # No master key
        export = export_keys(test_email, password=None, keys_dir=temp_keys_dir)

        info = verify_export_file(export)

        assert info["has_master_key"] is False


class TestExportImportIntegration:
    def test_json_serialization_roundtrip(self, temp_keys_dir, test_email):
        """Export can be serialized to JSON and back."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)
        original_fingerprint = get_key_fingerprint(load_public_key(test_email, temp_keys_dir))

        # Export and serialize
        export = export_keys(test_email, password="testpass", keys_dir=temp_keys_dir)
        json_str = json.dumps(export)

        # Parse back
        parsed = json.loads(json_str)

        # Delete keys
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()
        (temp_keys_dir / "master.key").unlink()

        # Import from parsed JSON
        email, fingerprint, friend_count = import_keys(parsed, password="testpass", keys_dir=temp_keys_dir)

        assert email == test_email
        assert fingerprint == original_fingerprint

    def test_file_based_roundtrip(self, temp_keys_dir, test_email):
        """Export to file and import back."""
        generate_keypair(test_email, keys_dir=temp_keys_dir)
        generate_master_key(test_email, keys_dir=temp_keys_dir)

        export_file = temp_keys_dir / "backup.cckeys"

        # Export to file
        export = export_keys(test_email, password="filetest", keys_dir=temp_keys_dir)
        export_file.write_text(json.dumps(export))

        # Delete keys
        (temp_keys_dir / "private.key").unlink()
        (temp_keys_dir / "public.key").unlink()
        (temp_keys_dir / "master.key").unlink()

        # Read and import
        imported_data = json.loads(export_file.read_text())
        email, fingerprint, friend_count = import_keys(
            imported_data, password="filetest", keys_dir=temp_keys_dir
        )

        assert email == test_email
        # Verify keys work
        load_private_key(test_email, temp_keys_dir)
        load_master_key(test_email, temp_keys_dir)


class TestFriendKeyExportImport:
    """Tests for friend key export/import functionality."""

    def test_export_includes_friend_keys(self, test_email):
        """Export includes friend public and master keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = Path(tmpdir) / "keys"
            keys_dir.mkdir()
            friends_dir = Path(tmpdir) / "friends"
            friends_dir.mkdir()

            # Generate our keys
            generate_keypair(test_email, keys_dir=keys_dir)
            generate_master_key(test_email, keys_dir=keys_dir)

            # Add a friend with both public and master key
            (friends_dir / "alice-example-com.pub").write_bytes(b"x" * 32)
            (friends_dir / "alice-example-com.master").write_bytes(b"y" * 32)

            # Add a friend with only public key
            (friends_dir / "bob-example-com.pub").write_bytes(b"z" * 32)

            # Export
            export = export_keys(
                test_email,
                password=None,
                keys_dir=keys_dir,
                friends_dir=friends_dir,
            )

            # Verify friends are included
            assert "friends" in export["keys"]
            assert "alice-example-com" in export["keys"]["friends"]
            assert "bob-example-com" in export["keys"]["friends"]

            alice = export["keys"]["friends"]["alice-example-com"]
            assert "public_key" in alice
            assert "master_key" in alice

            bob = export["keys"]["friends"]["bob-example-com"]
            assert "public_key" in bob
            assert "master_key" not in bob

    def test_import_restores_friend_keys(self, test_email):
        """Import restores friend keys to friends directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = Path(tmpdir) / "keys"
            keys_dir.mkdir()
            friends_dir = Path(tmpdir) / "friends"
            friends_dir.mkdir()

            # Generate our keys
            generate_keypair(test_email, keys_dir=keys_dir)
            generate_master_key(test_email, keys_dir=keys_dir)

            # Add a friend
            friend_pub = b"friend_public_key_32_bytes_long!"  # 32 bytes
            friend_master = b"friend_master_key_32bytes_long!"  # 32 bytes
            (friends_dir / "friend-example-com.pub").write_bytes(friend_pub)
            (friends_dir / "friend-example-com.master").write_bytes(friend_master)

            # Export
            export = export_keys(
                test_email,
                password="test",
                keys_dir=keys_dir,
                friends_dir=friends_dir,
            )

            # Delete everything
            (keys_dir / "private.key").unlink()
            (keys_dir / "public.key").unlink()
            (keys_dir / "master.key").unlink()
            (friends_dir / "friend-example-com.pub").unlink()
            (friends_dir / "friend-example-com.master").unlink()

            # Import
            email, fingerprint, friend_count = import_keys(
                export,
                password="test",
                keys_dir=keys_dir,
                friends_dir=friends_dir,
            )

            assert friend_count == 1

            # Verify friend keys were restored
            assert (friends_dir / "friend-example-com.pub").exists()
            assert (friends_dir / "friend-example-com.master").exists()
            assert (friends_dir / "friend-example-com.pub").read_bytes() == friend_pub
            assert (friends_dir / "friend-example-com.master").read_bytes() == friend_master

    def test_verify_shows_friend_count(self, test_email):
        """Verify export file shows friend count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            keys_dir = Path(tmpdir) / "keys"
            keys_dir.mkdir()
            friends_dir = Path(tmpdir) / "friends"
            friends_dir.mkdir()

            generate_keypair(test_email, keys_dir=keys_dir)

            # Add friends
            (friends_dir / "alice.pub").write_bytes(b"x" * 32)
            (friends_dir / "bob.pub").write_bytes(b"y" * 32)
            (friends_dir / "bob.master").write_bytes(b"z" * 32)

            export = export_keys(
                test_email,
                password=None,
                keys_dir=keys_dir,
                friends_dir=friends_dir,
            )

            info = verify_export_file(export)

            assert info["friend_count"] == 2
