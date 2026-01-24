# Plan: Adapt Interactive Sessions to HTTP-Based Sync

## Current State Analysis

### What We Built (feature/interactive-sessions branch)
- ✅ Transcript discovery from `~/.claude/projects/` (Claude Code's JSONL storage)
- ✅ JSONL → Markdown conversion with metadata
- ✅ Timestamp-based filenames: `YYYY-MM-DD-HHMMSS_{uuid}.md`
- ✅ Filtering interactive sessions by `cwd` (only imports peer sessions)
- ✅ Background sync loop with transcript import
- ✅ Interactive header with dev mode support
- ❌ **Peer commits disabled** (placeholder, was waiting for HTTP migration)

### What Changed on Main (HTTP Sync v2)
- ❌ **Removed**: `sync.py` (entire SVN sync loop class)
- ❌ **Removed**: `svn_ops.py` (all SVN operations)
- ✅ **Added**: HTTP-based sync in `cli.py:sync_http()`
- ✅ **Added**: Background async sync loop `run_with_http_sync()` (30s interval)
- ✅ **Added**: `pull_peer_context_http()` for fetching peer files
- ✅ **Added**: `upload_file_http()` for uploading to peer repos
- ⚠️ **Stub**: `commit_interactive_transcript()` exists but doesn't commit

### Architecture Changes

**Old (SVN):**
```
SyncLoop class (sync.py)
  ├─ _sync_once() every 30s
  ├─ _import_claude_code_transcripts()
  └─ _commit_interactive_transcripts_to_peers()
```

**New (HTTP):**
```
run_with_http_sync() async loop
  ├─ sync_http() every 30s
  └─ ??? (need to add transcript handling)
```

---

## Implementation Plan

### Phase 1: Port Transcript Discovery (No Sync Dependency)

**Goal**: Restore transcript import functionality independent of sync loop

**New File**: `src/claudeconnect/transcripts.py`

**Functions to port**:
1. `context_dir_to_claude_projects_dir(context_dir: Path) -> str`
2. `_format_timestamp_for_filename(iso_timestamp: str) -> str`
3. `_extract_peer_email_from_cwd(cwd: str) -> str | None`
4. `_extract_jsonl_metadata(jsonl_path: Path) -> dict | None`
5. `convert_jsonl_to_markdown(jsonl_path: Path, metadata: dict, our_email: str) -> str`

**New functions**:
```python
def discover_new_interactive_transcripts(our_email: str, context_dir: Path) -> list[tuple[Path, dict]]:
    """
    Find new Claude Code transcripts from interactive sessions.

    Returns:
        List of (jsonl_file, metadata) tuples for unprocessed transcripts
    """
    # Same logic as old _discover_claude_code_transcripts()
    # - Find ~/.claude/projects/
    # - Map peer dirs to project dirs
    # - Filter by cwd containing /.claude-connect/peers/
    # - Check mtime (60s stability)
    # - Check if already imported (find existing *_{uuid}.md)
    pass

def import_transcript(jsonl_path: Path, metadata: dict, our_email: str, context_dir: Path) -> Path | None:
    """
    Import a single transcript to local context.

    Returns:
        Path to saved markdown file, or None on failure
    """
    # Convert JSONL to markdown
    # Generate timestamped filename
    # Save to context_dir/claudeconnect/with-{peer}/
    # Return path
    pass
```

**Why separate file?**
- Clean separation of concerns
- Can be imported by both `cli.py` and future sync modules
- Testable in isolation
- No circular dependencies

---

### Phase 2: Implement Transcript Committing via HTTP

**Goal**: Enable committing transcripts to peer repos using HTTP API

**Location**: `src/claudeconnect/transcripts.py`

**New function**:
```python
def commit_transcript_to_peer(
    transcript_path: Path,
    peer_email: str,
    our_email: str,
    id_token: str,
) -> bool:
    """
    Upload a transcript to a peer's repo via HTTP API.

    Args:
        transcript_path: Path to the markdown transcript file
        peer_email: Peer's email (owner of destination repo)
        our_email: Our email (for conversation folder structure)
        id_token: JWT token for authentication

    Returns:
        True if successful, False otherwise

    Implementation:
        1. Read transcript content
        2. Determine destination path in peer's repo:
           claudeconnect/with-{our_repo_name}/{filename}.md
        3. Use upload_file_http() to upload (encrypts with peer's key if enabled)
        4. Handle errors gracefully
    """
    from .session import upload_file_http
    from .config import email_to_repo_name

    # Read transcript
    content = transcript_path.read_bytes()

    # Destination path in peer's repo
    our_repo_name = email_to_repo_name(our_email)
    filename = transcript_path.name
    remote_path = f"claudeconnect/with-{our_repo_name}/{filename}"

    # Upload using HTTP (will encrypt with peer's key if encryption enabled)
    success = upload_file_http(
        email=peer_email,
        path=remote_path,
        content=content,
        id_token=id_token,
        encrypt_for=peer_email,
        use_friend_key=True,
    )

    return success
```

**Update**: `src/claudeconnect/session.py:commit_interactive_transcript()`

Replace the stub with actual implementation:
```python
def commit_interactive_transcript(session_id: str) -> tuple[bool, str]:
    """Commit an interactive session transcript to peer's repo."""
    from .cli import get_valid_token
    from .transcripts import commit_transcript_to_peer

    tokens = get_valid_token()
    config = get_config()
    our_email = tokens.email
    context_dir = Path(config.context_dir)

    # Find transcript by UUID pattern
    transcript_path = None
    peer_email = None

    claudeconnect_dir = context_dir / "claudeconnect"
    for conv_dir in claudeconnect_dir.glob("with-*"):
        for candidate in conv_dir.glob(f"*_{session_id}.md"):
            transcript_path = candidate
            # Extract peer email from markdown header
            content = transcript_path.read_text()
            for line in content.split('\n')[:10]:
                if line.startswith('**Representing**:'):
                    peer_email = line.split(':', 1)[1].strip()
                    break
            break
        if transcript_path:
            break

    if not transcript_path:
        return False, f"Transcript not found for session: {session_id}"

    if not peer_email:
        return False, "Could not determine peer email from transcript"

    # Commit to peer's repo
    success = commit_transcript_to_peer(
        transcript_path,
        peer_email,
        our_email,
        tokens.id_token,
    )

    if success:
        return True, str(transcript_path)
    else:
        return False, "Failed to upload to peer's repo"
```

---

### Phase 3: Integrate with Background Sync Loop

**Goal**: Add transcript handling to the 30-second sync loop

**Location**: `src/claudeconnect/cli.py:run_with_http_sync()`

**Option A: Extend sync_http() function**

Add transcript handling directly to the sync function:
```python
def sync_http(context_dir: Path, email: str, id_token: str, max_workers: int = 10) -> bool:
    # ... existing sync logic ...

    # After regular file sync, handle interactive transcripts
    from .transcripts import discover_new_interactive_transcripts, import_transcript, commit_transcript_to_peer

    new_transcripts = discover_new_interactive_transcripts(email, context_dir)

    for jsonl_path, metadata in new_transcripts:
        # Import to our context
        transcript_path = import_transcript(jsonl_path, metadata, email, context_dir)

        if transcript_path:
            # Commit to peer's repo
            peer_email = metadata["peer_email"]
            commit_transcript_to_peer(transcript_path, peer_email, email, id_token)

    return True
```

**Option B: Separate transcript sync task**

Add a second background task for transcript handling:
```python
async def run_with_http_sync(
    context_dir: Path,
    email: str,
    id_token: str,
    interval: int = 30,
):
    stop_event = asyncio.Event()

    async def sync_loop():
        """Background sync loop using HTTP."""
        while not stop_event.is_set():
            # ... existing sync_http() call ...

    async def transcript_sync_loop():
        """Background transcript import and commit loop."""
        from .transcripts import discover_new_interactive_transcripts, import_transcript, commit_transcript_to_peer

        while not stop_event.is_set():
            try:
                await asyncio.sleep(60)  # Check every 60 seconds

                tokens = get_valid_token()
                if tokens:
                    new_transcripts = discover_new_interactive_transcripts(tokens.email, context_dir)

                    for jsonl_path, metadata in new_transcripts:
                        transcript_path = import_transcript(jsonl_path, metadata, tokens.email, context_dir)

                        if transcript_path:
                            peer_email = metadata["peer_email"]
                            commit_transcript_to_peer(transcript_path, peer_email, tokens.email, tokens.id_token)
            except Exception:
                pass

    # Start both sync tasks
    sync_task = asyncio.create_task(sync_loop())
    transcript_task = asyncio.create_task(transcript_sync_loop())

    # ... rest of function ...
```

**Recommendation**: Option A (extend sync_http)
- Simpler, fewer moving parts
- Transcripts are part of "sync"
- Same 30-second interval
- Can be refactored later if needed

---

### Phase 4: Update run_interactive_session()

**Goal**: Remove dev mode notice about disabled commits

**Location**: `src/claudeconnect/session.py:run_interactive_session()`

**Changes**:
1. Remove `YELLOW` notice about "Peer commits temporarily disabled"
2. Update message to reflect that commits happen automatically via sync

**Updated message**:
```python
print(f"\n✓ Interactive session started!")
print(f"\n  You're now chatting with {peer_email}'s Claude in the new window.")
print(f"  When you're done, press Ctrl+D or type 'exit'.")
print(f"\n  The conversation will be automatically imported and committed")
print(f"  to both repos within 60-90 seconds after you exit.")
```

---

## Critical Design Decisions

### 1. Where to store transcript discovery logic?

**Decision**: New file `src/claudeconnect/transcripts.py`

**Rationale**:
- Clean separation from session management
- Avoids bloating cli.py (already 2000+ lines)
- Can be tested independently
- Future-proof for additional transcript features

### 2. How to integrate with sync loop?

**Decision**: Extend `sync_http()` function (Option A)

**Rationale**:
- Simplest implementation
- Transcripts are conceptually part of "sync"
- Same 30-second interval as file sync
- Less async complexity

**Alternatives considered**:
- Separate async task (more complex, not needed yet)
- Manual command only (doesn't meet user's "automatic" requirement)

### 3. Error handling for failed commits?

**Decision**: Silent failure with logging, retry on next sync

**Rationale**:
- Consistent with existing sync_http() behavior
- Network issues are transient
- User shouldn't be interrupted during Claude session
- Can add explicit "retry failed commits" command later

### 4. Should we delete JSONL files after import?

**Decision**: No, leave them alone

**Rationale**:
- Claude Code owns those files
- User might want to re-import
- No storage pressure (files are small)
- Safer to be conservative

---

## Potential Issues & Mitigations

### Issue 1: Circular imports

**Risk**: `transcripts.py` imports from `session.py`, `session.py` imports from `transcripts.py`

**Mitigation**:
- Keep imports inside functions (lazy imports)
- Use TYPE_CHECKING blocks for type hints
- Ensure clear dependency direction: cli → transcripts → session

### Issue 2: Race condition during import

**Risk**: User exits interactive session, JSONL is still being written, we import incomplete file

**Mitigation**:
- Already handled: 60-second mtime stability check
- Only import if file hasn't been modified in 60+ seconds

### Issue 3: Network failures during commit

**Risk**: Transcript imported locally but fails to upload to peer

**Mitigation**:
- Track which transcripts have been uploaded (add metadata file?)
- Or: Always try to upload on every sync if transcript exists locally
- Rely on HTTP API idempotency (uploading same file twice is safe)

**Recommendation**: Don't track upload status, just always try. HTTP API should be idempotent.

### Issue 4: Encryption key not available

**Risk**: Peer has encryption enabled but we don't have their master key

**Mitigation**:
- `upload_file_http()` already handles this
- If `encrypt_for` is specified but key unavailable, upload fails gracefully
- User will see sync error (consistent with regular file sync behavior)

---

## Testing Strategy

### Unit Tests (`tests/test_transcripts.py`)

1. `test_context_dir_to_claude_projects_dir()`
2. `test_format_timestamp_for_filename()`
3. `test_extract_peer_email_from_cwd()`
4. `test_extract_jsonl_metadata()`
5. `test_convert_jsonl_to_markdown()`
6. `test_discover_new_interactive_transcripts()` (with mock filesystem)
7. `test_import_transcript()`

### Integration Tests

1. Create mock JSONL file in `~/.claude/projects/`
2. Wait for sync (or trigger manually)
3. Verify markdown file appears in local context
4. Verify upload to peer's repo (requires test server)

### Manual Testing

1. Start interactive session with test peer
2. Have conversation (3-5 exchanges)
3. Exit session (Ctrl+D)
4. Wait 90 seconds
5. Verify transcript appears locally
6. Verify transcript appears in peer's repo (check via web or pull peer context)

---

## Migration Path

### Step 1: Create transcripts.py with ported functions ✅
- Port all helper functions from old sync.py
- Add discover/import functions
- No sync integration yet
- Can test in isolation

### Step 2: Implement commit_transcript_to_peer() ✅
- Use existing upload_file_http()
- Handle encryption automatically
- Add error handling

### Step 3: Update commit_interactive_transcript() ✅
- Replace stub with real implementation
- Use new transcripts module
- Test manually (can call directly)

### Step 4: Integrate with sync_http() ✅
- Add transcript handling at end of sync
- Test with background sync loop

### Step 5: Update UI messages ✅
- Remove "temporarily disabled" notices
- Update to reflect automatic commits

### Step 6: Documentation ✅
- Update CLAUDE.md
- Update system.md if exists
- Add docstrings

---

## Timeline Estimate

**Not providing timeline estimates per instructions** - focus is on what needs to be done, not when.

---

## Open Questions

1. **Should we commit transcripts to our own repo too?**
   - Currently we only save locally and upload to peer
   - Should we also upload to our own repo via HTTP?
   - **Answer**: Yes, probably - makes our repo complete
   - **Implementation**: Call `upload_file_http(our_email, ...)` after importing

2. **What if peer doesn't have write permission for us?**
   - Currently we just fail silently
   - Should we notify user?
   - **Answer**: Consistent with file sync - silent failure is OK

3. **Should transcript import be configurable?**
   - Some users might not want automatic import
   - **Answer**: Not yet - keep it simple, add config later if requested

4. **Handle old UUID-only filenames?**
   - What if transcripts already exist with old format `{uuid}.md`?
   - **Answer**: Pattern matching `*_{uuid}.md` won't find them
   - **Solution**: Also check for exact `{uuid}.md` as fallback

---

## Success Criteria

✅ Interactive session transcripts automatically imported from Claude Code storage
✅ Transcripts automatically committed to peer repos within 90 seconds
✅ Transcripts committed to our own repo too
✅ Works with encryption enabled/disabled
✅ No user interaction required after exiting session
✅ Silent background operation (no spam in terminal)
✅ Graceful handling of network failures
✅ Compatible with timestamp-based filenames
✅ Dev mode support maintained
✅ All existing interactive session features preserved
