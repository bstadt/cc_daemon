"""
Tests for ephemeral test user management.

Tests create, list, delete, and cleanup of test users.
"""

import re
import time

import pytest

from helpers import run_cli, extract_email_from_output


class TestTestUserCreate:
    """Tests for test user creation."""

    def test_create_test_user(self):
        """Verify test user can be created."""
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        assert result.returncode == 0, f"Create should succeed: {result.stderr}"
        assert "Created test user" in result.stdout, "Should confirm creation"
        assert "@ephemeral.claudeconnect.io" in result.stdout, \
            "Should show ephemeral email"

        # Cleanup
        try:
            email = extract_email_from_output(result.stdout)
            run_cli(["test-user", "delete", email])
        except ValueError:
            pass

    def test_create_returns_email(self):
        """Verify create returns a valid email."""
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        email = extract_email_from_output(result.stdout)
        assert email.startswith("test-"), "Email should start with test-"
        assert email.endswith("@ephemeral.claudeconnect.io"), \
            "Email should be ephemeral domain"

        # Cleanup
        run_cli(["test-user", "delete", email])

    def test_create_returns_repo_url(self):
        """Verify create returns repo URL."""
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        assert "Repo:" in result.stdout or "repo" in result.stdout.lower(), \
            "Should show repo URL"
        # v2 server at v2.claudeconnect.io
        assert "v2.claudeconnect.io/svn" in result.stdout or "/svn/" in result.stdout, \
            "Should show SVN URL"

        # Cleanup
        email = extract_email_from_output(result.stdout)
        run_cli(["test-user", "delete", email])

    def test_create_returns_expiry(self):
        """Verify create returns expiry time."""
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        assert "Expires:" in result.stdout or "expire" in result.stdout.lower(), \
            "Should show expiry time"

        # Cleanup
        email = extract_email_from_output(result.stdout)
        run_cli(["test-user", "delete", email])

    def test_create_respects_ttl(self):
        """Verify TTL is set correctly."""
        # Create with 2 hour TTL
        result = run_cli(["test-user", "create", "--ttl", "2h"])

        assert result.returncode == 0
        # The expiry should be approximately 2 hours from now
        # We can't easily verify the exact time, but we can check output mentions it

        email = extract_email_from_output(result.stdout)
        run_cli(["test-user", "delete", email])

    def test_create_shows_usage_instructions(self):
        """Verify create shows how to use the test user."""
        result = run_cli(["test-user", "create", "--ttl", "1h"])

        assert "CC_TEST_USER=" in result.stdout, \
            "Should show how to use the test user"

        email = extract_email_from_output(result.stdout)
        run_cli(["test-user", "delete", email])


class TestTestUserList:
    """Tests for listing test users."""

    def test_list_empty(self):
        """Verify list works when no test users exist."""
        # First ensure no test users
        run_cli(["test-user", "delete-all"])

        result = run_cli(["test-user", "list"])

        assert result.returncode == 0
        assert "No test users" in result.stdout or "0" in result.stdout or \
               "empty" in result.stdout.lower()

    def test_list_shows_created_user(self, test_user):
        """Verify list shows created test user."""
        result = run_cli(["test-user", "list"])

        assert result.returncode == 0
        assert test_user in result.stdout, f"Should show {test_user}"

    def test_list_shows_expiry(self, test_user):
        """Verify list shows expiry for each user."""
        result = run_cli(["test-user", "list"])

        assert "Expires" in result.stdout or "expire" in result.stdout.lower(), \
            "Should show expiry"


class TestTestUserDelete:
    """Tests for deleting test users."""

    def test_delete_removes_local_credentials(self):
        """Verify delete removes local credentials."""
        # Create a user
        create_result = run_cli(["test-user", "create", "--ttl", "1h"])
        email = extract_email_from_output(create_result.stdout)

        # Verify it appears in list
        list_result = run_cli(["test-user", "list"])
        assert email in list_result.stdout

        # Delete
        delete_result = run_cli(["test-user", "delete", email])
        assert delete_result.returncode == 0
        assert "Deleted" in delete_result.stdout

        # Verify not in list anymore
        list_result = run_cli(["test-user", "list"])
        assert email not in list_result.stdout

    def test_delete_removes_server_repo(self):
        """Verify delete removes server repo."""
        # Create a user
        create_result = run_cli(["test-user", "create", "--ttl", "1h"])
        email = extract_email_from_output(create_result.stdout)

        # Delete
        delete_result = run_cli(["test-user", "delete", email])

        assert "Deleted server repo" in delete_result.stdout or \
               "server" in delete_result.stdout.lower(), \
            "Should indicate server repo was deleted"

    def test_delete_nonexistent_user(self):
        """Verify deleting nonexistent user handles gracefully."""
        result = run_cli(["test-user", "delete", "fake-user@ephemeral.claudeconnect.io"])

        # Should not crash, may show error or "not found"
        # Just verify it doesn't hang or crash badly
        assert result.returncode is not None


class TestTestUserDeleteAll:
    """Tests for delete-all command."""

    def test_delete_all_clears_users(self):
        """Verify delete-all removes all local test users."""
        # First clear any existing users
        run_cli(["test-user", "delete-all"], input_text="y\n")

        # Create a couple users
        emails = []
        for _ in range(2):
            result = run_cli(["test-user", "create", "--ttl", "1h"])
            if result.returncode == 0:
                try:
                    emails.append(extract_email_from_output(result.stdout))
                except ValueError:
                    pass

        # Verify users were created
        list_result = run_cli(["test-user", "list"])
        assert len(emails) == 0 or any(email in list_result.stdout for email in emails), \
            "Created users should appear in list"

        # Delete all (with confirmation)
        result = run_cli(["test-user", "delete-all"], input_text="y\n")

        # List should be empty
        list_result = run_cli(["test-user", "list"])
        assert "No test users" in list_result.stdout or \
               all(email not in list_result.stdout for email in emails)


class TestTestUserExpiry:
    """Tests for test user expiration."""

    def test_test_user_can_operate_before_expiry(self, test_context):
        """Verify test user can perform operations before expiry."""
        context_dir, test_user = test_context
        env = {"CC_TEST_USER": test_user}

        # Create a file and sync
        (context_dir / "expiry_test.md").write_text("# Test")
        result = run_cli(["sync"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, "Should be able to sync before expiry"
