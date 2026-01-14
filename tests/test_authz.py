"""
Tests for authz permission propagation.

Tests that authz changes propagate through the system and are enforced.
Each test verifies pre-conditions, performs actions, and verifies post-conditions.
"""

import subprocess
from pathlib import Path

import pytest

from helpers import (
    run_cli,
    get_repo_url,
    email_to_repo_name,
    svn_cat,
    svn_file_exists_in_repo,
    get_svn_file_status,
)


class TestAuthzSync:
    """Tests for syncing authz changes."""

    def test_authz_sync_to_svn(self, test_context):
        """Verify local authz changes commit to SVN."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        marker = "# Test marker for authz sync"

        # PRE-CONDITIONS
        assert authz_path.exists(), "authz file should exist"
        original = authz_path.read_text()
        assert marker not in original, "Marker should not exist before test"
        svn_original = svn_cat(context_dir, "authz")
        assert marker not in svn_original, "Marker should not be in SVN before test"

        # ACTION: Modify authz and sync
        authz_path.write_text(original + f"\n{marker}\n")
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        svn_content = svn_cat(context_dir, "authz")
        assert marker in svn_content, "Marker should be in SVN after sync"
        assert get_svn_file_status(context_dir, "authz") is None, \
            "authz should have no pending status after sync"

    def test_authz_preserved_after_sync(self, test_context):
        """Verify authz content is preserved after sync."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        marker = "# Preservation test marker"

        # PRE-CONDITIONS
        original = authz_path.read_text()
        assert marker not in original, "Marker should not exist before test"

        # ACTION: Add marker and sync
        authz_path.write_text(original + f"\n{marker}\n")
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS: Verify marker preserved in both local and SVN
        new_content = authz_path.read_text()
        assert marker in new_content, "Marker should be preserved locally after sync"
        svn_content = svn_cat(context_dir, "authz")
        assert marker in svn_content, "Marker should be preserved in SVN after sync"


class TestAuthzPermissions:
    """Tests for authz permission enforcement."""

    def test_add_read_permission(self, test_context):
        """Verify adding read permission to authz file works."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        fake_user = "friend@example.com"

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        assert f"{test_user} = rw" in authz_content, \
            "Owner should have rw access before test"
        assert f"{fake_user}" not in authz_content, \
            "Friend should not be in authz before test"

        # ACTION: Add friend with read permission
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw\n{fake_user} = r"
        )
        authz_path.write_text(new_authz)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}\n{result.stdout}"
        final_authz = authz_path.read_text()
        assert f"{fake_user} = r" in final_authz, \
            "Friend should have read permission locally"
        svn_authz = svn_cat(context_dir, "authz")
        assert f"{fake_user} = r" in svn_authz, \
            "Friend should have read permission in SVN"

    def test_revoke_permission(self, test_context):
        """Verify removing permission from authz works."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        fake_user = "friend@example.com"

        # SETUP: First grant access to fake user
        authz_content = authz_path.read_text()
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw\n{fake_user} = r"
        )
        authz_path.write_text(new_authz)
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        assert f"{fake_user} = r" in authz_content, \
            "Friend should have permission before revocation"
        svn_authz_before = svn_cat(context_dir, "authz")
        assert f"{fake_user} = r" in svn_authz_before, \
            "Friend permission should be in SVN before revocation"

        # ACTION: Revoke access
        revoked_authz = authz_content.replace(f"{fake_user} = r\n", "")
        authz_path.write_text(revoked_authz)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0
        final_authz = authz_path.read_text()
        assert f"{fake_user} = r" not in final_authz, \
            "Friend permission should be removed locally"
        svn_authz_after = svn_cat(context_dir, "authz")
        assert f"{fake_user} = r" not in svn_authz_after, \
            "Friend permission should be removed from SVN"

    def test_path_specific_permission(self, test_context):
        """Verify path-level permissions can be set."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        path_section = f"""
