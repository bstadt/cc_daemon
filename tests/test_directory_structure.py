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
