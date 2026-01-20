# Contributing to ClaudeConnect

This document provides guidelines for Claude instances and developers contributing to the ClaudeConnect codebase.

## Documentation Requirements

### Updating system.md

**Every code change that modifies system behavior must be reflected in `system.md`.**

When you make changes to the codebase, update the corresponding section in `system.md` to keep the documentation accurate. This is critical because:

1. Future Claude instances rely on this documentation to understand the system
2. The documentation serves as the authoritative reference for system behavior
3. Outdated docs lead to incorrect assumptions and bugs

### What Requires Documentation Updates

| Change Type | Documentation Required |
|-------------|----------------------|
| New CLI command | Add to CLI Commands section |
| New CLI option/flag | Add to relevant command section |
| New API endpoint | Add to Server API Reference |
| New file/directory created | Add to Directory Structure or Configuration sections |
| Changed authz behavior | Update Authorization System section |
| Changed sync behavior | Update Sync System section |
| Changed session behavior | Update Session System section |
| New error type | Add to Error Handling section |
| Changed authentication flow | Update Authentication System section |
| New config option | Add to Configuration section |
| Changed file format | Update relevant format documentation |

### Documentation Update Checklist

When making code changes:

- [ ] Identify which `system.md` sections are affected
- [ ] Update those sections with accurate, current information
- [ ] Update the "Last updated" date at the bottom of `system.md`
- [ ] If adding new functionality, add a new subsection if needed
- [ ] Ensure code examples in docs match actual implementation

## Code Style Guidelines

### Python

- Use type hints for all function parameters and returns
- Use `from __future__ import annotations` for forward references
- Follow existing patterns in the codebase
- Keep functions focused and single-purpose
- Use dataclasses for structured data

### Error Handling

- Raise `SvnError` for SVN operation failures
- Use descriptive error messages
- Log errors with appropriate context
- Handle expected failure cases gracefully

### CLI Commands

- Use Click decorators consistently
- Provide helpful `--help` text
- Check for authentication before operations requiring it
- Exit with code 1 on failure, 0 on success

## File Organization

```
src/claudeconnect/
├── __init__.py
├── auth.py         # OAuth/authentication
├── cli.py          # CLI entry point and commands
├── config.py       # Configuration management
├── scanner.py      # Sensitive content detection
├── session.py      # Conversation sessions
├── svn_ops.py      # SVN operations wrapper
├── sync.py         # Sync loop
└── skills/
    └── SKILL.md    # Claude skill file
```

### Adding New Modules

If adding a new module:

1. Create the file in `src/claudeconnect/`
2. Add appropriate docstring at top
3. Import in relevant places (usually `cli.py`)
4. Update `system.md` Architecture Overview table

## Testing Changes

### Local Testing

1. Install in development mode:
   ```bash
   pip install -e .
   ```

2. Test commands manually:
   ```bash
   claudeconnect status
   claudeconnect sync
   ```

### Multi-Instance Testing

For testing friend/session functionality:

1. Use a separate machine or VM for the second account
2. Or use `~/.claude-connect/peers/` for cached peer contexts
3. Test the full flow: friend request → accept → session

### Mock Dev Mode

For developing UX without server dependencies, use mock mode:

```bash
# Set up mock environment (one-time)
mkdir -p mock-env
CC_MOCK_DIR=./mock-env claudeconnect init

# Run commands in mock mode
CC_MOCK_DIR=./mock-env claudeconnect start
CC_MOCK_DIR=./mock-env claudeconnect status
```

**What mock mode provides:**
- Fake authentication (dev@example.com)
- Sample friend requests (carol@example.com, david@example.com)
- Sample conversations with alice@example.com
- Sample accepted friend notification from bob@example.com

**Mock directory structure:**
```
mock-env/
├── authz                          # Access control file
├── privacy.md                     # Privacy policy
├── claudeconnect/
│   ├── friend_requests/           # Pending friend requests
│   └── conversations/             # Conversation transcripts
├── notes/
│   └── sample-note.md
└── .mock/                         # Mock API responses
    ├── api-svn-token.json
    ├── api-ensure-repo.json
    └── config/tokens.json
```

**Key points:**
- Mock mode is activated by setting `CC_MOCK_DIR` environment variable
- All API calls are bypassed and return mock data
- SVN operations are skipped (no network required)
- Ideal for iterating on UI/UX without server setup

## Commit Messages

Use descriptive commit messages that explain:

- **What** changed
- **Why** it changed (if not obvious)

Examples:
```
Add --turns option to session command

Allow users to control conversation length in dual-instance mode.
```

```
Fix svn.add() called with list instead of single path

The SVN client's add() method expects a single Path, not a list.
Iterate and add files individually.
```

```
Migrate authz paths from /friend_requests to /claudeconnect/friend_requests

New directory structure keeps all claudeconnect files under one folder.
Migration runs automatically for existing repos.
```

## Common Pitfalls

### SVN Operations

- `svn.add()` takes a single `Path`, not a list
- Always use `parents=True` when adding files in subdirectories
- Clean up locks before operations if getting lock errors
- Check if directory is already a working copy before checkout

### Authentication

- Tokens expire - always use `get_valid_token()` instead of `get_tokens()`
- SVN token is different from OAuth token - exchange via API
- Email comes from JWT payload, not a separate field

### Authz

- Paths in authz must start with `/`
- Order of sections matters for SVN authorization
- `* =` (empty) blocks all access; `* = r` grants read to all
- Friend needs BOTH read on `/` AND write on `/claudeconnect/conversations`

### Sessions

- Dual-instance is default; use `--single` for legacy mode
- Each instance only sees their own context directory
- Transcripts are committed to both repos
- Peer repo commit may fail if they haven't granted write access

## Version Updates

When making significant changes:

1. Update version in `pyproject.toml`
2. Update version in `SKILL.md` metadata
3. Consider backwards compatibility

## Questions?

If unclear about how something works, read `system.md` first. If still unclear, check the source code - the documentation should match the implementation.

If you find discrepancies between docs and code, **update the docs** to match the actual behavior (unless the behavior is a bug).

---

*Last updated: 2026-01-20*
