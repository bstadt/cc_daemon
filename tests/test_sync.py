"""
Tests for sync operations.

Tests file syncing, additions, modifications, deletions.
Each test verifies pre-conditions, performs actions, and verifies post-conditions.
"""

import subprocess
from pathlib import Path

import pytest

from helpers import (
    run_cli,
    svn_status,
    svn_cat,
    svn_file_exists_in_repo,
    get_svn_file_status,
)


class TestSyncBasic:
    """Basic sync operation tests."""

    def test_sync_adds_new_files(self, test_context):
        """Verify new files are added and committed to SVN."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        test_file = context_dir / "test_add.md"
        test_content = "# Test File\n\nThis is a test."

        # PRE-CONDITIONS
        assert not test_file.exists(), "File should not exist before test"
        assert not svn_file_exists_in_repo(context_dir, "test_add.md"), \
            "File should not exist in SVN before test"

        # ACTION: Create file and sync
        test_file.write_text(test_content)
        assert test_file.exists(), "File should exist after creation"
        assert get_svn_file_status(context_dir, "test_add.md") == "?", \
            "New file should be untracked before sync"

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert svn_file_exists_in_repo(context_dir, "test_add.md"), \
            "File should exist in SVN after sync"
        assert svn_cat(context_dir, "test_add.md") == test_content, \
            "File content in SVN should match what was written"
        assert get_svn_file_status(context_dir, "test_add.md") is None, \
            "File should have no pending status after sync (committed)"

    def test_sync_updates_modified_files(self, test_context):
        """Verify modified files are committed with new content."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        test_file = context_dir / "test_modify.md"
        original_content = "# Original content"
        modified_content = "# Modified content\n\nNew stuff here."

        # SETUP: Create and sync initial file
        test_file.write_text(original_content)
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # PRE-CONDITIONS
        assert svn_file_exists_in_repo(context_dir, "test_modify.md"), \
            "File should exist in SVN before modification"
        assert svn_cat(context_dir, "test_modify.md") == original_content, \
            "SVN should have original content before modification"

        # ACTION: Modify and sync
        test_file.write_text(modified_content)
        assert get_svn_file_status(context_dir, "test_modify.md") == "M", \
            "Modified file should show 'M' status before sync"

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert svn_cat(context_dir, "test_modify.md") == modified_content, \
            "SVN should have modified content after sync"
        assert get_svn_file_status(context_dir, "test_modify.md") is None, \
            "File should have no pending status after sync"

    @pytest.mark.xfail(reason="Issue #10: Daemon restores deleted files instead of removing them")
    def test_sync_handles_deletions(self, test_context):
        """Verify deleted files are removed from SVN.

        NOTE: This test documents expected behavior. Currently failing due to
        Issue #10 where the daemon restores deleted files.
        """
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        test_file = context_dir / "test_delete.md"
        test_content = "# To be deleted"

        # SETUP: Create and sync initial file
        test_file.write_text(test_content)
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # PRE-CONDITIONS
        assert test_file.exists(), "File should exist locally before deletion"
        assert svn_file_exists_in_repo(context_dir, "test_delete.md"), \
            "File should exist in SVN before deletion"

        # ACTION: Delete locally and sync
        test_file.unlink()
        assert not test_file.exists(), "File should not exist locally after unlink"

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert not test_file.exists(), \
            "File should NOT be restored locally after sync (Issue #10 bug check)"
        assert not svn_file_exists_in_repo(context_dir, "test_delete.md"), \
            "File should be removed from SVN after sync"

    def test_sync_multiple_files(self, test_context):
        """Verify multiple files can be synced at once."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        filenames = [f"multi_test_{i}.md" for i in range(3)]
        contents = {f: f"# File {i}" for i, f in enumerate(filenames)}

        # PRE-CONDITIONS
        for filename in filenames:
            assert not (context_dir / filename).exists(), \
                f"{filename} should not exist before test"
            assert not svn_file_exists_in_repo(context_dir, filename), \
                f"{filename} should not exist in SVN before test"

        # ACTION: Create multiple files and sync
        for filename, content in contents.items():
            (context_dir / filename).write_text(content)

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        for filename, content in contents.items():
            assert svn_file_exists_in_repo(context_dir, filename), \
                f"{filename} should exist in SVN after sync"
            assert svn_cat(context_dir, filename) == content, \
                f"{filename} should have correct content in SVN"

    def test_sync_nested_directories(self, test_context):
        """Verify files in nested directories are synced."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        nested_path = "level1/level2/nested_file.md"
        nested_content = "# Nested file content"

        # PRE-CONDITIONS
        assert not (context_dir / "level1").exists(), \
            "Nested directory should not exist before test"

        # ACTION: Create nested structure and sync
        nested_dir = context_dir / "level1" / "level2"
        nested_dir.mkdir(parents=True)
        (nested_dir / "nested_file.md").write_text(nested_content)

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert svn_file_exists_in_repo(context_dir, nested_path), \
            "Nested file should exist in SVN after sync"
        assert svn_cat(context_dir, nested_path) == nested_content, \
            "Nested file should have correct content in SVN"

    def test_sync_empty_directory_not_committed(self, test_context):
        """Verify empty directories don't cause errors (SVN doesn't track empty dirs)."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        empty_dir = context_dir / "empty_dir"

        # PRE-CONDITIONS
        assert not empty_dir.exists(), "Empty dir should not exist before test"

        # ACTION: Create empty directory and sync
        empty_dir.mkdir()
        assert empty_dir.exists(), "Empty dir should exist after creation"
        assert empty_dir.is_dir(), "Should be a directory"
        assert list(empty_dir.iterdir()) == [], "Directory should be empty"

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should handle empty dirs: {result.stderr}"
        # Empty directories are not tracked by SVN, so we just verify no error


class TestSyncNoChanges:
    """Tests for sync when there are no changes."""

    def test_sync_no_changes(self, test_context):
        """Verify sync handles no changes gracefully."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # PRE-CONDITIONS
        status = svn_status(context_dir)
        # Status should show no uncommitted content changes

        # ACTION: Sync without changes
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"


