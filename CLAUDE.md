# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudeConnect is a peer-to-peer system that enables different Claude Code instances to share context and communicate. It uses SVN for versioned context storage, Google OAuth for authentication, and allows personalized Claude instances on different machines to maintain friendships and share contexts.

**Key design principle**: User sovereignty - each user controls their own SVN repo and authz file. No central permissions authority. Friending means updating YOUR permissions to grant access to YOUR repo.

## Commands

```bash
# Install in development mode
pip install -e .

# Run tests
pytest tests                    # All tests (60s timeout per test)
pytest tests/test_cli.py        # Specific test file
pytest tests/test_cli.py::TestCLIHelp::test_help_displays  # Single test

# CLI usage
claudeconnect status            # Check auth and sync status
claudeconnect sync              # Manual sync trigger
claudeconnect friend <email>    # Send friend request

# Mock dev mode (no server required)
CC_MOCK_DIR=./mock-env claudeconnect init   # Create mock environment
CC_MOCK_DIR=./mock-env claudeconnect start  # Run with mock data
```

## Architecture

```
User → Google OAuth → Server → SVN Repository
                              ↓
                Local context directory (~/claude/)
                              ↓
                Claude CLI with context
```

**Core modules** (`src/claudeconnect/`):
- `cli.py` - Entry point, all CLI commands, context initialization
- `auth.py` - OAuth flow, JWT tokens, token refresh
- `svn_ops.py` - SVN client wrapper with lock resilience
- `sync.py` - Background sync loop (30-second polling), conflict resolution
- `session.py` - Conversation sessions (dual-instance mode default)
- `scanner.py` - Sensitive content detection, auto-privatization
- `config.py` - Local config/token storage at `~/.claude-connect/`

## Documentation Requirements

**`system.md` is the authoritative technical reference.** Every code change that modifies system behavior must be reflected there. See `contributing.md` for the full documentation update checklist.

## Common Pitfalls

**SVN Operations:**
- `svn.add()` takes a single `Path`, not a list - iterate and add individually
- Always use `parents=True` when adding files in subdirectories

**Authentication:**
- Always use `get_valid_token()` not `get_tokens()` (handles expiry/refresh)
- SVN token ≠ OAuth token - exchange via API

**Authz:**
- Paths must start with `/`
- Friend needs BOTH read on `/` AND write on `/claudeconnect/conversations`
- `* =` blocks all access; `* = r` grants read to all

## Testing

Tests use ephemeral test users created on the server. Key fixtures in `tests/conftest.py`:
- `test_user` - Single ephemeral test user with auto-cleanup
- `test_context` - Initialized context directory
- `two_test_users` / `two_test_contexts` - Multi-user scenarios

## Code Style

- Type hints required for all function parameters and returns
- Use `from __future__ import annotations` for forward references
- Raise `SvnError` for SVN operation failures
- Exit code 1 on failure, 0 on success for CLI commands
