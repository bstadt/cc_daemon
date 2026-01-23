"""Test configuration for ClaudeConnect integration tests."""

import os

# Test user emails - can be overridden via environment variables
ALICE_EMAIL = os.environ.get("CC_TEST_ALICE", "thisismysignupacct@gmail.com")
BOB_EMAIL = os.environ.get("CC_TEST_BOB", "brandonduderstadt@gmail.com")

# Server configuration
SERVER = "v2s.claudeconnect.io"
SERVER_URL = f"https://{SERVER}"
API_BASE_URL = f"{SERVER_URL}/api"

# SSH key for server access (for cleanup operations)
SSH_KEY_PATH = os.path.expanduser("~/.ssh/calco_key.pem")
