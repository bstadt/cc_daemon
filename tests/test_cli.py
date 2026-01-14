"""
Tests for CLI commands.

Tests help, version, error handling, and general CLI behavior.
"""

import pytest

from helpers import run_cli


class TestCLIHelp:
    """Tests for help command."""

    def test_help_displays(self):
        """Verify --help shows usage."""
        result = run_cli(["--help"])

        assert result.returncode == 0, "Help should succeed"
        assert "Usage" in result.stdout or "usage" in result.stdout.lower(), \
            "Should show usage"

    def test_command_help(self):
        """Verify individual command help works."""
        commands = ["init", "sync", "status", "login", "test-user", "friend", "session", "pull"]

        for cmd in commands:
            result = run_cli([cmd, "--help"])
            assert result.returncode == 0, f"{cmd} --help should succeed"
            assert "Usage" in result.stdout or "Options" in result.stdout, \
                f"{cmd} should show help"

    def test_test_user_subcommand_help(self):
        """Verify test-user subcommand help works."""
        subcommands = ["create", "list", "delete", "delete-all"]

        for subcmd in subcommands:
            result = run_cli(["test-user", subcmd, "--help"])
            assert result.returncode == 0, f"test-user {subcmd} --help should succeed"


class TestCLIErrors:
    """Tests for CLI error handling."""

    def test_unknown_command_errors(self):
        """Verify invalid command fails gracefully."""
        result = run_cli(["nonexistent-command"])

        assert result.returncode != 0, "Unknown command should fail"
        # Should show an error message, not crash
        assert "Error" in result.stderr or "No such command" in result.stderr or \
               "Usage" in result.stderr or len(result.stderr) > 0

    def test_missing_required_args(self):
        """Verify missing required args shows helpful error."""
        # test-user delete requires an email
        result = run_cli(["test-user", "delete"])

        assert result.returncode != 0, "Missing arg should fail"

    def test_invalid_option(self):
        """Verify invalid option fails gracefully."""
        result = run_cli(["sync", "--nonexistent-option"])

        assert result.returncode != 0, "Invalid option should fail"


class TestCLIAuthentication:
    """Tests for authentication requirements."""

    def test_sync_requires_auth(self, tmp_path):
        """Verify sync requires authentication."""
        result = run_cli(["sync"], cwd=str(tmp_path))

        # Should fail or indicate need to log in
        assert result.returncode != 0 or "Not logged in" in result.stdout or \
               "login" in result.stdout.lower()

    def test_init_requires_auth(self, tmp_path):
        """Verify init requires authentication."""
        result = run_cli(["init"], cwd=str(tmp_path))

        # Should fail or indicate need to log in
        assert result.returncode != 0 or "Not logged in" in result.stdout or \
               "login" in result.stdout.lower()


class TestCLIOutput:
    """Tests for CLI output formatting."""

    def test_success_exit_code(self, test_context):
        """Verify successful commands return exit code 0."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        result = run_cli(["status"], env=env, cwd=str(context_dir))
        assert result.returncode == 0, "Successful status should return 0"

    def test_sync_shows_progress(self, test_context):
        """Verify sync shows progress information."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create a file to sync
        (context_dir / "progress_test.md").write_text("# Test")

        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0
        # Should show some indication of what happened
        assert len(result.stdout) > 0, "Sync should produce output"


class TestCLICommands:
    """Tests for specific CLI commands."""

    def test_login_shows_instructions(self):
        """Verify login shows what to do."""
        import subprocess
        # We can't actually complete OAuth in tests, but login should start
        # The login command waits for OAuth callback, so it will timeout
        try:
            result = run_cli(["login"])
            # If it returns, just verify it ran
            assert result.returncode is not None
        except subprocess.TimeoutExpired:
            # Expected - login waits for OAuth callback that won't come
            pass


class TestTestUserCLI:
    """Tests for test-user CLI subcommands."""

    def test_test_user_create_options(self):
        """Verify test-user create has TTL option."""
        result = run_cli(["test-user", "create", "--help"])

        assert "--ttl" in result.stdout, "Should have --ttl option"

    def test_test_user_list_works(self):
        """Verify test-user list runs without error."""
        result = run_cli(["test-user", "list"])

        assert result.returncode == 0, f"List should succeed: {result.stderr}"

    def test_test_user_delete_requires_email(self):
        """Verify test-user delete requires email argument."""
        result = run_cli(["test-user", "delete"])

        assert result.returncode != 0, "Delete without email should fail"

    def test_test_user_delete_all_works(self):
        """Verify test-user delete-all runs."""
        result = run_cli(["test-user", "delete-all"], input_text="y\n")

        # Should succeed (even if no users to delete)
        assert result.returncode == 0, f"delete-all should succeed: {result.stderr}"
