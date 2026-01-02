"""Configuration and constants."""

from pathlib import Path

# Local paths
CONFIG_DIR = Path.home() / ".claude-connect"
TOKENS_FILE = CONFIG_DIR / "tokens.json"

# Server
SERVER_URL = "https://claudeconnect.io"
LOGIN_URL = f"{SERVER_URL}/login"
REFRESH_URL = f"{SERVER_URL}/refresh"
SVN_BASE_URL = f"{SERVER_URL}/svn"

# Local callback
CALLBACK_PORT = 3407
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"


def ensure_config_dir():
    """Create config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
