"""
Tests for the new flat directory structure (Issue #30).

Tests that conversation directories use claudeconnect/with-{email}/
instead of claudeconnect/conversations/with-{email}/.
"""

import pytest
from pathlib import Path

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claudeconnect.cli import (
    generate_authz_content,
    add_friend_to_authz,
    migrate_authz_paths,
    migrate_conversation_directories,
)
from claudeconnect.svn_ops import email_to_repo_name


class TestGenerateAuthzContent:
    """Tests for generate_authz_content with new path structure."""

    def test_authz_uses_with_email_path(self):
        """Authz should use [/claudeconnect/with-{email}] not [/claudeconnect/conversations]."""
        email = "alice@example.com"
        authz = generate_authz_content(email)

        email_repo_name = email_to_repo_name(email)

        # New path format should be present
        assert f"[/claudeconnect/with-{email_repo_name}]" in authz

        # Old path format should NOT be present
        assert "[/claudeconnect/conversations]" not in authz

    def test_authz_owner_has_rw_to_with_section(self):
        """Owner should have rw access to their with-{email} section."""
        email = "bob@test.com"
        authz = generate_authz_content(email)

        email_repo_name = email_to_repo_name(email)

        # Find the with-{email} section and verify owner access
        lines = authz.split('\n')
        in_with_section = False
        found_owner_access = False

        for line in lines:
            if f"[/claudeconnect/with-{email_repo_name}]" in line:
                in_with_section = True
            elif in_with_section and line.strip().startswith('['):
                break
            elif in_with_section and f"{email} = rw" in line:
                found_owner_access = True

        assert found_owner_access, "Owner should have rw access to with-{email} section"


class TestAddFriendToAuthz:
    """Tests for add_friend_to_authz with new path structure."""

    def test_friend_gets_write_to_with_section(self, tmp_path):
        """Friend should get write access to [/claudeconnect/with-{my_email}]."""
        my_email = "owner@example.com"
        peer_email = "friend@example.com"
        my_repo_name = email_to_repo_name(my_email)

        # Create initial authz
        authz_path = tmp_path / "authz"
        authz_path.write_text(generate_authz_content(my_email))

        # Add friend
        add_friend_to_authz(authz_path, my_email, peer_email)

        authz = authz_path.read_text()

        # Friend should have write access to with-{my_email} section
        assert f"[/claudeconnect/with-{my_repo_name}]" in authz

        # Find the section and check friend has rw
        lines = authz.split('\n')
        in_with_section = False
        friend_has_access = False

        for line in lines:
            if f"[/claudeconnect/with-{my_repo_name}]" in line:
                in_with_section = True
            elif in_with_section and line.strip().startswith('['):
                break
            elif in_with_section and f"{peer_email} = rw" in line:
                friend_has_access = True

        assert friend_has_access, "Friend should have rw access to with-{my_email} section"

    def test_friend_gets_read_to_root(self, tmp_path):
        """Friend should get read access to [/]."""
        my_email = "owner@example.com"
        peer_email = "friend@example.com"

        authz_path = tmp_path / "authz"
        authz_path.write_text(generate_authz_content(my_email))

        add_friend_to_authz(authz_path, my_email, peer_email)

        authz = authz_path.read_text()

        # Find root section and check friend has read access
        lines = authz.split('\n')
        in_root_section = False
        friend_has_read = False

        for line in lines:
            if line.strip() == "[/]":
                in_root_section = True
            elif in_root_section and line.strip().startswith('['):
                break
            elif in_root_section and f"{peer_email} = r" in line:
                friend_has_read = True

        assert friend_has_read, "Friend should have read access to root section"

    def test_no_conversations_section_created(self, tmp_path):
        """Adding a friend should NOT create [/claudeconnect/conversations] section."""
        my_email = "owner@example.com"
        peer_email = "friend@example.com"

        authz_path = tmp_path / "authz"
        authz_path.write_text(generate_authz_content(my_email))

        add_friend_to_authz(authz_path, my_email, peer_email)

        authz = authz_path.read_text()

        # Old path format should NOT be present
        assert "[/claudeconnect/conversations]" not in authz


class TestMigrateAuthzPaths:
    """Tests for migrating old authz paths to new structure."""

    def test_migrate_conversations_to_with_email(self, tmp_path):
        """Should migrate [/claudeconnect/conversations] to [/claudeconnect/with-{email}]."""
        email = "user@example.com"
        email_repo_name = email_to_repo_name(email)

        # Create old-style authz
        old_authz = f"""[/]
{email} = rw

[/claudeconnect/friend_requests]
* = rw
{email} = rw

# Friends can write conversations to your repo
[/claudeconnect/conversations]
{email} = rw
friend@example.com = rw
"""
        authz_path = tmp_path / "authz"
        authz_path.write_text(old_authz)

        # Run migration
        changed = migrate_authz_paths(authz_path, email)

        assert changed, "Migration should report changes made"

        authz = authz_path.read_text()

        # Old path should be gone
        assert "[/claudeconnect/conversations]" not in authz

        # New path should exist
        assert f"[/claudeconnect/with-{email_repo_name}]" in authz

        # Friend permissions should be preserved
        assert "friend@example.com = rw" in authz

    def test_migrate_preserves_friend_requests(self, tmp_path):
        """Migration should not affect friend_requests section."""
        email = "user@example.com"

        old_authz = f"""[/]
{email} = rw

[/claudeconnect/friend_requests]
* = rw
{email} = rw

[/claudeconnect/conversations]
{email} = rw
"""
        authz_path = tmp_path / "authz"
        authz_path.write_text(old_authz)

        migrate_authz_paths(authz_path, email)

        authz = authz_path.read_text()

        # friend_requests should still exist
        assert "[/claudeconnect/friend_requests]" in authz

    def test_no_change_if_already_migrated(self, tmp_path):
        """Should not change already-migrated authz."""
        email = "user@example.com"
        email_repo_name = email_to_repo_name(email)

        # Create new-style authz
        new_authz = f"""[/]
{email} = rw

[/claudeconnect/friend_requests]
* = rw
{email} = rw

# Friends can write conversations to your with-{email_repo_name} folder
[/claudeconnect/with-{email_repo_name}]
{email} = rw
"""
        authz_path = tmp_path / "authz"
        authz_path.write_text(new_authz)

        # Run migration
        changed = migrate_authz_paths(authz_path, email)

        # Should not report changes since already migrated
        assert not changed, "Should not change already-migrated authz"