class TestSyncAuthz:
    """Tests for syncing authz file changes."""

    def test_sync_authz_changes(self, test_context):
        """Verify authz file changes are committed and preserved."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        authz_path = context_dir / "authz"
        marker = "\n# Test comment for authz sync verification\n"

        # PRE-CONDITIONS
        assert authz_path.exists(), "authz file should exist"
        original_content = authz_path.read_text()
        assert marker not in original_content, \
            "Marker should not be in authz before test"

        # ACTION: Add marker and sync
        authz_path.write_text(original_content + marker)
        assert get_svn_file_status(context_dir, "authz") == "M", \
            "authz should show as modified before sync"

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert get_svn_file_status(context_dir, "authz") is None, \
            "authz should have no pending status after sync"
        svn_authz_content = svn_cat(context_dir, "authz")
        assert marker in svn_authz_content, \
            "Marker should be in SVN authz after sync"


class TestSyncRevisions:
    """Tests for revision tracking."""

    def test_sync_increments_revision(self, test_context):
        """Verify sync increments SVN revision on commit."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # PRE-CONDITIONS: Get initial revision from svn info
        import subprocess
        info_result = subprocess.run(
            ["svn", "info", "--show-item", "revision"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
        )
        initial_revision = int(info_result.stdout.strip()) if info_result.stdout.strip() else 0

        # ACTION: Create file and sync
        (context_dir / "revision_test.md").write_text("# Test for revision")
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # POST-CONDITIONS
        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        info_result2 = subprocess.run(
            ["svn", "info", "--show-item", "revision"],
            cwd=str(context_dir),
            capture_output=True,
            text=True,
        )
        new_revision = int(info_result2.stdout.strip()) if info_result2.stdout.strip() else 0
        assert new_revision > initial_revision, \
            f"Revision should increment: was {initial_revision}, now {new_revision}"


class TestSyncErrors:
    """Tests for sync error handling."""

    def test_sync_without_init(self, tmp_path):
        """Verify sync fails gracefully in non-initialized directory."""
        # PRE-CONDITIONS
        assert not (tmp_path / ".svn").exists(), \
            "Should not be an SVN working copy"

        # ACTION: Try to sync
        result = run_cli(["sync"], cwd=str(tmp_path))

        # POST-CONDITIONS
        # Should fail or indicate not initialized/logged in
        assert result.returncode != 0 or "Not logged in" in result.stdout or \
               "not initialized" in result.stdout.lower(), \
            "Should indicate sync cannot proceed without init"
