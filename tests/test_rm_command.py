"""Tests for the rm command."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest

from claudeconnect.cli import _resolve_rm_paths, _execute_rm_deletions


class TestResolveRmPaths:
    """Tests for _resolve_rm_paths helper function."""

    def test_resolve_single_file(self):
        """Resolve a single existing file."""
        server_files = {"notes/file.md", "docs/readme.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("notes/file.md",), server_files, recursive=False
        )
        assert files == ["notes/file.md"]
        assert dirs == []
        assert warnings == []

    def test_resolve_multiple_files(self):
        """Resolve multiple existing files."""
        server_files = {"a.md", "b.md", "c.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("a.md", "b.md"), server_files, recursive=False
        )
        assert set(files) == {"a.md", "b.md"}
        assert dirs == []
        assert warnings == []

    def test_resolve_glob_pattern(self):
        """Resolve glob pattern matching multiple files."""
        server_files = {"notes/draft1.md", "notes/draft2.md", "docs/final.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("notes/*.md",), server_files, recursive=False
        )
        assert set(files) == {"notes/draft1.md", "notes/draft2.md"}
        assert dirs == []
        assert warnings == []

    def test_resolve_glob_no_matches(self):
        """Glob pattern with no matches produces warning."""
        server_files = {"notes/file.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("*.txt",), server_files, recursive=False
        )
        assert files == []
        assert dirs == []
        assert len(warnings) == 1
        assert "No files match pattern" in warnings[0]

    def test_resolve_file_not_found(self):
        """Non-existent file produces warning."""
        server_files = {"existing.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("missing.md",), server_files, recursive=False
        )
        assert files == []
        assert dirs == []
        assert len(warnings) == 1
        assert "not found on server" in warnings[0]

    def test_resolve_directory_without_recursive(self):
        """Directory without --recursive raises error."""
        server_files = {"mydir/file1.md", "mydir/file2.md"}
        with pytest.raises(click.ClickException) as exc_info:
            _resolve_rm_paths(("mydir",), server_files, recursive=False)
        assert "is a directory" in str(exc_info.value)
        assert "--recursive" in str(exc_info.value)

    def test_resolve_directory_with_recursive(self):
        """Directory with --recursive is accepted."""
        server_files = {"mydir/file1.md", "mydir/file2.md", "other.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("mydir",), server_files, recursive=True
        )
        assert files == []
        assert dirs == ["mydir"]
        assert warnings == []

    def test_resolve_directory_trailing_slash(self):
        """Directory with trailing slash is normalized."""
        server_files = {"mydir/file1.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("mydir/",), server_files, recursive=True
        )
        assert dirs == ["mydir"]

    def test_resolve_blocks_authz(self):
        """Cannot delete authz file."""
        server_files = {"authz", "other.md"}
        with pytest.raises(click.ClickException) as exc_info:
            _resolve_rm_paths(("authz",), server_files, recursive=False)
        assert "Cannot delete authz" in str(exc_info.value)

    def test_resolve_blocks_authz_directory(self):
        """Cannot delete authz even as directory prefix."""
        server_files = {"authz/subfile"}
        with pytest.raises(click.ClickException) as exc_info:
            _resolve_rm_paths(("authz/subfile",), server_files, recursive=False)
        assert "Cannot delete authz" in str(exc_info.value)

    def test_resolve_leading_slash_stripped(self):
        """Leading slash is stripped from paths."""
        server_files = {"notes/file.md"}
        files, dirs, warnings = _resolve_rm_paths(
            ("/notes/file.md",), server_files, recursive=False
        )
        assert files == ["notes/file.md"]

    def test_resolve_complex_glob(self):
        """Complex glob patterns work."""
        server_files = {
            "claudeconnect/with-alice/session1.md",
            "claudeconnect/with-alice/session2.md",
            "claudeconnect/with-bob/session1.md",
            "other/file.md",
        }
        files, dirs, warnings = _resolve_rm_paths(
            ("claudeconnect/with-*/session*.md",), server_files, recursive=False
        )
        assert len(files) == 3
        assert "other/file.md" not in files


class TestExecuteRmDeletions:
    """Tests for _execute_rm_deletions helper function."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary shadow and context directories."""
        with tempfile.TemporaryDirectory() as shadow_tmp:
            with tempfile.TemporaryDirectory() as context_tmp:
                shadow_dir = Path(shadow_tmp)
                context_dir = Path(context_tmp)
                yield shadow_dir, context_dir

    def test_delete_file_all_locations(self, temp_dirs):
        """Deleting a file removes from server, shadow, and context."""
        shadow_dir, context_dir = temp_dirs

        # Create test files
        (shadow_dir / "test.md").write_text("shadow")
        (context_dir / "test.md").write_text("context")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["test.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 1
        assert results["shadow_deleted"] == 1
        assert results["context_deleted"] == 1
        assert results["errors"] == []
        assert not (shadow_dir / "test.md").exists()
        assert not (context_dir / "test.md").exists()

    def test_delete_file_server_only(self, temp_dirs):
        """--server-only keeps context files."""
        shadow_dir, context_dir = temp_dirs

        (shadow_dir / "test.md").write_text("shadow")
        (context_dir / "test.md").write_text("context")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["test.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=True,
            )

        assert results["server_deleted"] == 1
        assert results["shadow_deleted"] == 1
        assert results["context_deleted"] == 0
        assert (context_dir / "test.md").exists()  # Kept

    def test_delete_file_server_error(self, temp_dirs):
        """Server error is recorded in errors."""
        shadow_dir, context_dir = temp_dirs

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal error"

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["test.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 0
        assert len(results["errors"]) == 1
        assert "server error" in results["errors"][0]

    def test_delete_file_404_idempotent(self, temp_dirs):
        """404 response is treated as success (idempotent)."""
        shadow_dir, context_dir = temp_dirs

        (shadow_dir / "test.md").write_text("shadow")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["test.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        # No error for 404
        assert results["errors"] == []
        # Shadow still cleaned up
        assert results["shadow_deleted"] == 1

    def test_delete_directory_recursive(self, temp_dirs):
        """Recursive directory deletion works."""
        shadow_dir, context_dir = temp_dirs

        # Create directory structure
        (shadow_dir / "mydir").mkdir()
        (shadow_dir / "mydir" / "file1.md").write_text("1")
        (shadow_dir / "mydir" / "file2.md").write_text("2")
        (context_dir / "mydir").mkdir()
        (context_dir / "mydir" / "file1.md").write_text("1")
        (context_dir / "mydir" / "file2.md").write_text("2")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"deleted": 2}

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=[],
                dirs=["mydir"],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 2
        assert results["shadow_deleted"] == 2
        assert results["context_deleted"] == 2
        assert not (shadow_dir / "mydir").exists()
        assert not (context_dir / "mydir").exists()

    def test_delete_multiple_files(self, temp_dirs):
        """Multiple files are all deleted."""
        shadow_dir, context_dir = temp_dirs

        (shadow_dir / "a.md").write_text("a")
        (shadow_dir / "b.md").write_text("b")
        (context_dir / "a.md").write_text("a")
        (context_dir / "b.md").write_text("b")

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["a.md", "b.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 2
        assert results["shadow_deleted"] == 2
        assert results["context_deleted"] == 2

    def test_delete_missing_local_files(self, temp_dirs):
        """Deletion works even if local files don't exist."""
        shadow_dir, context_dir = temp_dirs
        # No local files created

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("claudeconnect.cli.httpx.delete", return_value=mock_response):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["nonexistent.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 1
        assert results["shadow_deleted"] == 0
        assert results["context_deleted"] == 0
        assert results["errors"] == []

    def test_network_error_recorded(self, temp_dirs):
        """Network errors are recorded."""
        shadow_dir, context_dir = temp_dirs

        with patch("claudeconnect.cli.httpx.delete", side_effect=Exception("Connection failed")):
            results = _execute_rm_deletions(
                email="test@example.com",
                id_token="token",
                files=["test.md"],
                dirs=[],
                shadow_dir=shadow_dir,
                context_dir=context_dir,
                server_only=False,
            )

        assert results["server_deleted"] == 0
        assert len(results["errors"]) == 1
        assert "Connection failed" in results["errors"][0]


class TestRmCommandCLI:
    """Tests for the rm CLI command interface."""

    def test_rm_requires_paths(self):
        """rm command requires at least one path argument."""
        from click.testing import CliRunner
        from claudeconnect.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["rm"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "required" in result.output.lower()

    def test_rm_help(self):
        """rm --help shows usage."""
        from click.testing import CliRunner
        from claudeconnect.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["rm", "--help"])
        assert result.exit_code == 0
        assert "Delete files from the server" in result.output
        assert "--recursive" in result.output
        assert "--dry-run" in result.output
        assert "--server-only" in result.output

    def test_rm_not_logged_in(self):
        """rm fails if not logged in."""
        from click.testing import CliRunner
        from claudeconnect.cli import cli

        runner = CliRunner()
        with patch("claudeconnect.cli.get_valid_token", return_value=None):
            result = runner.invoke(cli, ["rm", "test.md"])
        assert result.exit_code == 1
        assert "Not logged in" in result.output

    def test_rm_no_context_dir(self):
        """rm fails if no context directory configured."""
        from click.testing import CliRunner
        from claudeconnect.cli import cli

        mock_tokens = MagicMock()
        mock_tokens.email = "test@example.com"
        mock_config = MagicMock()
        mock_config.context_dir = None

        runner = CliRunner()
        with patch("claudeconnect.cli.get_valid_token", return_value=mock_tokens):
            with patch("claudeconnect.cli.get_config", return_value=mock_config):
                result = runner.invoke(cli, ["rm", "test.md"])
        assert result.exit_code == 1
        assert "No context directory" in result.output
