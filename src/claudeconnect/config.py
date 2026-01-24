"""Configuration management for Claude Connect."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict


# Server configuration - single source of truth
# Override with CLAUDECONNECT_SERVER environment variable
DEFAULT_SERVER_URL = "https://claudeconnect.io"
SERVER_URL = os.environ.get("CLAUDECONNECT_SERVER", DEFAULT_SERVER_URL)
API_BASE_URL = f"{SERVER_URL}/api"

CONFIG_DIR = Path.home() / ".claude-connect"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKENS_FILE = CONFIG_DIR / "tokens.json"
TEST_USERS_DIR = CONFIG_DIR / "test-users"
SHADOW_DIR = CONFIG_DIR / "shadow"  # Local encrypted file cache for sync


def sanitize_email(email: str) -> str:
    """
    Sanitize email for use in directory/file names.

    Args:
        email: User email (e.g., brandon@gmail.com)

    Returns:
        Sanitized name (e.g., brandon-gmail-com)
    """
    return email.replace("@", "-").replace(".", "-").lower()


# Alias for backwards compatibility
email_to_repo_name = sanitize_email


def repo_name_to_email(repo_name: str) -> str:
    """
    Convert repository name back to email (best effort).

    This is a lossy conversion since both @ and . become -.
    Uses heuristic: last two parts are domain, rest is username.

    Args:
        repo_name: Sanitized repo name (e.g., "brandon-gmail-com")

    Returns:
        Email address (e.g., "brandon@gmail.com")

    Examples:
        "brandon-gmail-com" → "brandon@gmail.com"
        "john-doe-example-com" → "john.doe@example.com"
    """
    # Convert all - to .
    with_dots = repo_name.replace("-", ".")

    # If there are 2+ dots, split into user@domain
    if with_dots.count(".") >= 2:
        parts = with_dots.rsplit(".", 2)
        if len(parts) == 3:
            return f"{parts[0]}@{parts[1]}.{parts[2]}"

    # Fallback: return as-is (shouldn't happen with valid repo names)
    return with_dots


def get_shadow_dir(email: str) -> Path:
    """
    Get the shadow directory path for a user.

    The shadow directory stores encrypted files locally for sync comparison.

    Args:
        email: User email

    Returns:
        Path to shadow directory (~/.claude-connect/shadow/<sanitized-email>/)
    """
    return SHADOW_DIR / sanitize_email(email)


@dataclass
class Tokens:
    """OAuth tokens."""
    id_token: str
    refresh_token: str
    email: str

    def save(self):
        """Save tokens to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKENS_FILE.write_text(json.dumps(asdict(self), indent=2))
        # Restrict permissions
        TOKENS_FILE.chmod(0o600)

    @classmethod
    def load(cls) -> Optional["Tokens"]:
        """Load tokens from disk."""
        if not TOKENS_FILE.exists():
            return None
        try:
            data = json.loads(TOKENS_FILE.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None


@dataclass
class Config:
    """Claude Connect configuration."""
    context_dir: Optional[str] = None
    encryption_enabled: bool = False  # Whether to encrypt context files

    def save(self):
        """Save config to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "Config":
        """Load config from disk, or return defaults."""
        if not CONFIG_FILE.exists():
            return cls()
        try:
            data = json.loads(CONFIG_FILE.read_text())
            # Filter to only known fields (backwards compatibility)
            known_fields = {"context_dir", "encryption_enabled"}
            filtered = {k: v for k, v in data.items() if k in known_fields}
            return cls(**filtered)
        except (json.JSONDecodeError, TypeError):
            return cls()


def get_tokens() -> Optional[Tokens]:
    """Get stored OAuth tokens."""
    return Tokens.load()


def get_config() -> Config:
    """Get configuration."""
    return Config.load()


def is_logged_in() -> bool:
    """Check if user is logged in."""
    tokens = get_tokens()
    return tokens is not None and tokens.id_token


def get_email() -> Optional[str]:
    """Get logged-in user's email."""
    tokens = get_tokens()
    return tokens.email if tokens else None


# =============================================================================
# Test User Credentials
# =============================================================================

@dataclass
class TestUserCredentials:
    """Credentials for an ephemeral test user."""
    email: str
    api_token: str
    repo_url: str
    expires_at: int
    context_dir: Optional[str] = None

    def save(self) -> None:
        """Save test user credentials to disk."""
        user_dir = TEST_USERS_DIR / self.email
        user_dir.mkdir(parents=True, exist_ok=True)
        creds_file = user_dir / "credentials.json"
        creds_file.write_text(json.dumps(asdict(self), indent=2))
        creds_file.chmod(0o600)

    @classmethod
    def load(cls, email: str) -> Optional["TestUserCredentials"]:
        """Load test user credentials from disk."""
        creds_file = TEST_USERS_DIR / email / "credentials.json"
        if not creds_file.exists():
            return None
        try:
            data = json.loads(creds_file.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def delete(self) -> None:
        """Delete test user credentials from disk."""
        import shutil
        user_dir = TEST_USERS_DIR / self.email
        if user_dir.exists():
            shutil.rmtree(user_dir)


def get_test_user_email() -> Optional[str]:
    """Get test user email from environment variable."""
    return os.environ.get("CC_TEST_USER")


def list_test_users() -> list[str]:
    """List all local test user emails."""
    if not TEST_USERS_DIR.exists():
        return []
    return [
        d.name for d in TEST_USERS_DIR.iterdir()
        if d.is_dir() and (d / "credentials.json").exists()
    ]


def get_test_user_credentials(email: str) -> Optional[TestUserCredentials]:
    """Get credentials for a specific test user."""
    return TestUserCredentials.load(email)
