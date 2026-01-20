"""
Tests for repository management functionality.

Tests init command, directory structure, authz setup, etc.
Per system2.md specification.
"""

import subprocess
from pathlib import Path

import pytest

from helpers import run_cli, email_to_repo_name


class TestInit:
    """Tests for the init command."""

    def test_init_creates_working_copy(self, test_context):
        """Verify init creates SVN working copy."""
        context_dir, test_user = test_context

        # Check .svn directory exists
        svn_dir = context_dir / ".svn"
        assert svn_dir.is_dir(), ".svn directory should exist after init"

    def test_init_creates_claudeconnect_dir(self, test_context):
        """Verify init creates claudeconnect directory."""
        context_dir, test_user = test_context

        cc_dir = context_dir / "claudeconnect"
        assert cc_dir.is_dir(), "claudeconnect/ directory should exist"

    def test_init_creates_system_messages_dir(self, test_context):
        """Verify init creates with-claudeconnect-io directory for system messages."""
        context_dir, test_user = test_context

        # Per system2.md, system messages (friend requests, etc.) go in with-claudeconnect-io/
        system_dir = context_dir / "claudeconnect" / "with-claudeconnect-io"
        assert system_dir.is_dir(), "claudeconnect/with-claudeconnect-io/ should exist"

    def test_init_installs_skill_globally(self, test_context):
        """Verify init installs SKILL.md to ~/.claude/skills/."""
        context_dir, test_user = test_context
        from pathlib import Path

        # Skills are installed globally, not in context dir
        skill_file = Path.home() / ".claude" / "skills" / "claudeconnect" / "SKILL.md"
        assert skill_file.is_file(), "SKILL.md should exist in ~/.claude/skills/"

        content = skill_file.read_text()
        assert "ClaudeConnect" in content or "claudeconnect" in content.lower(), \
            "SKILL.md should contain ClaudeConnect instructions"

    def test_init_creates_authz_file(self, test_context):
        """Verify init creates authz file at repo root."""
        context_dir, test_user = test_context

        authz_file = context_dir / "authz"
        assert authz_file.is_file(), "authz file should exist at root"

    def test_init_authz_has_user_permissions(self, test_context):
        """Verify authz grants user rw access on root."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        assert f"{test_user} = rw" in authz_content, \
            f"User {test_user} should have rw access on root"

    def test_init_authz_has_system_messages_section(self, test_context):
        """Verify authz has with-claudeconnect-io section (owner only, not world-writable)."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        assert "[/claudeconnect/with-claudeconnect-io]" in authz_content, \
            "with-claudeconnect-io section should exist in authz"

        # Per system2.md, this section should NOT be world-writable
        # Server uses admin bypass to write friend requests
        lines = authz_content.split('\n')
        in_system_section = False
        for line in lines:
            if line.strip() == "[/claudeconnect/with-claudeconnect-io]":
                in_system_section = True
            elif line.strip().startswith("["):
                in_system_section = False
            elif in_system_section and "* = rw" in line:
                pytest.fail("with-claudeconnect-io should NOT be world-writable")

    def test_init_authz_has_with_section(self, test_context):
        """Verify authz has with-{email} section for conversations."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()
        user_repo_name = email_to_repo_name(test_user)

        assert f"[/claudeconnect/with-{user_repo_name}]" in authz_content, \
            f"with-{user_repo_name} section should exist in authz"

    def test_init_idempotent(self, test_context):
        """Verify running init twice doesn't break anything."""
        context_dir, test_user = test_context

        # Run init again
        env = {"CC_TEST_USER": test_user}
        result = run_cli(
            ["init"],
            env=env,
            cwd=str(context_dir),
            input_text="y\n",
        )

        # Should succeed or gracefully handle already initialized
        # Check that repo is still functional
        assert (context_dir / ".svn").is_dir(), "SVN working copy should still exist"
        assert (context_dir / "claudeconnect").is_dir(), "claudeconnect dir should still exist"


class TestStatus:
    """Tests for the status command."""

    def test_status_shows_test_user_mode(self, test_context):
        """Verify status shows test user mode indicator."""
        context_dir, test_user = test_context

        env = {"CC_TEST_USER": test_user}
        result = run_cli(["status"], env=env, cwd=str(context_dir))

        assert result.returncode == 0, f"Status should succeed: {result.stderr}"
        assert "[Test User Mode]" in result.stdout, "Should show test user mode"

    def test_status_shows_email(self, test_context):
        """Verify status shows user email."""
        context_dir, test_user = test_context

        env = {"CC_TEST_USER": test_user}
        result = run_cli(["status"], env=env, cwd=str(context_dir))

        assert test_user in result.stdout, f"Should show email {test_user}"

    def test_status_shows_repo_url(self, test_context):
        """Verify status shows repo URL."""
        context_dir, test_user = test_context

        env = {"CC_TEST_USER": test_user}
        result = run_cli(["status"], env=env, cwd=str(context_dir))

        # v2 server at v2.claudeconnect.io
        assert "v2.claudeconnect.io/svn" in result.stdout or "/svn/" in result.stdout, \
            "Should show repo URL"

    def test_status_shows_expiry(self, test_context):
        """Verify status shows expiry time for test users."""
        context_dir, test_user = test_context

        env = {"CC_TEST_USER": test_user}
        result = run_cli(["status"], env=env, cwd=str(context_dir))

        assert "Expires:" in result.stdout or "expires" in result.stdout.lower(), \
            "Should show expiry time"

    def test_status_without_test_user(self, tmp_path):
        """Verify status without CC_TEST_USER shows regular user info or prompts login."""
        # Note: This test will show regular user info if logged in normally
        # We just verify it doesn't crash and returns some output
        result = run_cli(["status"], cwd=str(tmp_path))

        # Should either show logged in info or "not logged in"
        assert result.returncode == 0 or "Not logged in" in result.stdout or \
               "Logged in as" in result.stdout


