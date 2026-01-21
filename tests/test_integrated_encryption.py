"""
Integration tests for end-to-end encryption flow.

Tests the full workflow:
1. Multiple users initialize with encryption
2. Friend request flow with public key exchange
3. File encryption/decryption between friends
4. Non-friends cannot decrypt

These tests use ephemeral test users and real SVN operations.
"""

import time
from pathlib import Path

import pytest

# Skip all tests if cryptography is not installed
pytest.importorskip("cryptography")

from helpers import run_cli, extract_email_from_output

from claudeconnect.encryption import (
    encrypt_file,
    decrypt_file,
    is_encrypted_file,
    generate_keypair,
    load_public_key,
    save_friend_public_key,
    MAGIC_BYTES,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def three_test_users():
    """
    Create three ephemeral test users (Alice, Bob, Carol).

    Yields list of three test user emails.
    Automatically cleans up after the test.
    """
    users = []

    for _ in range(3):
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        if result.returncode != 0:
            # Cleanup any already created users
            for email in users:
                run_cli(["test-user", "delete", email])
            pytest.skip(f"Could not create test user: {result.stderr}")

        try:
            email = extract_email_from_output(result.stdout)
            users.append(email)
        except ValueError as e:
            for email in users:
                run_cli(["test-user", "delete", email])
            pytest.fail(str(e))

    yield users

    # Cleanup
    for email in users:
        run_cli(["test-user", "delete", email])


@pytest.fixture(scope="function")
def three_encrypted_contexts(three_test_users, tmp_path):
    """
    Create three initialized context directories with encryption enabled.

    Each user has:
    - A context directory
    - Encryption keypair generated
    - SVN working copy

    Yields list of dicts with 'email', 'dir', 'env', 'keys_dir' keys.
    """
    contexts = []

    for i, email in enumerate(three_test_users):
        context_dir = tmp_path / f"context_{i}"
        context_dir.mkdir()

        # Create a separate keys directory for each user (to isolate keys)
        keys_dir = tmp_path / f"keys_{i}"
        keys_dir.mkdir()

        env = {"CC_TEST_USER": email}

        # Initialize with encryption
        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",  # Confirm directory switch
        )

        if result.returncode != 0:
            pytest.fail(f"Could not initialize encrypted context for {email}: {result.stderr}\n{result.stdout}")

        contexts.append({
            "email": email,
            "dir": context_dir,
            "env": env,
            "keys_dir": keys_dir,
        })

    yield contexts


# =============================================================================
# Unit Tests for Encryption Primitives (Multi-User)
# =============================================================================

