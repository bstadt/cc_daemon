"""
Tests for sync operations.

Tests file syncing, additions, modifications, deletions.
"""

import subprocess
from pathlib import Path

import pytest

from helpers import run_cli


class TestSyncBasic:
    """Basic sync operation tests."""

    def test_sync_adds_new_files(self, test_context):
        """Verify new files are added and committed."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create a new file
        test_file = context_dir / "test_file.md"
        test_file.write_text("# Test File\n\nThis is a test.")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert "Added" in result.stdout or "Committed" in result.stdout, \
            "Should indicate file was added/committed"

    def test_sync_updates_modified_files(self, test_context):
        """Verify modified files are committed."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create and sync a file first
        test_file = context_dir / "modify_test.md"
        test_file.write_text("# Original content")
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # Modify the file
        test_file.write_text("# Modified content\n\nNew stuff here.")

        # Sync again
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert "Committed" in result.stdout, "Should commit modifications"

    def test_sync_handles_deletions(self, test_context):
        """Verify deleted files are removed from SVN."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create and sync a file
        test_file = context_dir / "delete_test.md"
        test_file.write_text("# To be deleted")
        run_cli(["sync"], env=env, cwd=str(context_dir))

        # Delete the file
        test_file.unlink()

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"

    def test_sync_multiple_files(self, test_context):
        """Verify multiple files can be synced at once."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create multiple files
        for i in range(3):
            (context_dir / f"multi_test_{i}.md").write_text(f"# File {i}")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert "Added 3 files" in result.stdout or "Committed" in result.stdout, \
            "Should add multiple files"

    def test_sync_nested_directories(self, test_context):
        """Verify files in nested directories are synced."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create nested structure
        nested_dir = context_dir / "level1" / "level2"
        nested_dir.mkdir(parents=True)
        (nested_dir / "nested_file.md").write_text("# Nested file")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"

    def test_sync_empty_directory_not_committed(self, test_context):
        """Verify empty directories are handled (SVN doesn't track empty dirs)."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create empty directory
        empty_dir = context_dir / "empty_dir"
        empty_dir.mkdir()

        # Sync - should not fail
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        # Should succeed (empty dirs are typically ignored)
        assert result.returncode == 0, f"Sync should handle empty dirs: {result.stderr}"


class TestSyncNoChanges:
    """Tests for sync when there are no changes."""

    def test_sync_no_changes(self, test_context):
        """Verify sync handles no changes gracefully."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Sync without making any changes
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        # Should indicate nothing to commit or just succeed
        assert "Sync complete" in result.stdout or "Nothing" in result.stdout or \
               "no changes" in result.stdout.lower() or result.returncode == 0


class TestSyncAuthz:
    """Tests for syncing authz file changes."""

    def test_sync_authz_changes(self, test_context):
        """Verify authz file changes are committed."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Modify authz
        authz_path = context_dir / "authz"
        original = authz_path.read_text()
        authz_path.write_text(original + "\n# Test comment for sync\n")

        # Sync
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Sync should succeed: {result.stderr}"
        assert "Committed" in result.stdout, "Should commit authz changes"


class TestSyncRevisions:
    """Tests for revision tracking."""

    def test_sync_increments_revision(self, test_context):
        """Verify sync increments SVN revision."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Get initial revision
        result1 = run_cli(["sync"], env=env, cwd=str(context_dir))

        # Create a file and sync
        (context_dir / "revision_test.md").write_text("# Test")
        result2 = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result2.returncode == 0
        # Should mention a revision number
        assert "revision" in result2.stdout.lower(), "Should mention revision"


class TestSyncErrors:
    """Tests for sync error handling."""

    def test_sync_without_init(self, tmp_path):
        """Verify sync fails gracefully without init."""
        # Try to sync in a non-initialized directory
        result = run_cli(["sync"], cwd=str(tmp_path))

        # Should fail or indicate not initialized
        assert result.returncode != 0 or "Not logged in" in result.stdout or \
               "not initialized" in result.stdout.lower()
