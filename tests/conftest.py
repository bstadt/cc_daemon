"""
Pytest fixtures for ClaudeConnect tests.

These fixtures use ephemeral test users for isolated testing.
"""

import sys
from pathlib import Path

import pytest

# Add tests dir to path for helpers import
sys.path.insert(0, str(Path(__file__).parent))

from helpers import run_cli, extract_email_from_output


@pytest.fixture(scope="function")
def test_user():
    """
    Create an ephemeral test user for the test.

    Yields the test user email.
    Automatically cleans up after the test.
    """
    # Create test user
    result = run_cli(["test-user", "create", "--ttl", "1h"])

    if result.returncode != 0:
        pytest.skip(f"Could not create test user: {result.stderr}")

    try:
        email = extract_email_from_output(result.stdout)
    except ValueError as e:
        pytest.fail(str(e))

    yield email

    # Cleanup - delete test user
    run_cli(["test-user", "delete", email])


@pytest.fixture(scope="function")
def test_context(test_user, tmp_path):
    """
    Create an initialized context directory for the test user.

    Yields tuple of (context_dir: Path, test_user_email: str).
    """
    context_dir = tmp_path / "context"
    context_dir.mkdir()

    env = {"CC_TEST_USER": test_user}

    # Initialize the context directory
    result = run_cli(
        ["init"],
        env=env,
        cwd=str(context_dir),
        input_text="y\n",  # Confirm directory switch
    )

    if result.returncode != 0:
        pytest.fail(f"Could not initialize context: {result.stderr}\n{result.stdout}")

    yield context_dir, test_user


@pytest.fixture(scope="function")
def two_test_users():
    """
    Create two ephemeral test users for multi-user tests.

    Yields list of two test user emails.
    Automatically cleans up both after the test.
    """
    users = []

    for _ in range(2):
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
def two_test_contexts(two_test_users, tmp_path):
    """
    Create two initialized context directories for two test users.

    Yields list of dicts with 'email', 'dir', and 'env' keys.
    """
    contexts = []

    for i, email in enumerate(two_test_users):
        context_dir = tmp_path / f"context_{i}"
        context_dir.mkdir()

        env = {"CC_TEST_USER": email}

        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",
        )

        if result.returncode != 0:
            pytest.fail(f"Could not initialize context for {email}: {result.stderr}")

        contexts.append({
            "email": email,
            "dir": context_dir,
            "env": env,
        })

    yield contexts
