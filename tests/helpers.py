"""
Helper functions for ClaudeConnect tests.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def run_cli(args: list[str], env: dict = None, cwd: str = None, input_text: str = None) -> subprocess.CompletedProcess:
    """Run claudeconnect CLI command."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    # Run via Python to ensure we use the local code
    project_root = Path(__file__).parent.parent
    cmd = [
        sys.executable, "-c",
        f"import sys; sys.path.insert(0, '{project_root}/src'); from claudeconnect.cli import cli; cli()",
        *args
    ]

    return subprocess.run(
        cmd,
        env=full_env,
        cwd=cwd or str(project_root),
        capture_output=True,
        text=True,
        input=input_text,
        timeout=30,
    )


def extract_email_from_output(output: str) -> str:
    """Extract test user email from CLI output."""
    match = re.search(r'test-[a-f0-9]+@ephemeral\.claudeconnect\.io', output)
    if match:
        return match.group(0)
    raise ValueError(f"Could not extract email from output: {output}")


def get_repo_url(email: str) -> str:
    """Get SVN repo URL for an email."""
    repo_name = email.lower().replace("@", "-").replace(".", "-")
    return f"https://claudeconnect.io/svn/{repo_name}"


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name."""
    return email.lower().replace("@", "-").replace(".", "-")


def svn_status(context_dir: Path) -> str:
    """Get SVN status output for working copy."""
    result = subprocess.run(
        ["svn", "status"],
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def svn_info(context_dir: Path, path: str = None) -> dict:
    """Get SVN info for a path. Returns empty dict if path doesn't exist."""
    cmd = ["svn", "info"]
    if path:
        cmd.append(path)
    result = subprocess.run(
        cmd,
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return {}

    info = {}
    for line in result.stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            info[key] = value
    return info


def svn_cat(context_dir: Path, path: str) -> Optional[str]:
    """Get file content from SVN repository (not working copy).

    Uses svn cat to get the committed content, not local changes.
    Returns None if file doesn't exist in SVN.
    """
    result = subprocess.run(
        ["svn", "cat", path],
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def svn_list(context_dir: Path, path: str = ".") -> list[str]:
    """List files in SVN at given path. Returns empty list if path doesn't exist."""
    result = subprocess.run(
        ["svn", "list", path],
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return []
    return [line.rstrip("/") for line in result.stdout.splitlines() if line.strip()]


def svn_file_exists_in_repo(context_dir: Path, path: str) -> bool:
    """Check if a file exists in the SVN repository (committed state).

    This checks the actual repo, not just the working copy.
    """
    result = subprocess.run(
        ["svn", "cat", path],
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode == 0


def fresh_checkout(repo_url: str, email: str, target_dir: Path) -> Path:
    """Do a fresh SVN checkout to verify repo state.

    Returns path to the checked-out directory.
    """
    # Get credentials from environment or test user
    # For test users, the CLI stores credentials
    result = subprocess.run(
        ["svn", "checkout", repo_url, str(target_dir),
         "--username", email, "--password", "test",  # Test users use simple auth
         "--non-interactive", "--trust-server-cert-failures=unknown-ca"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Checkout failed: {result.stderr}")
    return target_dir


def get_working_copy_revision(context_dir: Path) -> int:
    """Get current working copy revision number."""
    info = svn_info(context_dir)
    return int(info.get("Revision", 0))


def file_in_working_copy(context_dir: Path, path: str) -> bool:
    """Check if file exists in working copy (local filesystem)."""
    return (context_dir / path).exists()


def get_svn_file_status(context_dir: Path, path: str) -> Optional[str]:
    """Get SVN status code for a specific file.

    Returns status code like 'A' (added), 'M' (modified), 'D' (deleted), '?' (untracked)
    or None if file has no special status (committed and unchanged).
    """
    result = subprocess.run(
        ["svn", "status", path],
        cwd=str(context_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.stdout.strip():
        # First character is the status code
        return result.stdout[0]
    return None