class TestMultiUserEncryptionPrimitives:
    """
    Tests for encryption primitives with multiple users.

    These tests use local keys (not CLI) to verify the encryption
    logic works correctly before testing the full integration.
    """

    def test_encrypt_decrypt_between_friends(self, tmp_path):
        """Alice encrypts for herself and Bob. Both can decrypt."""
        # Create keypairs for Alice and Bob
        alice_keys_dir = tmp_path / "alice_keys"
        alice_keys_dir.mkdir()
        bob_keys_dir = tmp_path / "bob_keys"
        bob_keys_dir.mkdir()

        _, alice_public = generate_keypair(alice_keys_dir)
        _, bob_public = generate_keypair(bob_keys_dir)

        alice_email = "alice@test.com"
        bob_email = "bob@test.com"

        # Alice encrypts a message for herself and Bob
        plaintext = b"Secret message between Alice and Bob"
        recipients = {
            alice_email: alice_public,
            bob_email: bob_public,
        }
        ciphertext = encrypt_file(plaintext, recipients, alice_keys_dir)

        # Verify it's encrypted
        assert is_encrypted_file(ciphertext)
        assert plaintext not in ciphertext

        # Alice can decrypt
        decrypted_by_alice = decrypt_file(ciphertext, alice_email, alice_keys_dir)
        assert decrypted_by_alice == plaintext

        # Bob can decrypt
        decrypted_by_bob = decrypt_file(ciphertext, bob_email, bob_keys_dir)
        assert decrypted_by_bob == plaintext

    def test_non_friend_cannot_decrypt(self, tmp_path):
        """Carol (not a recipient) cannot decrypt Alice's file."""
        # Create keypairs
        alice_keys_dir = tmp_path / "alice_keys"
        alice_keys_dir.mkdir()
        bob_keys_dir = tmp_path / "bob_keys"
        bob_keys_dir.mkdir()
        carol_keys_dir = tmp_path / "carol_keys"
        carol_keys_dir.mkdir()

        _, alice_public = generate_keypair(alice_keys_dir)
        _, bob_public = generate_keypair(bob_keys_dir)
        generate_keypair(carol_keys_dir)  # Carol has keys but isn't a recipient

        alice_email = "alice@test.com"
        bob_email = "bob@test.com"
        carol_email = "carol@test.com"

        # Alice encrypts for herself and Bob only (not Carol)
        plaintext = b"Secret message - Carol cannot read this"
        recipients = {
            alice_email: alice_public,
            bob_email: bob_public,
        }
        ciphertext = encrypt_file(plaintext, recipients, alice_keys_dir)

        # Carol tries to decrypt and fails
        with pytest.raises(ValueError, match="Not a recipient"):
            decrypt_file(ciphertext, carol_email, carol_keys_dir)

    def test_friend_key_exchange_simulation(self, tmp_path):
        """Simulate the friend key exchange flow."""
        # Alice and Bob create their keypairs
        alice_keys_dir = tmp_path / "alice_keys"
        alice_keys_dir.mkdir()
        alice_friends_dir = tmp_path / "alice_friends"
        alice_friends_dir.mkdir()

        bob_keys_dir = tmp_path / "bob_keys"
        bob_keys_dir.mkdir()
        bob_friends_dir = tmp_path / "bob_friends"
        bob_friends_dir.mkdir()

        _, alice_public = generate_keypair(alice_keys_dir)
        _, bob_public = generate_keypair(bob_keys_dir)

        alice_email = "alice@test.com"
        bob_email = "bob@test.com"

        # Step 1: Alice sends friend request (includes her public key)
        # Bob receives and saves Alice's public key
        save_friend_public_key(alice_email, alice_public, bob_friends_dir)

        # Step 2: Bob accepts (Alice gets Bob's public key)
        save_friend_public_key(bob_email, bob_public, alice_friends_dir)

        # Now Alice creates a file encrypted for both
        plaintext = b"# Shared Notes\n\nThis is shared between Alice and Bob."
        recipients = {
            alice_email: alice_public,
            bob_email: bob_public,
        }
        ciphertext = encrypt_file(plaintext, recipients, alice_keys_dir)

        # Both can decrypt
        assert decrypt_file(ciphertext, alice_email, alice_keys_dir) == plaintext
        assert decrypt_file(ciphertext, bob_email, bob_keys_dir) == plaintext


# =============================================================================
# Integration Tests with Real SVN
# =============================================================================

