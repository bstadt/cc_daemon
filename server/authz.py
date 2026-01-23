"""Authorization parser for ClaudeConnect.

Parses authz files with the format:

    # Public Key: <X25519-hex-string>

    [/]
    friend@example.com = r

    [/claudeconnect/with-friend@example.com]
    friend@example.com = rw

The public key comment MUST be present at the top of the file.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

# Regex patterns
# Accept both "# Public Key:" and "# Public-Key:" formats
PUBLIC_KEY_PATTERN = re.compile(r"^#\s*Public[-\s]Key:\s*([a-fA-F0-9]{64})\s*$")
SECTION_PATTERN = re.compile(r"^\[([^\]]+)\]$")
PERMISSION_PATTERN = re.compile(r"^([^=]+?)\s*=\s*([rw]*)\s*$")


@dataclass
class AuthzRule:
    """A single authorization rule."""
    path: str  # e.g., "/" or "/claudeconnect/with-bob@example.com"
    user: str  # e.g., "bob@example.com"
    can_read: bool
    can_write: bool


@dataclass
class Authz:
    """Parsed authz file."""
    public_key: str
    rules: list[AuthzRule] = field(default_factory=list)

    def can_read(self, user_email: str, path: str) -> bool:
        """Check if user can read the given path."""
        # Normalize path to start with /
        if not path.startswith("/"):
            path = "/" + path

        # Find the most specific matching rule
        best_match: Optional[AuthzRule] = None
        best_match_len = -1

        for rule in self.rules:
            if rule.user != user_email:
                continue

            # Check if rule path matches (path starts with rule.path)
            if path == rule.path or path.startswith(rule.path.rstrip("/") + "/") or rule.path == "/":
                rule_len = len(rule.path)
                if rule_len > best_match_len:
                    best_match = rule
                    best_match_len = rule_len

        if best_match:
            return best_match.can_read

        return False

    def can_write(self, user_email: str, path: str) -> bool:
        """Check if user can write to the given path."""
        # Normalize path to start with /
        if not path.startswith("/"):
            path = "/" + path

        # Find the most specific matching rule
        best_match: Optional[AuthzRule] = None
        best_match_len = -1

        for rule in self.rules:
            if rule.user != user_email:
                continue

            # Check if rule path matches
            if path == rule.path or path.startswith(rule.path.rstrip("/") + "/") or rule.path == "/":
                rule_len = len(rule.path)
                if rule_len > best_match_len:
                    best_match = rule
                    best_match_len = rule_len

        if best_match:
            return best_match.can_write

        return False


class AuthzParseError(Exception):
    """Error parsing authz file."""
    pass


def parse_authz(content: str) -> Authz:
    """
    Parse an authz file content.

    Args:
        content: The raw authz file content

    Returns:
        Parsed Authz object

    Raises:
        AuthzParseError: If the file is malformed or missing required fields
    """
    lines = content.strip().split("\n")

    public_key: Optional[str] = None
    rules: list[AuthzRule] = []
    current_section: Optional[str] = None

    for line_num, line in enumerate(lines, 1):
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Check for public key comment (must be first non-empty line)
        if public_key is None:
            match = PUBLIC_KEY_PATTERN.match(line)
            if match:
                public_key = match.group(1).lower()
                continue
            elif line.startswith("#"):
                # Skip other comments before public key
                continue
            else:
                raise AuthzParseError(
                    f"Line {line_num}: Public key must be first. "
                    f"Expected '# Public Key: <64-char-hex>', got: {line[:50]}"
                )

        # Skip comments after public key
        if line.startswith("#"):
            continue

        # Check for section header
        section_match = SECTION_PATTERN.match(line)
        if section_match:
            current_section = section_match.group(1)
            # Validate section path
            if not current_section.startswith("/"):
                raise AuthzParseError(
                    f"Line {line_num}: Section path must start with /, got: [{current_section}]"
                )
            continue

        # Check for permission line
        perm_match = PERMISSION_PATTERN.match(line)
        if perm_match:
            if current_section is None:
                raise AuthzParseError(
                    f"Line {line_num}: Permission outside of section: {line}"
                )

            user = perm_match.group(1).strip()
            perms = perm_match.group(2).lower()

            # Validate email format (basic check)
            if "@" not in user:
                raise AuthzParseError(
                    f"Line {line_num}: Invalid user email: {user}"
                )

            rules.append(AuthzRule(
                path=current_section,
                user=user,
                can_read="r" in perms,
                can_write="w" in perms,
            ))
            continue

        # Unknown line
        raise AuthzParseError(f"Line {line_num}: Unrecognized line: {line}")

    if public_key is None:
        raise AuthzParseError("Missing public key. File must start with: # Public Key: <64-char-hex>")

    return Authz(public_key=public_key, rules=rules)


def validate_authz(content: str) -> tuple[bool, Optional[str]]:
    """
    Validate authz file content.

    Args:
        content: The raw authz file content

    Returns:
        (is_valid, error_message) - error_message is None if valid
    """
    try:
        parse_authz(content)
        return True, None
    except AuthzParseError as e:
        return False, str(e)


def get_user_dir(user_email: str) -> Path:
    """Get the data directory for a user."""
    # Sanitize email for filesystem
    safe_email = user_email.replace("/", "_").replace("\\", "_")
    return settings.data_dir / safe_email


def load_authz(user_email: str) -> Optional[Authz]:
    """
    Load and parse a user's authz file.

    Args:
        user_email: The user whose authz to load

    Returns:
        Parsed Authz, or None if user doesn't exist or has no authz
    """
    authz_path = get_user_dir(user_email) / "authz"

    if not authz_path.exists():
        return None

    try:
        content = authz_path.read_text()
        return parse_authz(content)
    except AuthzParseError as e:
        logger.error(f"Failed to parse authz for {user_email}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to read authz for {user_email}: {e}")
        return None


def get_public_key(user_email: str) -> Optional[str]:
    """
    Get a user's public key from their authz file.

    Args:
        user_email: The user whose public key to retrieve

    Returns:
        The public key hex string, or None if not found
    """
    authz = load_authz(user_email)
    if authz:
        return authz.public_key
    return None


def check_read_permission(owner_email: str, requester_email: str, path: str) -> bool:
    """
    Check if requester can read a path from owner's storage.

    Args:
        owner_email: The user who owns the files
        requester_email: The user requesting access
        path: The path being accessed

    Returns:
        True if access is allowed
    """
    # Owner can always read their own files
    if owner_email == requester_email:
        return True

    authz = load_authz(owner_email)
    if authz is None:
        # No authz = no access for others
        return False

    return authz.can_read(requester_email, path)


def check_write_permission(owner_email: str, requester_email: str, path: str) -> bool:
    """
    Check if requester can write to a path in owner's storage.

    Args:
        owner_email: The user who owns the files
        requester_email: The user requesting access
        path: The path being written

    Returns:
        True if write is allowed
    """
    # Owner can always write their own files
    if owner_email == requester_email:
        return True

    authz = load_authz(owner_email)
    if authz is None:
        # No authz = no access for others
        return False

    return authz.can_write(requester_email, path)
