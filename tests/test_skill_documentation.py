"""
Tests to verify SKILL.md documentation matches actual CLI commands.

Catches documentation drift - when CLI changes but SKILL.md isn't updated.
"""

import re
from pathlib import Path

import pytest

from helpers import run_cli


# Path to SKILL.md
SKILL_MD_PATH = Path(__file__).parent.parent / "src" / "claudeconnect" / "skills" / "SKILL.md"


def extract_documented_commands(skill_content: str) -> list[dict]:
    """
    Extract CLI commands documented in SKILL.md.

    Looks for lines like:
        claudeconnect sync       # comment
        claudeconnect init --no-encrypt  # comment
        claudeconnect friend <email>

    Returns list of dicts with 'command', 'subcommand', 'options' keys.
    """
    commands = []

    # Match lines starting with 'claudeconnect' in code blocks
    # Pattern: claudeconnect [subcommand] [options/args]
    pattern = r'^claudeconnect\s+(\S+)?(?:\s+(.*))?'

    for line in skill_content.split('\n'):
        line = line.strip()

        # Skip if not a claudeconnect command
        if not line.startswith('claudeconnect'):
            continue

        # Remove trailing comments
        if '#' in line:
            line = line.split('#')[0].strip()

        match = re.match(pattern, line)
        if match:
            subcommand = match.group(1)
            rest = match.group(2) or ""

            # Extract options (--something or -x)
            options = re.findall(r'(--?\w+(?:-\w+)*)', rest)

            # Skip if it's just 'claudeconnect' with no subcommand (that's valid - runs start)
            if subcommand:
                commands.append({
                    'command': f'claudeconnect {subcommand}',
                    'subcommand': subcommand,
                    'options': options,
                    'full_line': line,
                })

    return commands


def get_cli_commands() -> set[str]:
    """Get list of actual CLI commands by running --help."""
    result = run_cli(["--help"])

    commands = set()
    in_commands_section = False

    for line in result.stdout.split('\n'):
        if 'Commands:' in line:
            in_commands_section = True
            continue
        if in_commands_section:
            # Command lines are indented and have format: "  command  Description"
            match = re.match(r'^\s+(\S+)\s+', line)
            if match:
                commands.add(match.group(1))
            elif line.strip() == '':
                # Empty line might end the section
                continue
            elif not line.startswith(' '):
                # Non-indented line ends the section
                break

    return commands


def get_command_options(subcommand: str) -> set[str]:
    """Get options for a specific subcommand by running --help."""
    result = run_cli([subcommand, "--help"])

    options = set()

    # Match --option or -x patterns
    for match in re.finditer(r'(--?\w+(?:-\w+)*)', result.stdout):
        opt = match.group(1)
        # Filter out common non-options
        if opt not in ('--help', '-h') and not opt.startswith('---'):
            options.add(opt)

    return options


class TestSkillDocumentation:
    """Tests for SKILL.md documentation accuracy."""

    def test_skill_file_exists(self):
        """SKILL.md should exist."""
        assert SKILL_MD_PATH.exists(), f"SKILL.md not found at {SKILL_MD_PATH}"

    def test_documented_commands_exist(self):
        """All commands documented in SKILL.md should exist in CLI."""
        skill_content = SKILL_MD_PATH.read_text()
        documented = extract_documented_commands(skill_content)
        actual_commands = get_cli_commands()

        missing = []
        for doc in documented:
            subcommand = doc['subcommand']
            # Handle hyphenated commands (accept-friend -> accept-friend)
            if subcommand not in actual_commands:
                missing.append(f"{doc['full_line']} (subcommand '{subcommand}' not found)")

        assert not missing, (
            f"SKILL.md documents commands that don't exist:\n" +
            "\n".join(f"  - {m}" for m in missing) +
            f"\n\nActual commands: {sorted(actual_commands)}"
        )

    def test_documented_options_exist(self):
        """Options documented in SKILL.md should exist for their commands."""
        skill_content = SKILL_MD_PATH.read_text()
        documented = extract_documented_commands(skill_content)

        missing = []
        for doc in documented:
            subcommand = doc['subcommand']
            doc_options = doc['options']

            if not doc_options:
                continue

            try:
                actual_options = get_command_options(subcommand)
            except Exception:
                # Command might not exist or have issues
                continue

            for opt in doc_options:
                # Skip placeholders like <email>
                if opt.startswith('<') or opt.startswith('['):
                    continue
                if opt not in actual_options and opt not in ('-t', '-m'):
                    # -t and -m are short forms that might not show in help
                    missing.append(f"{doc['full_line']}: option '{opt}' not found")

        assert not missing, (
            f"SKILL.md documents options that don't exist:\n" +
            "\n".join(f"  - {m}" for m in missing)
        )

    def test_key_locations_documented(self):
        """Key file locations should be documented."""
        skill_content = SKILL_MD_PATH.read_text()

        required_locations = [
            "~/.claude-connect/config.json",
            "~/.claude-connect/tokens.json",
            "~/.claude-connect/keys/",
            "~/.claude-connect/friends/",
            "authz",
        ]

        missing = []
        for loc in required_locations:
            if loc not in skill_content:
                missing.append(loc)

        assert not missing, (
            f"SKILL.md missing documentation for locations:\n" +
            "\n".join(f"  - {m}" for m in missing)
        )

    def test_encryption_documented(self):
        """Encryption should be documented as default."""
        skill_content = SKILL_MD_PATH.read_text()

        assert "encryption" in skill_content.lower(), "Encryption not mentioned"
        assert "--no-encrypt" in skill_content, "--no-encrypt option not documented"
        assert "X25519" in skill_content or "AES-256-GCM" in skill_content, \
            "Encryption algorithm not documented"

    def test_no_deprecated_message_option(self):
        """Friend command should not document --message option (removed)."""
        skill_content = SKILL_MD_PATH.read_text()

        # Look for friend command with -m or --message
        friend_with_message = re.search(
            r'claudeconnect\s+friend\s+.*(-m|--message)',
            skill_content
        )

        assert friend_with_message is None, (
            "SKILL.md still documents --message/-m option for friend command (removed)"
        )


class TestSkillExamples:
    """Tests for example commands in SKILL.md."""

    def test_example_friend_command_no_message(self):
        """Example friend commands should not include message."""
        skill_content = SKILL_MD_PATH.read_text()

        # Find all example friend commands
        friend_examples = re.findall(
            r'claudeconnect friend \S+.*',
            skill_content
        )

        for example in friend_examples:
            assert '-m' not in example and '--message' not in example, (
                f"Example still has message: {example}"
            )

    def test_init_example_shows_default_encryption(self):
        """Init examples should show encryption is default."""
        skill_content = SKILL_MD_PATH.read_text()

        # Should have plain 'init' (encryption on) and 'init --no-encrypt'
        assert re.search(r'claudeconnect init\s+#.*encryption', skill_content, re.IGNORECASE), \
            "Init command should mention encryption in comment"
        assert "claudeconnect init --no-encrypt" in skill_content, \
            "Should document --no-encrypt option"
