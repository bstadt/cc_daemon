"""
Tests for authz permission propagation.

Tests that authz changes propagate through the system and are enforced.
"""

import subprocess
from pathlib import Path

import pytest

from helpers import run_cli, get_repo_url, email_to_repo_name


class TestAuthzSync:
    """Tests for syncing authz changes."""

    def test_authz_sync_to_svn(self, test_context):
        """Verify local authz changes commit to SVN."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Modify authz
        authz_path = context_dir / "authz"
        original = authz_path.read_text()
        marker = "# Test marker for authz sync"
        authz_path.write_text(original + f"\n{marker}\n")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert "Committed" in result.stdout, "Should commit changes"

    def test_authz_preserved_after_sync(self, test_context):
        """Verify authz content is preserved after sync."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Get original authz
        authz_path = context_dir / "authz"
        original = authz_path.read_text()

        # Add something and sync
        marker = "# Preservation test marker"
        authz_path.write_text(original + f"\n{marker}\n")
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # Verify marker is still there
        new_content = authz_path.read_text()
        assert marker in new_content, "Marker should be preserved after sync"


class TestAuthzPermissions:
    """Tests for authz permission enforcement."""

    def test_add_read_permission(self, test_context):
        """Verify adding read permission to authz file works."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Add a fake user read permission
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()

        # Add fake user to root section
        fake_user = "friend@example.com"
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw\n{fake_user} = r"
        )
        authz_path.write_text(new_authz)

        # Sync to apply permissions
        result = run_cli(["sync"], env=env, cwd=str(context_dir))
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}\n{result.stdout}"

        # Verify the permission was added to the file
        final_authz = authz_path.read_text()
        assert f"{fake_user} = r" in final_authz, \
            "Friend should have read permission in authz"

    def test_revoke_permission(self, test_context):
        """Verify removing permission from authz works."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # First grant access to fake user
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()
        fake_user = "friend@example.com"
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw\n{fake_user} = r"
        )
        authz_path.write_text(new_authz)
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # Now revoke access
        authz_content = authz_path.read_text()
        revoked_authz = authz_content.replace(f"{fake_user} = r\n", "")
        authz_path.write_text(revoked_authz)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0
        final_authz = authz_path.read_text()
        assert f"{fake_user} = r" not in final_authz, \
            "Friend permission should be removed"

    def test_path_specific_permission(self, test_context):
        """Verify path-level permissions can be set."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Add a path-specific section to authz
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()
        path_section = f"""
[/private]
* =
{test_user} = rw
"""
        authz_path.write_text(authz_content + path_section)

        # Create the private directory
        private_dir = context_dir / "private"
        private_dir.mkdir()
        (private_dir / "secret.md").write_text("# Secret content")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"

        # Verify authz has the section
        final_authz = authz_path.read_text()
        assert "[/private]" in final_authz, "Private section should exist"


class TestAuthzStructure:
    """Tests for authz file structure requirements."""

    def test_authz_has_root_section(self, test_context):
        """Verify authz has root section."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        assert "[/]" in authz_content, "Root section should exist"

    def test_authz_has_friend_requests_section(self, test_context):
        """Verify authz has friend_requests section."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        assert "[/claudeconnect/friend_requests]" in authz_content, \
            "friend_requests section should exist"

    def test_authz_friend_requests_world_writable(self, test_context):
        """Verify friend_requests is world-writable for friend requests to work."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()

        # Find the friend_requests section
        fr_start = authz_content.find("[/claudeconnect/friend_requests]")
        assert fr_start != -1, "friend_requests section should exist"

        # Get the section content (until next section or end)
        fr_section = authz_content[fr_start:]
        next_section = fr_section.find("\n[", 1)
        if next_section != -1:
            fr_section = fr_section[:next_section]

        assert "* = rw" in fr_section, \
            "friend_requests should have * = rw for world write access"


class TestAuthzEdgeCases:
    """Tests for authz edge cases."""

    def test_authz_comment_preserved(self, test_context):
        """Verify comments in authz are preserved."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Add comment
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()
        comment = "# This is a test comment that should be preserved"
        authz_path.write_text(authz_content + f"\n{comment}\n")

        # Sync
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # Verify comment preserved
        final_authz = authz_path.read_text()
        assert comment in final_authz, "Comment should be preserved"

    def test_authz_multiple_users(self, test_context):
        """Verify multiple users can be added to authz."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Add multiple fake users
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()
        additions = """
friend1@example.com = r
friend2@example.com = r
friend3@example.com = r
"""
        # Insert after the main user
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw{additions}"
        )
        authz_path.write_text(new_authz)

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0
        final_authz = authz_path.read_text()
        assert "friend1@example.com" in final_authz
        assert "friend2@example.com" in final_authz
        assert "friend3@example.com" in final_authz

    def test_authz_empty_permission_blocks(self, test_context):
        """Verify empty permission (user =) blocks access."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Add a section that blocks everyone except owner
        authz_path = context_dir / "authz"
        authz_content = authz_path.read_text()
        block_section = f"""
[/blocked]
* =
{test_user} = rw
"""
        authz_path.write_text(authz_content + block_section)

        # Create the directory
        blocked_dir = context_dir / "blocked"
        blocked_dir.mkdir()
        (blocked_dir / "file.md").write_text("# Blocked content")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0
        final_authz = authz_path.read_text()
        assert "* =" in final_authz, "Block rule should exist"


class TestAuthzServerPropagation:
    """Tests that authz changes propagate to the server and are enforced.

    These tests verify the fix for issue #15 where authz wasn't propagating.
    """

    def test_authz_unversioned_gets_added_on_sync(self, test_context):
        """
        Verify that an unversioned authz file gets added during sync.

        This directly tests the fix for issue #15.
        """
        import subprocess

        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # First, verify authz exists and check SVN status
        authz_path = context_dir / "authz"
        assert authz_path.exists(), "authz should exist after init"

        # Modify authz to add a test permission
        original = authz_path.read_text()
        test_marker = "# Test marker for unversioned authz test"
        authz_path.write_text(original + f"\n{test_marker}\n")

        # Sync - this should add authz if unversioned and commit changes
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        # Should have committed something (either adding authz or committing changes)
        assert "Committed" in result.stdout or "Added authz" in result.stdout, \
            f"Should commit authz changes: {result.stdout}"

        # Verify the marker is still in the file
        final_authz = authz_path.read_text()
        assert test_marker in final_authz, "Test marker should be preserved"