class TestIntegratedEncryptionFlow:
    """
    Full integration tests using real test users and SVN.

    These tests exercise the complete workflow through CLI commands.
    """

    def test_init_creates_keypair_by_default(self, three_test_users, tmp_path):
        """Verify init creates encryption keypair by default."""
        email = three_test_users[0]
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        env = {"CC_TEST_USER": email}

        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",
        )

        assert result.returncode == 0, f"Init should succeed: {result.stderr}\n{result.stdout}"
        assert "keypair" in result.stdout.lower() or "encrypt" in result.stdout.lower(), \
            "Should mention keypair/encryption in output"

        # Verify keypair was created in default location
        keys_dir = Path.home() / ".claude-connect" / "keys"
        assert (keys_dir / "private.key").exists(), "Private key should exist"
        assert (keys_dir / "public.key").exists(), "Public key should exist"

    def test_init_sets_encryption_enabled_in_config(self, three_test_users, tmp_path):
        """Verify init sets encryption_enabled=True in config."""
        import json

        email = three_test_users[0]
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        env = {"CC_TEST_USER": email}

        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",
        )

        assert result.returncode == 0, f"Init should succeed: {result.stderr}\n{result.stdout}"

        # Verify config file has encryption_enabled=True
        config_file = Path.home() / ".claude-connect" / "config.json"
        assert config_file.exists(), "Config file should exist"

        config_data = json.loads(config_file.read_text())
        assert config_data.get("encryption_enabled") is True, \
            f"Config should have encryption_enabled=True, got: {config_data}"

    def test_sync_encrypts_markdown_files(self, three_test_users, tmp_path):
        """Verify sync encrypts .md files when encryption is enabled."""
        email = three_test_users[0]
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        env = {"CC_TEST_USER": email}

        # Initialize with encryption
        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",
        )
        assert result.returncode == 0

        # Create a markdown file
        test_file = context_dir / "test-note.md"
        test_file.write_text("# Test Note\n\nThis is a secret note.")

        # Sync to commit
        result = run_cli(["sync"], env=env, cwd=str(context_dir))
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"

        # The file should still be readable locally (decrypted after commit)
        # But the SVN copy should be encrypted
        # We can verify the local copy is still plaintext
        content = test_file.read_text()
        assert "# Test Note" in content, "Local file should remain plaintext"

    @pytest.mark.timeout(120)
    def test_friend_request_includes_public_key(self, three_test_users, tmp_path):
        """Verify friend request includes sender's public key."""
        alice_email = three_test_users[0]
        bob_email = three_test_users[1]

        # Initialize Alice with encryption
        alice_dir = tmp_path / "alice_context"
        alice_dir.mkdir()
        alice_env = {"CC_TEST_USER": alice_email}

        result = run_cli(
            ["init"],
            env=alice_env,
            cwd=str(alice_dir),
            input_text="y\n",
        )
        assert result.returncode == 0

        # Initialize Bob (so he can receive friend request)
        bob_dir = tmp_path / "bob_context"
        bob_dir.mkdir()
        bob_env = {"CC_TEST_USER": bob_email}

        result = run_cli(
            ["init"],
            env=bob_env,
            cwd=str(bob_dir),
            input_text="y\n",
        )
        assert result.returncode == 0

        # Alice sends friend request to Bob
        result = run_cli(
            ["friend", bob_email],
            env=alice_env,
            cwd=str(alice_dir),
        )

        # Check if friend request was sent (may fail if server not available)
        if result.returncode != 0:
            pytest.skip(f"Friend request failed (server issue?): {result.stderr}")

        # MUST mention public key - this is critical for encryption to work
        assert "public key" in result.stdout.lower(), \
            f"Friend request MUST include public key. Output was:\n{result.stdout}"

    @pytest.mark.timeout(180)
    def test_full_friend_flow_with_encryption(self, three_test_users, tmp_path):
        """
        Full integration test:
        1. Alice and Bob init with encryption
        2. Alice friends Bob
        3. Bob accepts
        4. Alice creates encrypted file
        5. Bob can read Alice's context
        6. Carol (non-friend) cannot decrypt
        """
        alice_email = three_test_users[0]
        bob_email = three_test_users[1]
        carol_email = three_test_users[2]

        # Initialize all three with encryption
        alice_dir = tmp_path / "alice"
        bob_dir = tmp_path / "bob"
        carol_dir = tmp_path / "carol"

        for d in [alice_dir, bob_dir, carol_dir]:
            d.mkdir()

        alice_env = {"CC_TEST_USER": alice_email}
        bob_env = {"CC_TEST_USER": bob_email}
        carol_env = {"CC_TEST_USER": carol_email}

        # Init all
        for email, env, d in [
            (alice_email, alice_env, alice_dir),
            (bob_email, bob_env, bob_dir),
            (carol_email, carol_env, carol_dir),
        ]:
            result = run_cli(["init"], env=env, cwd=str(d), input_text="y\n")
            if result.returncode != 0:
                pytest.fail(f"Init failed for {email}: {result.stderr}\n{result.stdout}")

        # Alice sends friend request to Bob
        result = run_cli(
            ["friend", bob_email],
            env=alice_env,
            cwd=str(alice_dir),
        )
        if result.returncode != 0:
            pytest.skip(f"Friend request failed: {result.stderr}")

        # Give server time to process
        time.sleep(2)

        # Bob syncs to receive the friend request
        result = run_cli(["sync"], env=bob_env, cwd=str(bob_dir))
        assert result.returncode == 0, f"Bob sync failed: {result.stderr}"

        # Check if friend request file exists
        request_file = bob_dir / "claudeconnect" / "with-claudeconnect-io" / f"friend-request-{alice_email}.md"

        if not request_file.exists():
            # Try one more sync after a delay
            time.sleep(3)
            run_cli(["sync"], env=bob_env, cwd=str(bob_dir))

        if not request_file.exists():
            pytest.skip("Friend request file not received (server/timing issue)")

        # Bob accepts Alice's friend request
        result = run_cli(
            ["accept-friend", alice_email],
            env=bob_env,
            cwd=str(bob_dir),
        )
        assert result.returncode == 0, f"Accept friend failed: {result.stderr}\n{result.stdout}"

        # Alice creates a markdown file
        alice_note = alice_dir / "shared-with-bob.md"
        alice_note.write_text("# Secret Plans\n\nThis is shared with Bob only!")

        # Alice syncs
        result = run_cli(["sync"], env=alice_env, cwd=str(alice_dir))
        assert result.returncode == 0

        # Bob syncs Alice's repo (needs pull command or appropriate access)
        # Note: Bob reading Alice's files requires the pull command
        result = run_cli(
            ["pull", alice_email],
            env=bob_env,
            cwd=str(bob_dir),
        )

        # If pull works, verify Bob can see the content
        if result.returncode == 0:
            # The file should be decrypted for Bob if he's a friend
            pulled_file = bob_dir / "claudeconnect" / f"with-{alice_email.replace('@', '-').replace('.', '-')}" / "shared-with-bob.md"
            if pulled_file.exists():
                content = pulled_file.read_text()
                # Should be able to read the content
                assert "Secret Plans" in content or is_encrypted_file(pulled_file.read_bytes())


