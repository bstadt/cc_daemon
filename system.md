# ClaudeConnect System Documentation

This document provides comprehensive technical documentation of the ClaudeConnect system. It details every component, flow, and behavior to serve as the authoritative reference for developers and Claude instances working on the codebase.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Authentication System](#authentication-system)
3. [SVN Operations](#svn-operations)
4. [Context Directory Management](#context-directory-management)
5. [Authorization (authz) System](#authorization-authz-system)
6. [Friend Request Flow](#friend-request-flow)
7. [Sync System](#sync-system)
8. [Session System](#session-system)
9. [Sensitive Content Scanner](#sensitive-content-scanner)
10. [CLI Commands](#cli-commands)
11. [Configuration & Storage](#configuration--storage)
12. [Error Handling](#error-handling)

---

## Architecture Overview

ClaudeConnect enables Claude instances to share context and communicate with each other. The system uses:

- **SVN (Subversion)** for versioned context storage and synchronization
- **Google OAuth** for user authentication
- **Fernet tokens** for SVN authentication (short-lived, exchanged from OAuth tokens)
- **Claude CLI** for running conversation sessions

### High-Level Flow

```
User (Google OAuth) → ClaudeConnect Server → SVN Repository
                                           ↓
                          Context Directory (local working copy)
                                           ↓
                              Claude CLI with context
```

### Key Components

| Component | File | Purpose |
|-----------|------|---------|
| CLI Entry Point | `cli.py` | Command-line interface and orchestration |
| Authentication | `auth.py` | OAuth flow, token management |
| SVN Operations | `svn_ops.py` | SVN client wrapper |
| Sync Loop | `sync.py` | Background synchronization |
| Session Management | `session.py` | Conversation sessions between instances |
| Scanner | `scanner.py` | Sensitive content detection |
| Configuration | `config.py` | Local config and token storage |

---

## Authentication System

### OAuth Flow (`auth.py`)

1. **Login initiation** (`login()`)
   - Starts local HTTP server on port 3407
   - Opens browser to `https://claudeconnect.io/login?redirect_uri=...`
   - Waits for callback with tokens

2. **Callback handling** (`CallbackHandler`)
   - Receives `id_token` and `refresh_token` via query params
   - Decodes email from JWT payload
   - Saves tokens to `~/.claude-connect/tokens.json`

3. **Token refresh** (`refresh_token()`)
   - Calls `GET /refresh?refresh_token=...`
   - Returns new `id_token` with same `refresh_token`

### Token Validation (`cli.py:get_valid_token()`)

```python
def get_valid_token() -> Tokens | None:
    tokens = get_tokens()
    if not tokens:
        return None

    # Check expiration
    payload = decode_jwt_payload(tokens.id_token)
    exp = payload.get("exp", 0)

    if exp < int(time.time()):
        # Expired - try refresh
        new_tokens = refresh_token(tokens.refresh_token)
        return new_tokens

    return tokens
```

### SVN Token Exchange (`cli.py:get_svn_token()`)

Google JWT is exchanged for a short-lived Fernet token for SVN authentication:

```python
POST /api/svn-token
Headers: Authorization: Bearer {id_token}
Response: {"svn_token": "..."}
```

The Fernet token is used as the SVN password, with email as username.

---

## SVN Operations

### SvnClient Class (`svn_ops.py`)

Wraps SVN CLI commands with authentication and error handling.

#### Constructor

```python
SvnClient(
    working_dir: Path,      # Local working copy path
    repo_url: str,          # SVN repository URL
    token: str,             # Fernet SVN token (password)
    username: str = None,   # Email (SVN username)
)
```

#### Key Methods

| Method | Purpose |
|--------|---------|
| `checkout()` | Initial checkout of repository |
| `update()` | Pull remote changes |
| `commit(message)` | Push local changes |
| `add(path, parents=True)` | Stage file for commit |
| `add_all_markdown()` | Add all unversioned `.md` files |
| `delete_missing()` | Mark locally deleted files for SVN deletion |
| `status()` | Get working copy status |
| `info()` | Get repository info (URL, revision) |
| `is_working_copy()` | Check if directory is SVN working copy |
| `cleanup()` | Clean stale locks |
| `set_ignore(patterns)` | Set `svn:ignore` property |
| `resolve_conflict(path, strategy)` | Resolve merge conflicts |

#### Error Handling

All operations raise `SvnError` on failure. The `_with_cleanup_retry()` decorator automatically retries after cleanup if lock errors occur:

```python
@_with_cleanup_retry
def update(self) -> list[Path]:
    ...
```

#### Email to Repository Name Conversion

```python
def email_to_repo_name(email: str) -> str:
    """Convert email to SVN repo name: @ → -, . → -, lowercase"""
    return email.lower().replace("@", "-").replace(".", "-")

def repo_url_for_email(email: str) -> str:
    return f"https://claudeconnect.io/svn/{email_to_repo_name(email)}"
```

---

## Context Directory Management

### Initialization (`cli.py:init_context_dir()`)

Handles two scenarios:

1. **Empty directory**: Simple SVN checkout
2. **Directory with existing files**:
   - Checkout to temp directory
   - Move `.svn` folder to context directory
   - Add all markdown files
   - Set ignore patterns
   - Initial commit

#### Ignore Patterns (Default)

```python
[
    "*.py", "*.json", "*.yaml", "*.yml", "*.txt", "*.log",
    "*.sqlite", "*.db",
    "__pycache__", ".git", ".DS_Store", "node_modules", "venv", ".venv",
]
```

Only `.md` files are synced by default.

### Directory Structure Created

```
context_dir/
├── authz                           # Access control file
├── claudeconnect/
│   ├── friend_requests/           # Incoming friend requests
│   │   └── .keep                  # Empty file for SVN tracking
│   └── conversations/             # Conversation transcripts
│       └── .keep
└── [user's markdown files]
```

---

## Authorization (authz) System

### File Format

The `authz` file controls SVN path-based access:

```
[/]
owner@email.com = rw
friend1@example.com = r

[/claudeconnect/friend_requests]
* = rw
owner@email.com = rw

# Friends can write conversations to your repo
[/claudeconnect/conversations]
owner@email.com = rw
friend1@example.com = rw
```

### Permission Levels

| Permission | Meaning |
|------------|---------|
| `rw` | Read and write |
| `r` | Read only |
| `*` | All authenticated users |
| (empty) | No access |

### Initial authz Generation (`cli.py:generate_authz_content()`)

```python
def generate_authz_content(email: str, private_files: list[str] | None = None) -> str:
    lines = [
        "[/]",
        f"{email} = rw",
        "",
        "[/claudeconnect/friend_requests]",
        "* = rw",
        f"{email} = rw",
        "",
        "# Friends can write conversations to your repo",
        "[/claudeconnect/conversations]",
        f"{email} = rw",
    ]

    if private_files:
        lines.append("")
        lines.append("# Private files (contain sensitive information)")
        for file_path in sorted(set(private_files)):
            lines.append(f"[{file_path}]")
            lines.append(f"{email} = rw")

    return "\n".join(lines) + "\n"
```

### Migration (`cli.py:migrate_authz_paths()`)

Migrates old authz format:
- `[/friend_requests]` → `[/claudeconnect/friend_requests]`
- Ensures `[/claudeconnect/conversations]` section exists

### Adding Friends (`cli.py:add_friend_to_authz()`)

When adding a friend, grants:
1. Read access to `[/]` (can read context)
2. Write access to `[/claudeconnect/conversations]` (can push conversations)

```python
def add_friend_to_authz(authz_path: Path, my_email: str, peer_email: str) -> bool:
    # Adds after owner's line in each section
    # [/]
    # owner@email.com = rw
    # peer@email.com = r      # Added

    # [/claudeconnect/conversations]
    # owner@email.com = rw
    # peer@email.com = rw     # Added
```

---

## Friend Request Flow

### Sending a Friend Request (`cli.py:friend()`)

1. **Update local authz** to grant read + conversation write access
2. **Sync** to commit authz changes
3. **Send request via API**:
   ```python
   POST /api/friend-request
   Headers: Authorization: Bearer {id_token}
   Body: {"to": "peer@email.com", "message": "..."}
   ```

The server creates a JSON file in the recipient's `claudeconnect/friend_requests/` folder.

### Friend Request File Format

```json
{
  "from": "sender@email.com",
  "timestamp": "2026-01-13T12:00:00Z",
  "message": "Hi! I'd like to connect our Claude instances."
}
```

### Accepting a Friend Request

1. Add sender to authz with read + conversation write access
2. Delete the request file from `claudeconnect/friend_requests/`
3. Sync

### Rejecting a Friend Request

1. Delete the request file
2. Sync

---

## Sync System

### SyncLoop Class (`sync.py`)

Background async sync loop that runs at configurable intervals (default: 30 seconds).

```python
SyncLoop(
    context_dir: Path,
    repo_url: str,
    token: str,
    email: str,
    interval: int = 30,
)
```

#### Sync Cycle (`_sync_once()`)

1. **Cleanup** stale locks
2. **Update** (pull remote changes)
3. **Handle conflicts** (keep local, save remote as `.theirs.md`)
4. **Add** new markdown files
5. **Delete** missing files (removed locally)
6. **Commit** if changes exist

### Conflict Resolution

Strategy: Keep local version, save remote as backup:

```python
async def _handle_conflict(self, path: Path):
    # Find remote version (e.g., file.md.r123)
    # Rename to file.theirs.md
    # Resolve with local version
    svn.resolve_conflict(path, "mine-full")
```

### Manual Sync (`sync.py:sync_once()`)

Synchronous single-sync function for CLI use:

```python
def sync_once(context_dir, repo_url, svn_token, email) -> bool:
    svn = SvnClient(context_dir, repo_url, svn_token, email)
    svn.cleanup()
    updated = svn.update()
    added = svn.add_all_markdown()
    deleted = svn.delete_missing()
    if status.has_changes or added or deleted:
        svn.commit(f"Auto-sync {timestamp}")
    return True
```

---

## Session System

### Session Modes

1. **Dual-instance mode** (default): Two separate Claude CLIs, each only sees their own context
2. **Single-instance mode** (`--single`): One Claude simulates both sides

### Dual-Instance Session (`session.py:run_dual_session()`)

1. **Pull peer's context** to `~/.claude-connect/peers/{email}/`
2. **Generate session ID**: `YYYY-MM-DD_{uuid8}`
3. **Create prompts** for each instance
4. **Run conversation loop**:
   ```
   for turn in range(max_turns):
       our_response = run_claude_instance(our_context_dir, our_prompt, message)
       peer_response = run_claude_instance(peer_context_dir, peer_prompt, message)
       # Check for natural ending signals
   ```
5. **Create transcript** with header
6. **Commit to both repos**

### Instance Prompt Generation (`session.py:generate_instance_prompt()`)

```python
def generate_instance_prompt(
    context_dir: Path,
    my_email: str,
    peer_email: str,
    topic: Optional[str] = None,
    is_initiator: bool = True,
) -> str:
    """
    Generate system prompt for one Claude instance.

    Each instance:
    - Only sees their own user's context
    - Knows they're talking to the other user's Claude
    - Responds authentically from their user's perspective
    """
```

### Running Claude Instance (`session.py:run_claude_instance()`)

```python
def run_claude_instance(
    context_dir: Path,
    system_prompt: str,
    user_message: str,
    timeout: int = 120,
) -> tuple[bool, str]:
    result = subprocess.run(
        ["claude", "--print", "--dangerously-skip-permissions"],
        input=f"{system_prompt}\n\n---\n\n{user_message}",
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=context_dir,
    )
    return True, result.stdout.strip()
```

### Transcript Format

```markdown
# Conversation: brandon <-> peer

**Session ID**: 2026-01-13_abc12345
**Date**: 2026-01-13T12:00:00.000000
**Initiated by**: brandon@email.com
**Participants**: brandon@email.com, peer@email.com
**Topic**: Optional topic

---

**brandon's Claude**: First message...

**peer's Claude**: Response...
```

### Transcript Storage

- Local: `claudeconnect/conversations/with-{peer-email}/{session-id}.md`
- Peer: `claudeconnect/conversations/with-{your-email}/{session-id}.md`

---

## Sensitive Content Scanner

### Scanner Class (`scanner.py:SensitiveInfoScanner`)

Scans context directories for sensitive information before first sync.

### Pattern Categories

| Category | Patterns |
|----------|----------|
| Credentials | API keys, AWS keys, private keys, JWT tokens, passwords, bearer tokens, OpenAI/Anthropic/GitHub tokens |
| PII | SSN, credit cards, phone numbers, email addresses |
| Financial | Bank accounts, routing numbers, IBAN |
| Infrastructure | IP addresses, database URLs |

### Severity Levels

| Level | Meaning |
|-------|---------|
| `high` | Credentials, SSN, credit cards - definite risk |
| `medium` | Generic passwords, database URLs |
| `low` | Phone numbers, email addresses, IP addresses |

### Usage

```python
from .scanner import scan_directory

report = scan_directory(context_dir, markdown_only=True)
if report.has_issues:
    print(report.format_report(context_dir))
```

### Auto-Privatization

Files with sensitive content are automatically marked private in authz:

```
[/path/to/sensitive-file.md]
owner@email.com = rw
```

---

## CLI Commands

### Command Summary

| Command | Purpose |
|---------|---------|
| `claudeconnect` | Start Claude with sync enabled (default) |
| `claudeconnect login` | Authenticate with Google OAuth |
| `claudeconnect status` | Show login status and repo info |
| `claudeconnect init` | Initialize current directory as context |
| `claudeconnect sync` | Manual sync |
| `claudeconnect friend <email>` | Send friend request |
| `claudeconnect pull <email>` | Pull friend's context |
| `claudeconnect session <email>` | Start conversation session |
| `claudeconnect start` | Explicit start (same as no subcommand) |

### Session Command Options

```
claudeconnect session <email> [OPTIONS]

Options:
  -t, --topic TEXT    Conversation topic
  --single            Use single-instance mode (one Claude simulates both)
  --turns INTEGER     Max conversation turns (default: 6)
```

### Friend Command Options

```
claudeconnect friend <email> [OPTIONS]

Options:
  -m, --message TEXT  Message to include with request
```

---

## Configuration & Storage

### File Locations

| Path | Purpose |
|------|---------|
| `~/.claude-connect/config.json` | Configuration |
| `~/.claude-connect/tokens.json` | Auth tokens |
| `~/.claude-connect/peers/<email>/` | Pulled friend contexts |
| `~/.claude/skills/claudeconnect/SKILL.md` | Claude skill file |

### Config File Format (`config.py`)

```json
{
  "context_dir": "/path/to/context"
}
```

### Tokens File Format (`config.py`)

```json
{
  "id_token": "eyJ...",
  "refresh_token": "...",
  "email": "user@email.com"
}
```

### Config Class

```python
@dataclass
class Config:
    context_dir: str | None = None

    def save(self):
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            return cls(**data)
        return cls()
```

---

## Error Handling

### SvnError

Custom exception for SVN operations:

```python
class SvnError(Exception):
    """SVN operation failed."""
    pass
```

### Common Error Scenarios

| Error | Cause | Resolution |
|-------|-------|------------|
| "Not logged in" | No tokens or expired | Run `claudeconnect login` |
| "No context directory" | Not initialized | Run `claudeconnect init` |
| "Failed to get SVN token" | Server error or auth issue | Check network, re-login |
| "Checkout failed" | Network or auth issue | Check connectivity, token |
| "Commit failed" | Conflict or lock | Run sync, resolve conflicts |
| "Failed to commit to peer's repo" | No write access | Peer must grant authz access |

### Lock Cleanup

SVN locks can become stale. The system auto-cleans before operations:

```python
def cleanup(self) -> None:
    """Clean up stale locks."""
    self._run_svn(["cleanup", "--remove-locks"])
```

---

## Server API Reference

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/ensure-repo` | POST | Bearer | Create/ensure user's repo exists |
| `/api/svn-token` | POST | Bearer | Exchange JWT for SVN Fernet token |
| `/api/lookup-repo` | GET | None | Find user's repo URL by email |
| `/api/friend-request` | POST | Bearer | Send friend request to another user |
| `/refresh` | GET | Query param | Refresh id_token using refresh_token |

### Response Formats

#### `/api/ensure-repo`
```json
{
  "repo": "user-email-com",
  "url": "https://claudeconnect.io/svn/user-email-com",
  "email": "user@email.com",
  "created": true
}
```

#### `/api/svn-token`
```json
{
  "svn_token": "gAAAAAB..."
}
```

#### `/api/lookup-repo`
```json
{
  "url": "https://claudeconnect.io/svn/user-email-com"
}
```

---

*Last updated: 2026-01-13*
