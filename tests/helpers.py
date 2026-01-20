"""
Helper functions for ClaudeConnect tests.
"""

import os
import re
import subprocess
import sys
from pathlib import Path


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
    return f"https://v2.claudeconnect.io/svn/{repo_name}"


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name."""
    return email.lower().replace("@", "-").replace(".", "-")
