# Interactive Sessions with HTTP Sync

## Overview

Interactive sessions allow users to chat directly with a Claude instance that has access to their peer's context in a new Terminal window. Conversations are automatically imported from Claude Code's native transcript storage and synced to the user's repository; peers pull transcripts from the user's `with-<peer>` folder during their normal sync.

## Architecture

### Flow

1. User runs `claudeconnect interactive peer@example.com`
2. System generates a UUID and creates pending session file in `~/.claude-connect/accounts/{email}/pending-sessions/{uuid}.json`
3. System pulls peer's context via HTTP
4. Opens new Terminal window with Claude running in peer's context directory, passing `--session-id {uuid}`
5. User interacts with Claude naturally (no special wrapper commands)
6. Claude Code saves conversation to `~/.claude/projects/.../{uuid}.jsonl`
7. Background sync loop discovers new transcripts by matching pending UUIDs (every 30 seconds)
8. Transcripts are imported to local context as Markdown
9. Pending session file remains until there are no changes for 24 hours (cleanup)
10. Transcripts are uploaded to user's repo (automatic via `sync_http()`)
11. Peer can pull and decrypt transcripts from the user's `with-<peer>` folder during their sync

### Key Components

**Module: `src/claudeconnect/transcripts.py`**

- `discover_new_interactive_transcripts()` - Matches pending sessions to JSONL files
  - Reads pending session files from `~/.claude-connect/accounts/{email}/pending-sessions/`
  - Searches `~/.claude/projects/**/{uuid}.jsonl` for each pending UUID
  - Multi-account safe: only imports sessions started by THIS account
  - Cleans up orphaned pending files after 24 hours of inactivity

- `import_transcript()` - Converts JSONL to Markdown and saves locally
  - Parses Claude Code's JSONL format
  - Extracts session metadata (ID, timestamps, peer email)
  - Generates Markdown with header and formatted conversation
  - Saves to `~/context/claudeconnect/with-{peer}/YYYY-MM-DD-HHMMSS_{uuid}.md`
  - Updates pending session metadata after import; cleanup happens after inactivity

- `cleanup_orphaned_pending_sessions()` - Removes stale pending files
  - Called automatically during discovery
  - Removes pending files after 24 hours of inactivity

**Integration: `src/claudeconnect/cli.py:sync_http()`**

Background sync loop (30-second interval) now includes:
```python
# After regular file sync
new_transcripts = discover_new_interactive_transcripts(email, context_dir)
for jsonl_path, metadata in new_transcripts:
    transcript_path = import_transcript(jsonl_path, metadata, email, context_dir)
    # Saved locally; uploaded on next sync. Peers pull from our with-<peer> folder.
```

**Module: `src/claudeconnect/session.py:run_interactive_session()`**

- Generates UUID for session tracking
- Creates pending session file with peer_email and timestamp
- Passes `--session-id {uuid}` to Claude Code via `claudeconnect start`
- Opens Terminal window with peer context

## Session Filtering (Critical)

**Problem**: We only want to import interactive sessions, NOT the user's normal Claude Code usage. On shared machines, multiple users may run interactive sessions.

**Solution**: Pending session files with UUID matching

1. When starting a session, we generate a UUID and create `pending-sessions/{uuid}.json`
2. We pass `--session-id {uuid}` to Claude Code so the JSONL is named `{uuid}.jsonl`
3. On sync, we only look for JSONL files matching our pending UUIDs
4. This is deterministic and multi-account safe - no path parsing or cwd checking needed

## File Naming

**Format**: `YYYY-MM-DD-HHMMSS_{session_uuid}.md`

Example: `2026-01-24-143022_a1b2c3d4-e5f6-7890-abcd-ef1234567890.md`

**Benefits**:
- Chronologically sortable
- Timestamp makes it easy to find recent conversations
- UUID ensures uniqueness
- `.md` extension for human readability

## Transcript Format

```markdown
# Interactive Session: alice@example.com ↔ bob@example.com's Claude

**Session ID**: a1b2c3d4-e5f6-7890-abcd-ef1234567890
**Date**: 2026-01-24T14:30:22Z
**User**: alice@example.com
**Representing**: bob@example.com
**Type**: interactive
**Source**: claude-code-transcript

---

**User** (2026-01-24T14:30:25Z):
Hi, can you help me understand the authentication flow?

**Assistant** (2026-01-24T14:30:30Z):
I'd be happy to help! Looking at the auth.py file in this codebase...
```

## Testing

**File**: `tests/test_interactive_sessions.py`

Comprehensive 10-step integration test:
1. Setup two test accounts (Alice and Bob)
2. Complete friend request flow
3. Alice starts interactive session with Bob
4. User manually interacts in Terminal window
5. Wait for transcript auto-discovery
6. Verify transcript content and format
7. Verify upload to Alice's server repo
8. Bob syncs to pull transcript
9. Verify Bob can decrypt and read transcript
10. Verify peer pull complete

**Run**: `pytest tests/test_interactive_sessions.py -s -m integration`

## Migration from SVN

### Previous Approach (SVN-based)
- Used macOS `script` command to capture raw terminal I/O
- Generated session IDs manually
- Created `.txt` files with ANSI codes and command echoes
- Required manual `claudeconnect commit {session_id}` step
- Used SVN for version control

### Current Approach (HTTP-based)
- Uses Claude Code's native JSONL storage
- Session IDs from Claude Code UUIDs
- Clean Markdown transcripts
- Fully automatic sync (no manual commit)
- Uses HTTP file storage with encryption

### Backwards Compatibility
Old `.txt` transcripts in `~/.claude-connect/transcripts/` are left untouched. The new system only processes JSONL files from `~/.claude/projects/`.

## Platform Support

**Current**: macOS only (uses Terminal.app via AppleScript)

**Future**: Could be extended to Linux/Windows by detecting platform and using appropriate terminal emulators.

## Edge Cases

1. **User exits immediately**: Empty session still creates JSONL, imports normally
2. **Multiple concurrent sessions**: Each has unique UUID, all imported separately
3. **Incomplete conversation**: Re-imports will catch updates as the JSONL grows
4. **Network failure during upload**: Will retry on next sync cycle
5. **Session crashes before Claude starts**: Orphaned pending file cleaned up after inactivity
6. **Malformed JSONL**: Wrapped in try/except, logs warning, continues
7. **Multiple accounts on same machine**: Each account has isolated pending-sessions directory
8. **Pending cleanup**: Pending files are removed after 24 hours of inactivity

## Performance

- Background sync runs every 30 seconds
- Discovery only searches for UUIDs from pending sessions (not entire `~/.claude/projects/`)
- Each pending UUID triggers a targeted `rglob` search for `{uuid}.jsonl`
- Modification time tracking prevents re-processing unchanged files
- Pending files serve as the "to-import" list - no marker files needed
- Minimal overhead when no pending sessions exist
