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
ACCOUNTS_DIR = CONFIG_DIR / "accounts"
ACTIVE_ACCOUNT_FILE = CONFIG_DIR / "active_account"

# Legacy paths (for migration)
LEGACY_CONFIG_FILE = CONFIG_DIR / "config.json"
LEGACY_TOKENS_FILE = CONFIG_DIR / "tokens.json"
LEGACY_SHADOW_DIR = CONFIG_DIR / "shadow"
LEGACY_PEERS_DIR = CONFIG_DIR / "peers"

TEST_USERS_DIR = CONFIG_DIR / "test-users"


def get_account_dir(email: str) -> Path:
    """Get the account directory for a user.

    New structure: ~/.claude-connect/accounts/{email}/
    """
    return ACCOUNTS_DIR / email


def get_config_file(email: str) -> Path:
    """Get config file path for a user."""
    return get_account_dir(email) / "config.json"


def get_tokens_file(email: str) -> Path:
    """Get tokens file path for a user."""
    return get_account_dir(email) / "tokens.json"


def get_shadow_dir(email: str) -> Path:
    """Get shadow directory for a user's sync cache."""
    return get_account_dir(email) / "shadow"


def get_peers_dir(email: str) -> Path:
    """Get peers directory for a user's cached peer contexts."""
    return get_account_dir(email) / "peers"


def get_keys_dir(email: str) -> Path:
    """Get keys directory for a user's encryption keys."""
    return get_account_dir(email) / "keys"


def get_friends_dir(email: str) -> Path:
    """Get friends directory for a user's stored friend keys."""
    return get_account_dir(email) / "friends"


def get_active_account() -> Optional[str]:
    """Get the currently active account email."""
    if not ACTIVE_ACCOUNT_FILE.exists():
        return None
    try:
        return ACTIVE_ACCOUNT_FILE.read_text().strip()
    except Exception:
        return None


def set_active_account(email: str) -> None:
    """Set the currently active account."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_ACCOUNT_FILE.write_text(email)


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




@dataclass
class Tokens:
    """OAuth tokens."""
    id_token: str
    refresh_token: str
    email: str

    def save(self):
        """Save tokens to disk in email-specific directory."""
        account_dir = get_account_dir(self.email)
        account_dir.mkdir(parents=True, exist_ok=True)
        tokens_file = get_tokens_file(self.email)
        tokens_file.write_text(json.dumps(asdict(self), indent=2))
        # Restrict permissions
        tokens_file.chmod(0o600)
        # Set as active account
        set_active_account(self.email)

    @classmethod
    def load(cls, email: Optional[str] = None) -> Optional["Tokens"]:
        """Load tokens from disk.

        Args:
            email: If provided, load tokens for this specific account.
                   If None, load tokens for the active account.
        """
        if email is None:
            email = get_active_account()
        if email is None:
            # Try legacy location for migration
            if LEGACY_TOKENS_FILE.exists():
                try:
                    data = json.loads(LEGACY_TOKENS_FILE.read_text())
                    return cls(**data)
                except (json.JSONDecodeError, TypeError, KeyError):
                    return None
            return None

        tokens_file = get_tokens_file(email)
        if not tokens_file.exists():
            return None
        try:
            data = json.loads(tokens_file.read_text())
            return cls(**data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None


@dataclass
class Config:
    """Claude Connect configuration."""
    context_dir: Optional[str] = None
    encryption_enabled: bool = False  # Whether to encrypt context files
    email: Optional[str] = None  # Associated email (set after load)

    def save(self, email: str):
        """Save config to disk for a specific account."""
        account_dir = get_account_dir(email)
        account_dir.mkdir(parents=True, exist_ok=True)
        config_file = get_config_file(email)
        # Don't save the email field in the JSON (it's derived from path)
        data = {"context_dir": self.context_dir, "encryption_enabled": self.encryption_enabled}
        config_file.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, email: Optional[str] = None) -> "Config":
        """Load config from disk, or return defaults.

        Args:
            email: If provided, load config for this specific account.
                   If None, load config for the active account.
        """
        if email is None:
            email = get_active_account()

        if email is None:
            # Try legacy location for migration
            if LEGACY_CONFIG_FILE.exists():
                try:
                    data = json.loads(LEGACY_CONFIG_FILE.read_text())
                    known_fields = {"context_dir", "encryption_enabled"}
                    filtered = {k: v for k, v in data.items() if k in known_fields}
                    return cls(**filtered)
                except (json.JSONDecodeError, TypeError):
                    return cls()
            return cls()

        config_file = get_config_file(email)
        if not config_file.exists():
            config = cls()
            config.email = email
            return config

        try:
            data = json.loads(config_file.read_text())
            # Filter to only known fields (backwards compatibility)
            known_fields = {"context_dir", "encryption_enabled"}
            filtered = {k: v for k, v in data.items() if k in known_fields}
            config = cls(**filtered)
            config.email = email
            return config
        except (json.JSONDecodeError, TypeError):
            config = cls()
            config.email = email
            return config


def get_tokens(email: Optional[str] = None) -> Optional[Tokens]:
    """Get stored OAuth tokens for an account.

    Args:
        email: If provided, get tokens for this account.
               If None, get tokens for the active account.
    """
    return Tokens.load(email)


def get_config(email: Optional[str] = None) -> Config:
    """Get configuration for an account.

    Args:
        email: If provided, get config for this account.
               If None, get config for the active account.
    """
    return Config.load(email)


def is_logged_in(email: Optional[str] = None) -> bool:
    """Check if user is logged in.

    Args:
        email: If provided, check if this account is logged in.
               If None, check if the active account is logged in.
    """
    tokens = get_tokens(email)
    return tokens is not None and tokens.id_token


def get_email() -> Optional[str]:
    """Get the active account's email."""
    return get_active_account()


def list_accounts() -> list[str]:
    """List all configured accounts."""
    if not ACCOUNTS_DIR.exists():
        return []
    return [
        d.name for d in ACCOUNTS_DIR.iterdir()
        if d.is_dir() and (d / "tokens.json").exists()
    ]


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
