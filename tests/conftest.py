"""
Pytest fixtures for ClaudeConnect tests.

These fixtures use ephemeral test users for isolated testing.
"""

import sys
from pathlib import Path

import pytest

# Add tests dir to path for helpers import
sys.path.insert(0, str(Path(__file__).parent))

from helpers import run_cli, extract_email_from_output, email_to_repo_name


def verify_init_structure(context_dir: Path, email: str) -> list[str]:
    """
    Verify that claudeconnect init created the correct structure per system2.md.

    Expected structure:
        context_dir/
        ├── .svn/                           # SVN working copy
        ├── authz                           # Access control file
        └── claudeconnect/
            └── with-claudeconnect-io/      # System messages folder

    Expected authz sections:
        [/]
        owner@email.com = rw

        [/claudeconnect/with-claudeconnect-io]
        owner@email.com = rw

        [/claudeconnect/with-{owner-email-sanitized}]
        owner@email.com = rw

    Args:
        context_dir: The initialized context directory
        email: The user's email address

    Returns:
        List of error messages. Empty if structure is correct.
    """
    errors = []
    email_sanitized = email_to_repo_name(email)

    # === Directory Structure ===

    # .svn/ must exist (SVN working copy)
    svn_dir = context_dir / ".svn"
    if not svn_dir.is_dir():
        errors.append(".svn/ directory missing - not a valid SVN working copy")

    # claudeconnect/ directory must exist
    cc_dir = context_dir / "claudeconnect"
    if not cc_dir.is_dir():
        errors.append("claudeconnect/ directory missing")
    else:
        # with-claudeconnect-io/ must exist (system messages folder)
        system_dir = cc_dir / "with-claudeconnect-io"
        if not system_dir.is_dir():
            errors.append("claudeconnect/with-claudeconnect-io/ directory missing")

    # authz file must exist
    authz_file = context_dir / "authz"
    if not authz_file.is_file():
        errors.append("authz file missing")
    else:
        authz_content = authz_file.read_text()

        # === Authz Structure ===

        # [/] section with owner rw access
        if "[/]" not in authz_content:
            errors.append("authz missing [/] section")
        elif f"{email} = rw" not in authz_content:
            errors.append(f"authz [/] section missing '{email} = rw'")

        # [/claudeconnect/with-claudeconnect-io] section (owner only, NOT world-writable)
        if "[/claudeconnect/with-claudeconnect-io]" not in authz_content:
            errors.append("authz missing [/claudeconnect/with-claudeconnect-io] section")

        # [/claudeconnect/with-{owner-email}] section
        owner_section = f"[/claudeconnect/with-{email_sanitized}]"
        if owner_section not in authz_content:
            errors.append(f"authz missing {owner_section} section")

        # Verify with-claudeconnect-io is NOT world-writable
        # Check if there's a "* = rw" under the with-claudeconnect-io section
        lines = authz_content.split('\n')
        in_system_section = False
        for line in lines:
            if line.strip() == "[/claudeconnect/with-claudeconnect-io]":
                in_system_section = True
            elif line.strip().startswith("["):
                in_system_section = False
            elif in_system_section and "* = rw" in line:
                errors.append("with-claudeconnect-io section should NOT be world-writable (* = rw)")
                break

    # === Skill Installation ===
    skill_file = Path.home() / ".claude" / "skills" / "claudeconnect" / "SKILL.md"
    if not skill_file.is_file():
        errors.append("SKILL.md not installed at ~/.claude/skills/claudeconnect/")

    return errors


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

    Verifies the directory structure matches system2.md specification:
    - .svn/ directory exists
    - claudeconnect/with-claudeconnect-io/ exists
    - authz file has correct sections
    - SKILL.md is installed

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

    # Verify the structure matches system2.md
    errors = verify_init_structure(context_dir, test_user)
    if errors:
        pytest.fail(
            f"Init created incorrect structure:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )

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

    Verifies each context directory structure matches system2.md.

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

        # Verify the structure matches system2.md
        errors = verify_init_structure(context_dir, email)
        if errors:
            pytest.fail(
                f"Init created incorrect structure for {email}:\n" +
                "\n".join(f"  - {e}" for e in errors)
            )

        contexts.append({
            "email": email,
            "dir": context_dir,
            "env": env,
        })

    yield contexts
