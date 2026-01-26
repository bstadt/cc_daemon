"""Tests for authz permission evaluation logic.

Tests the actual server/authz.py implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock the server.config module before importing authz
# This avoids the pydantic Settings validation during import
mock_config = MagicMock()
mock_config.settings = MagicMock()
mock_config.settings.data_dir = Path("/tmp/test-data")
sys.modules["server"] = MagicMock()
sys.modules["server.config"] = mock_config

# Now we can import authz by adding the server directory and using absolute import
cc_daemon_root = Path(__file__).parent.parent
sys.path.insert(0, str(cc_daemon_root / "server"))

# Import the actual authz code (with .config mocked)
import re
import logging
from dataclasses import dataclass, field
from typing import Optional

# Re-execute the authz.py code with our mocked config
exec(open(cc_daemon_root / "server" / "authz.py").read().replace("from .config import settings", ""))

# Now parse_authz and Authz are available in local scope


# =============================================================================
# Tests
# =============================================================================

def test_specific_section_overrides_root_permission():
    """
    Test that a more specific section blocks access even if root grants it.

    Regression test for issue #82: If a user has read access at `/` but a
    child path like `/relationships` only grants access to the owner,
    the peer should be denied access to that child path.
    """
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
owner@example.com = rw
peer@example.com = r

[/relationships]
owner@example.com = rw
"""
    authz = parse_authz(authz_content)

    # Owner should have access everywhere
    assert authz.can_read("owner@example.com", "/relationships/romantic.md") is True
    assert authz.can_write("owner@example.com", "/relationships/romantic.md") is True

    # Peer should NOT have access to /relationships (section exists but no rule for them)
    assert authz.can_read("peer@example.com", "/relationships/romantic.md") is False
    assert authz.can_write("peer@example.com", "/relationships/romantic.md") is False

    # Peer should still have access to other paths covered by root
    assert authz.can_read("peer@example.com", "/context/todos.md") is True
    assert authz.can_write("peer@example.com", "/context/todos.md") is False  # Only has 'r'


def test_most_specific_section_wins():
    """Test that the most specific matching section determines access."""
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
owner@example.com = rw
peer@example.com = r

[/work]
owner@example.com = rw
peer@example.com = r

[/work/secret]
owner@example.com = rw
"""
    authz = parse_authz(authz_content)

    # Peer can read /work
    assert authz.can_read("peer@example.com", "/work/public.md") is True

    # Peer cannot read /work/secret (more specific section without peer)
    assert authz.can_read("peer@example.com", "/work/secret/plans.md") is False

    # Owner can read everything
    assert authz.can_read("owner@example.com", "/work/secret/plans.md") is True


def test_nested_sections_with_explicit_deny():
    """Test deeply nested sections with varying permissions."""
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
owner@example.com = rw
friend@example.com = r

[/projects]
owner@example.com = rw
friend@example.com = rw

[/projects/private]
owner@example.com = rw

[/projects/private/shared]
owner@example.com = rw
friend@example.com = r
"""
    authz = parse_authz(authz_content)

    # Friend can read root-level files
    assert authz.can_read("friend@example.com", "/readme.md") is True

    # Friend can read and write in /projects
    assert authz.can_read("friend@example.com", "/projects/code.py") is True
    assert authz.can_write("friend@example.com", "/projects/code.py") is True

    # Friend CANNOT access /projects/private (no rule for friend)
    assert authz.can_read("friend@example.com", "/projects/private/secret.md") is False

    # Friend CAN access /projects/private/shared (explicit rule re-grants access)
    assert authz.can_read("friend@example.com", "/projects/private/shared/doc.md") is True
    assert authz.can_write("friend@example.com", "/projects/private/shared/doc.md") is False


def test_user_with_no_permissions():
    """Test that unknown users have no access."""
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
owner@example.com = rw
friend@example.com = r
"""
    authz = parse_authz(authz_content)

    # Unknown user should have no access
    assert authz.can_read("stranger@example.com", "/anything.md") is False
    assert authz.can_write("stranger@example.com", "/anything.md") is False


def test_claudeconnect_conversation_permissions():
    """Test the typical ClaudeConnect friending pattern."""
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
alice@example.com = rw
bob@example.com = r

[/claudeconnect/conversations]
alice@example.com = rw
bob@example.com = rw

[/relationships]
alice@example.com = rw
"""
    authz = parse_authz(authz_content)

    # Bob can read Alice's general context
    assert authz.can_read("bob@example.com", "/context/todos.md") is True

    # Bob can write to conversations directory
    assert authz.can_write("bob@example.com", "/claudeconnect/conversations/transcript.md") is True

    # Bob CANNOT read Alice's relationships (private section)
    assert authz.can_read("bob@example.com", "/relationships/family.md") is False


def test_exact_path_match():
    """Test that exact path matches work correctly."""
    authz_content = """# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

[/]
owner@example.com = rw

[/specific-file.md]
owner@example.com = rw
peer@example.com = r
"""
    authz = parse_authz(authz_content)

    # Peer can only access the specific file
    assert authz.can_read("peer@example.com", "/specific-file.md") is True
    assert authz.can_read("peer@example.com", "/other-file.md") is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
