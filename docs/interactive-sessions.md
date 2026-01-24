# Interactive Sessions with HTTP Sync

## Overview

Interactive sessions allow users to chat directly with a Claude instance that has access to their peer's context in a new Terminal window. Conversations are automatically imported from Claude Code's native transcript storage and synced bidirectionally to both the user's and peer's repositories.

## Architecture

### Flow

1. User runs `claudeconnect interactive peer@example.com`
2. System pulls peer's context via HTTP
3. Opens new Terminal window with Claude running in peer's context directory
4. User interacts with Claude naturally (no special wrapper commands)
5. Claude Code saves conversation to `~/.claude/projects/` in JSONL format
6. Background sync loop discovers new transcripts (every 30 seconds)
7. Transcripts are imported to local context as Markdown
8. Transcripts are uploaded to user's repo (automatic via `sync_http()`)
9. Transcripts are uploaded to peer's repo (explicit API call)
10. Peer can pull and decrypt transcripts during their sync

### Key Components

**Module: `src/claudeconnect/transcripts.py`**

- `discover_new_interactive_transcripts()` - Finds new JSONL files in `~/.claude/projects/`
  - Filters by `cwd` field to only import peer context sessions
  - Ignores user's normal Claude Code usage
  - Checks file modification time (60s stability threshold)

- `import_transcript()` - Converts JSONL to Markdown and saves locally
  - Parses Claude Code's JSONL format
  - Extracts session metadata (ID, timestamps, peer email)
  - Generates Markdown with header and formatted conversation
  - Saves to `~/context/claudeconnect/with-{peer}/YYYY-MM-DD-HHMMSS_{uuid}.md`

- `commit_transcript_to_peer()` - Uploads transcript to peer's repository
  - Encrypts with peer's friend master key
  - Uploads via HTTP API to peer's repo

**Integration: `src/claudeconnect/cli.py:sync_http()`**

Background sync loop (30-second interval) now includes:
```python
# After regular file sync
new_transcripts = discover_new_interactive_transcripts(email, context_dir)
for jsonl_path, metadata in new_transcripts:
    transcript_path = import_transcript(jsonl_path, metadata, email, context_dir)
    if transcript_path and metadata.get("peer_email"):
        commit_transcript_to_peer(transcript_path, peer_email, email, id_token)
```

**Simplified: `src/claudeconnect/session.py:run_interactive_session()`**

Removed ~50 lines of complexity:
- No longer generates session IDs (Claude Code handles this)
- No longer uses `script` command to capture terminal I/O
- No longer creates transcript files upfront
- Simply opens Terminal and lets Claude Code save naturally

## Session Filtering (Critical)

**Problem**: We only want to import interactive sessions, NOT the user's normal Claude Code usage.

**Solution**: Check `cwd` field in JSONL metadata
- Interactive sessions have `cwd` pointing to `~/.claude-connect/peers/{peer-name}/`
- Regular Claude usage has `cwd` pointing to user's project directories
- Only import transcripts where `cwd` contains `/.claude-connect/peers/`

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

Comprehensive 11-step integration test:
1. Setup two test accounts (Alice and Bob)
2. Complete friend request flow
3. Alice starts interactive session with Bob
4. User manually interacts in Terminal window
5. Wait for transcript auto-discovery
6. Verify transcript content and format
7. Verify upload to Alice's server repo
8. Verify upload to Bob's server repo
9. Bob syncs to pull transcript
10. Verify Bob can decrypt and read transcript
11. Verify bidirectional sync complete

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
3. **Incomplete conversation**: 60-second stability check prevents importing mid-conversation
4. **Network failure during upload**: Will retry on next sync cycle
5. **Missing peer email**: Skips transcript with warning
6. **Malformed JSONL**: Wrapped in try/except, logs warning, continues

## Performance

- Background sync runs every 30 seconds
- Discovery only checks peer directories (not entire `~/.claude/projects/`)
- Modification time tracking prevents re-processing unchanged files
- `.imported` marker files track processed transcripts
- Minimal overhead (~100ms per sync cycle with no new transcripts)