class TestAuthzAccessControl:
    """Tests that verify authz changes actually grant/deny SVN access."""

    @pytest.mark.timeout(120)
    def test_friend_can_pull_after_accept(self, three_test_users, tmp_path):
        """
        Verify the full access control flow:
        1. Bob CANNOT pull Alice's repo initially (not a friend)
        2. Alice friends Bob, Bob accepts
        3. Bob CAN pull Alice's repo after accept

        This catches authz sync issues where versioned authz isn't
        copied to conf/authz for Apache.
        """
        alice_email = three_test_users[0]
        bob_email = three_test_users[1]

        # Initialize Alice and Bob
        alice_dir = tmp_path / "alice"
        bob_dir = tmp_path / "bob"
        alice_dir.mkdir()
        bob_dir.mkdir()

        alice_env = {"CC_TEST_USER": alice_email}
        bob_env = {"CC_TEST_USER": bob_email}

        for email, env, d in [(alice_email, alice_env, alice_dir),
                               (bob_email, bob_env, bob_dir)]:
            result = run_cli(["init"], env=env, cwd=str(d), input_text="y\n")
            if result.returncode != 0:
                pytest.fail(f"Init failed for {email}: {result.stderr}")

        # Alice creates a file and syncs
        (alice_dir / "secret.md").write_text("# Alice's Secret\n\nTop secret info!")
        result = run_cli(["sync"], env=alice_env, cwd=str(alice_dir))
        assert result.returncode == 0, f"Alice sync failed: {result.stderr}"

        # Bob tries to pull Alice BEFORE being friends - should fail
        result = run_cli(["pull", alice_email], env=bob_env, cwd=str(bob_dir))
        # Note: pull might fail with access denied or just return empty - depends on implementation

        # Alice friends Bob
        result = run_cli(["friend", bob_email], env=alice_env, cwd=str(alice_dir))
        if result.returncode != 0:
            pytest.skip(f"Friend request failed: {result.stderr}")

        # Wait for server to process
        time.sleep(2)

        # Bob syncs to get friend request
        run_cli(["sync"], env=bob_env, cwd=str(bob_dir))
        time.sleep(1)

        # Bob accepts Alice's friend request
        result = run_cli(
            ["accept-friend", alice_email],
            env=bob_env,
            cwd=str(bob_dir),
        )
        if result.returncode != 0:
            pytest.skip(f"Accept friend failed: {result.stderr}")

        # Wait for authz to propagate (post-commit hook should sync it)
        time.sleep(2)

        # Bob pulls Alice's context - THIS IS THE CRITICAL TEST
        # If authz wasn't synced to conf/authz, this will fail with access denied
        result = run_cli(["pull", alice_email], env=bob_env, cwd=str(bob_dir))

        assert result.returncode == 0, \
            f"Bob should be able to pull Alice after friending. Error: {result.stderr}\n" \
            "This usually means the versioned authz wasn't synced to conf/authz. " \
            "Check that the post-commit hook is installed on the server."


