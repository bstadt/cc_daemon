"""CLI integration tests for import-substack command."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import subprocess


def test_import_substack_requires_init():
    """Test that import-substack fails gracefully when context not initialized."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set HOME to tmpdir to isolate config from user's actual ~/.claude-connect
        env = {**os.environ, "HOME": tmpdir}
        result = subprocess.run(
            ["claudeconnect", "import-substack", "xiqo"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env
        )

        # Should fail with appropriate error message
        assert result.returncode == 1
        assert ("No context directory configured" in result.stdout or
                "Run `claudeconnect init` first" in result.stdout or
                "Context directory not found" in result.stdout)


def test_import_substack_help():
    """Test that help text is displayed correctly."""
    result = subprocess.run(
        ["claudeconnect", "import-substack", "--help"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Import all posts from a Substack" in result.stdout
    assert "--max-posts" in result.stdout


# Note: Full integration test with actual Substack API calls would require
# network access and may be rate-limited. The unit tests in test_substack.py
# provide comprehensive coverage using mocks. For a real end-to-end test,
# you would need to:
# 1. Initialize a real context directory with `claudeconnect init`
# 2. Run `claudeconnect import-substack <real-username>`
# 3. Verify the created files
#
# This is better done as a manual integration test rather than in automated CI/CD.
