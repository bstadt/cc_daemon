"""
Pytest configuration and fixtures for ClaudeConnect tests.
"""

import pytest


# Default test accounts
DEFAULT_ACCOUNT1 = "brandonduderstadt@gmail.com"
DEFAULT_ACCOUNT2 = "thisismysignupacct@gmail.com"


def pytest_addoption(parser):
    """Add custom pytest command-line options for integration tests."""
    parser.addoption(
        "--account1",
        action="store",
        default=DEFAULT_ACCOUNT1,
        help=f"Email for Account 1 in integration tests (default: {DEFAULT_ACCOUNT1})"
    )
    parser.addoption(
        "--account2",
        action="store",
        default=DEFAULT_ACCOUNT2,
        help=f"Email for Account 2 in integration tests (default: {DEFAULT_ACCOUNT2})"
    )
