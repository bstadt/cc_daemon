"""Configuration management for Claude Connect."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict


CONFIG_DIR = Path.home() / ".claude-connect"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKENS_FILE = CONFIG_DIR / "tokens.json"


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
    svn_username: Optional[str] = None
    svn_password: Optional[str] = None

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
            return cls(**data)
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