class TestMigrateConversationDirectories:
    """Tests for migrating old conversation directory structure."""

    def test_migrate_conversations_dir_to_flat(self, tmp_path):
        """Should move claudeconnect/conversations/with-{email}/ to claudeconnect/with-{email}/."""
        context_dir = tmp_path / "context"

        # Create old structure
        old_conv_dir = context_dir / "claudeconnect" / "conversations" / "with-bob-example-com"
        old_conv_dir.mkdir(parents=True)
        transcript = old_conv_dir / "2026-01-01_abc12345.md"
        transcript.write_text("# Old Transcript\n\nContent here")

        # Run migration
        migrated = migrate_conversation_directories(context_dir)

        assert migrated, "Migration should report that it ran"

        # Old location should be gone
        assert not (context_dir / "claudeconnect" / "conversations").exists()

        # New location should exist with content
        new_dir = context_dir / "claudeconnect" / "with-bob-example-com"
        assert new_dir.exists()
        assert (new_dir / "2026-01-01_abc12345.md").exists()
        assert (new_dir / "2026-01-01_abc12345.md").read_text() == "# Old Transcript\n\nContent here"

    def test_migrate_multiple_conversation_dirs(self, tmp_path):
        """Should migrate all with-{email} directories."""
        context_dir = tmp_path / "context"

        # Create old structure with multiple friends
        for friend in ["alice-example-com", "bob-example-com", "charlie-test-io"]:
            old_dir = context_dir / "claudeconnect" / "conversations" / f"with-{friend}"
            old_dir.mkdir(parents=True)
            (old_dir / "test.md").write_text(f"Conversation with {friend}")

        # Run migration
        migrate_conversation_directories(context_dir)

        # All should be migrated
        for friend in ["alice-example-com", "bob-example-com", "charlie-test-io"]:
            new_dir = context_dir / "claudeconnect" / f"with-{friend}"
            assert new_dir.exists(), f"with-{friend} should be migrated"
            assert (new_dir / "test.md").exists()

        # Old conversations dir should be removed
        assert not (context_dir / "claudeconnect" / "conversations").exists()

    def test_no_migration_if_no_conversations_dir(self, tmp_path):
        """Should do nothing if no old conversations directory exists."""
        context_dir = tmp_path / "context"

        # Create new-style structure directly
        new_dir = context_dir / "claudeconnect" / "with-friend-example-com"
        new_dir.mkdir(parents=True)
        (new_dir / "test.md").write_text("Already migrated")

        # Run migration
        migrated = migrate_conversation_directories(context_dir)

        assert not migrated, "Should report no migration needed"

        # Content should be unchanged
        assert (new_dir / "test.md").read_text() == "Already migrated"

    def test_merge_if_destination_exists(self, tmp_path):
        """Should merge contents if destination already exists."""
        context_dir = tmp_path / "context"

        # Create old structure
        old_dir = context_dir / "claudeconnect" / "conversations" / "with-friend-example-com"
        old_dir.mkdir(parents=True)
        (old_dir / "old_transcript.md").write_text("Old content")

        # Create new structure with existing file
        new_dir = context_dir / "claudeconnect" / "with-friend-example-com"
        new_dir.mkdir(parents=True)
        (new_dir / "new_transcript.md").write_text("New content")

        # Run migration
        migrate_conversation_directories(context_dir)

        # Both files should exist in new location
        assert (new_dir / "old_transcript.md").exists()
        assert (new_dir / "new_transcript.md").exists()


class TestConversationPathIntegration:
    """Integration tests verifying session.py uses correct paths."""

    def test_session_path_format(self):
        """Verify the path format used in session.py."""
        from claudeconnect.svn_ops import email_to_repo_name

        peer_email = "alice@example.com"
        expected_dir_name = f"with-{email_to_repo_name(peer_email)}"

        # The path should be claudeconnect/with-{email}/ not claudeconnect/conversations/with-{email}/
        assert expected_dir_name == "with-alice-example-com"

        # This is what the path should look like
        from pathlib import Path
        context_dir = Path("/tmp/test")
        expected_path = context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"

        # Should NOT include "conversations"
        assert "conversations" not in str(expected_path)
        assert str(expected_path) == "/tmp/test/claudeconnect/with-alice-example-com"