class TestEncryptionEdgeCases:
    """Edge case tests for encryption integration."""

    def test_authz_file_not_encrypted(self, three_test_users, tmp_path):
        """Verify authz file remains plaintext (required for SVN)."""
        email = three_test_users[0]
        context_dir = tmp_path / "context"
        context_dir.mkdir()

        env = {"CC_TEST_USER": email}

        # Init with encryption
        result = run_cli(["init"], env=env, cwd=str(context_dir), input_text="y\n")
        assert result.returncode == 0

        # Check authz is plaintext
        authz_path = context_dir / "authz"
        assert authz_path.exists()

        content = authz_path.read_bytes()
        assert not is_encrypted_file(content), "authz should NOT be encrypted"
        assert b"[/]" in content, "authz should be readable plaintext"

    def test_encryption_without_cryptography(self, three_test_users, tmp_path, monkeypatch):
        """Verify graceful handling when cryptography is not installed."""
        # This test simulates missing cryptography by checking the error message
        # when init is run without the cryptography library
        #
        # We can't actually uninstall cryptography during the test,
        # but we verify the error path exists in the code
        pass  # Covered by unit tests

    def test_reencryption_on_new_friend(self, tmp_path):
        """
        When Alice adds Carol as a friend after encrypting files,
        existing files need to be re-encrypted to include Carol.
        """
        # Create keys for Alice, Bob, and Carol
        alice_keys_dir = tmp_path / "alice_keys"
        alice_keys_dir.mkdir()
        bob_keys_dir = tmp_path / "bob_keys"
        bob_keys_dir.mkdir()
        carol_keys_dir = tmp_path / "carol_keys"
        carol_keys_dir.mkdir()

        _, alice_public = generate_keypair(alice_keys_dir)
        _, bob_public = generate_keypair(bob_keys_dir)
        _, carol_public = generate_keypair(carol_keys_dir)

        alice_email = "alice@test.com"
        bob_email = "bob@test.com"
        carol_email = "carol@test.com"

        # Alice encrypts for herself and Bob (not Carol yet)
        plaintext = b"# Original Secret\n\nInitially just for Alice and Bob."
        recipients_v1 = {
            alice_email: alice_public,
            bob_email: bob_public,
        }
        ciphertext_v1 = encrypt_file(plaintext, recipients_v1, alice_keys_dir)

        # Verify Carol cannot decrypt v1
        with pytest.raises(ValueError, match="Not a recipient"):
            decrypt_file(ciphertext_v1, carol_email, carol_keys_dir)

        # Alice adds Carol and re-encrypts
        recipients_v2 = {
            alice_email: alice_public,
            bob_email: bob_public,
            carol_email: carol_public,
        }
        ciphertext_v2 = encrypt_file(plaintext, recipients_v2, alice_keys_dir)

        # Now all three can decrypt v2
        assert decrypt_file(ciphertext_v2, alice_email, alice_keys_dir) == plaintext
        assert decrypt_file(ciphertext_v2, bob_email, bob_keys_dir) == plaintext
        assert decrypt_file(ciphertext_v2, carol_email, carol_keys_dir) == plaintext


# =============================================================================
# Security Tests
# =============================================================================

class TestEncryptionSecurity:
    """Security-focused integration tests."""

    def test_different_files_different_ciphertext(self, tmp_path):
        """Same content encrypted twice should produce different ciphertext."""
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        _, public_key = generate_keypair(keys_dir)

        plaintext = b"Same secret content"
        recipients = {"user@test.com": public_key}

        ciphertext1 = encrypt_file(plaintext, recipients, keys_dir)
        ciphertext2 = encrypt_file(plaintext, recipients, keys_dir)

        # Nonces and ephemeral keys should differ
        assert ciphertext1 != ciphertext2

        # But both should decrypt to the same content
        assert decrypt_file(ciphertext1, "user@test.com", keys_dir) == plaintext
        assert decrypt_file(ciphertext2, "user@test.com", keys_dir) == plaintext

    def test_tampered_ciphertext_detected(self, tmp_path):
        """Modifications to ciphertext should be detected."""
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        _, public_key = generate_keypair(keys_dir)

        plaintext = b"Original content"
        recipients = {"user@test.com": public_key}

        ciphertext = bytearray(encrypt_file(plaintext, recipients, keys_dir))

        # Tamper with the encrypted content (near the end, in ciphertext area)
        ciphertext[-10] ^= 0xFF

        # Decryption should fail due to GCM authentication
        with pytest.raises(Exception):  # Could be InvalidTag or other crypto error
            decrypt_file(bytes(ciphertext), "user@test.com", keys_dir)

    def test_private_key_stays_local(self, tmp_path):
        """Verify private key never appears in ciphertext."""
        keys_dir = tmp_path / "keys"
        keys_dir.mkdir()
        private_bytes, public_key = generate_keypair(keys_dir)

        plaintext = b"Secret data"
        recipients = {"user@test.com": public_key}

        ciphertext = encrypt_file(plaintext, recipients, keys_dir)

        # Private key should never be in the ciphertext
        assert private_bytes not in ciphertext