class TestDirectoryStructure:
    """Tests for the complete directory structure after init per system2.md."""

    def test_complete_structure(self, test_context):
        """Verify complete directory structure is created per system2.md."""
        context_dir, test_user = test_context

        # Per system2.md, the structure should be:
        # context_dir/
        # ├── .svn/
        # ├── authz
        # └── claudeconnect/
        #     └── with-claudeconnect-io/
        expected_structure = [
            ".svn",
            "authz",
            "claudeconnect",
            "claudeconnect/with-claudeconnect-io",
        ]

        for path in expected_structure:
            full_path = context_dir / path
            assert full_path.exists(), f"Expected {path} to exist"

    def test_authz_structure(self, test_context):
        """Verify authz file has correct section structure per system2.md."""
        context_dir, test_user = test_context

        authz_content = (context_dir / "authz").read_text()

        # Should have root section
        assert "[/]" in authz_content, "Root section should exist"

        # Should have with-claudeconnect-io section (system messages)
        assert "[/claudeconnect/with-claudeconnect-io]" in authz_content, \
            "with-claudeconnect-io section should exist"

        # Should have with-{email} section (conversations)
        user_repo_name = email_to_repo_name(test_user)
        assert f"[/claudeconnect/with-{user_repo_name}]" in authz_content, \
            f"with-{user_repo_name} section should exist"

        # User should have rw on root
        root_section_end = authz_content.find("[/claudeconnect")
        if root_section_end == -1:
            root_section_end = len(authz_content)
        root_section = authz_content[:root_section_end]
        assert f"{test_user} = rw" in root_section, \
            "User should have rw on root section"


class TestVerifyInitStructure:
    """Tests for the verify_init_structure function."""

    def test_verify_passes_after_valid_init(self, test_context):
        """Verify that verify_init_structure returns no errors after valid init."""
        context_dir, test_user = test_context

        # Import the verification function
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from claudeconnect.cli import verify_init_structure

        errors = verify_init_structure(context_dir)

        # Should have no errors (except possibly skill, which is tested separately)
        dir_errors = [e for e in errors if "SKILL.md" not in e]
        assert len(dir_errors) == 0, f"Unexpected errors: {dir_errors}"

    def test_verify_detects_missing_svn(self, tmp_path):
        """Verify detection of missing .svn directory."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from claudeconnect.cli import verify_init_structure

        # Create a directory without .svn
        context_dir = tmp_path / "no_svn"
        context_dir.mkdir()

        errors = verify_init_structure(context_dir)

        assert any(".svn" in e for e in errors), "Should detect missing .svn"

    def test_verify_detects_missing_claudeconnect_dir(self, tmp_path):
        """Verify detection of missing claudeconnect directory."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from claudeconnect.cli import verify_init_structure

        # Create directory with .svn but no claudeconnect/
        context_dir = tmp_path / "no_cc"
        context_dir.mkdir()
        (context_dir / ".svn").mkdir()

        errors = verify_init_structure(context_dir)

        assert any("claudeconnect/" in e for e in errors), "Should detect missing claudeconnect dir"

    def test_verify_detects_missing_system_messages_dir(self, tmp_path):
        """Verify detection of missing with-claudeconnect-io directory."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from claudeconnect.cli import verify_init_structure

        # Create directory structure missing with-claudeconnect-io
        context_dir = tmp_path / "no_system"
        context_dir.mkdir()
        (context_dir / ".svn").mkdir()
        (context_dir / "claudeconnect").mkdir()
        (context_dir / "authz").write_text("[/]\ntest@test.com = rw\n")

        errors = verify_init_structure(context_dir)

        assert any("with-claudeconnect-io" in e for e in errors), \
            "Should detect missing with-claudeconnect-io"

    def test_verify_detects_missing_authz(self, tmp_path):
        """Verify detection of missing authz file."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from claudeconnect.cli import verify_init_structure

        # Create directory structure without authz
        context_dir = tmp_path / "no_authz"
        context_dir.mkdir()
        (context_dir / ".svn").mkdir()
        (context_dir / "claudeconnect").mkdir()
        (context_dir / "claudeconnect" / "with-claudeconnect-io").mkdir()

        errors = verify_init_structure(context_dir)

        assert any("authz" in e for e in errors), "Should detect missing authz"