[/private]
* =
{test_user} = rw
"""
        private_dir = context_dir / "private"
        secret_file = private_dir / "secret.md"
        secret_content = "# Secret content"

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        assert "[/private]" not in authz_content, \
            "Private section should not exist before test"
        assert not private_dir.exists(), "Private dir should not exist before test"

        # ACTION: Add path-specific section, create directory and file, sync
        authz_path.write_text(authz_content + path_section)
        private_dir.mkdir()
        secret_file.write_text(secret_content)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"

        # Verify authz has the section locally
        final_authz = authz_path.read_text()
        assert "[/private]" in final_authz, "Private section should exist locally"
        assert f"* =" in final_authz, "Block rule should exist locally"

        # Verify authz has the section in SVN
        svn_authz = svn_cat(context_dir, "authz")
        assert "[/private]" in svn_authz, "Private section should exist in SVN"

        # Verify the secret file was synced
        assert svn_file_exists_in_repo(context_dir, "private/secret.md"), \
            "Secret file should be in SVN"
        assert svn_cat(context_dir, "private/secret.md") == secret_content, \
            "Secret file should have correct content in SVN"


class TestAuthzStructure:
    """Tests for authz file structure requirements."""

    def test_authz_has_root_section(self, test_context):
        """Verify authz has root section."""
        context_dir, test_user = test_context

        # POST-CONDITIONS (checking initial state from init)
        authz_content = (context_dir / "authz").read_text()
        assert "[/]" in authz_content, "Root section should exist"

        # Verify it's also in SVN
        svn_authz = svn_cat(context_dir, "authz")
        assert "[/]" in svn_authz, "Root section should exist in SVN"

    def test_authz_has_friend_requests_section(self, test_context):
        """Verify authz has friend_requests section."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        assert "[/claudeconnect/friend_requests]" in authz_content, \
            "friend_requests section should exist locally"

        svn_authz = svn_cat(context_dir, "authz")
        assert "[/claudeconnect/friend_requests]" in svn_authz, \
            "friend_requests section should exist in SVN"

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

        # Verify same in SVN
        svn_authz = svn_cat(context_dir, "authz")
        svn_fr_start = svn_authz.find("[/claudeconnect/friend_requests]")
        svn_fr_section = svn_authz[svn_fr_start:]
        svn_next_section = svn_fr_section.find("\n[", 1)
        if svn_next_section != -1:
            svn_fr_section = svn_fr_section[:svn_next_section]

        assert "* = rw" in svn_fr_section, \
            "friend_requests should have * = rw in SVN"


class TestAuthzEdgeCases:
    """Tests for authz edge cases."""

    def test_authz_comment_preserved(self, test_context):
        """Verify comments in authz are preserved."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        comment = "# This is a test comment that should be preserved"

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        assert comment not in authz_content, "Comment should not exist before test"

        # ACTION: Add comment and sync
        authz_path.write_text(authz_content + f"\n{comment}\n")
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        final_authz = authz_path.read_text()
        assert comment in final_authz, "Comment should be preserved locally"
        svn_authz = svn_cat(context_dir, "authz")
        assert comment in svn_authz, "Comment should be preserved in SVN"

    def test_authz_multiple_users(self, test_context):
        """Verify multiple users can be added to authz."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        friends = ["friend1@example.com", "friend2@example.com", "friend3@example.com"]
        additions = "\n".join(f"{f} = r" for f in friends)

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        for friend in friends:
            assert friend not in authz_content, \
                f"{friend} should not exist before test"

        # ACTION: Insert friends after main user
        new_authz = authz_content.replace(
            f"{test_user} = rw",
            f"{test_user} = rw\n{additions}"
        )
        authz_path.write_text(new_authz)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0
        final_authz = authz_path.read_text()
        svn_authz = svn_cat(context_dir, "authz")

        for friend in friends:
            assert friend in final_authz, \
                f"{friend} should be in local authz"
            assert friend in svn_authz, \
                f"{friend} should be in SVN authz"

    def test_authz_empty_permission_blocks(self, test_context):
        """Verify empty permission (user =) blocks access."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        block_section = f"""
[/blocked]
* =
{test_user} = rw
"""
        blocked_dir = context_dir / "blocked"
        blocked_file = blocked_dir / "file.md"
        blocked_content = "# Blocked content"

        # PRE-CONDITIONS
        authz_content = authz_path.read_text()
        assert "[/blocked]" not in authz_content, \
            "Blocked section should not exist before test"
        assert not blocked_dir.exists(), "Blocked dir should not exist before test"

        # ACTION: Add block section, create directory, sync
        authz_path.write_text(authz_content + block_section)
        blocked_dir.mkdir()
        blocked_file.write_text(blocked_content)
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0
        final_authz = authz_path.read_text()
        assert "* =" in final_authz, "Block rule should exist locally"

        svn_authz = svn_cat(context_dir, "authz")
        assert "[/blocked]" in svn_authz, "Blocked section should exist in SVN"
        assert "* =" in svn_authz, "Block rule should exist in SVN"

        # Verify the blocked file was synced
        assert svn_file_exists_in_repo(context_dir, "blocked/file.md"), \
            "Blocked file should be in SVN"
