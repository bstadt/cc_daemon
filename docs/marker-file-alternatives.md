# Alternatives to Marker Files

> **✅ RESOLUTION**: We implemented the "no tracking" approach using modification time comparison for imports and relying on SVN's built-in change detection for commits. No marker files needed!

## ~~Current~~ Previous Problem

We currently use two types of marker files:
1. `.imported` - Tracks which Claude Code JSONL files have been imported to markdown
2. `.peer-committed` - Tracks which interactive transcripts have been committed to peer repos

**Issues:**
- "Littering" - creates many small files alongside actual data
- Not atomic - marker creation separate from the action it tracks
- Harder to clean up - orphaned markers if files deleted
- Not centralized - distributed tracking state

## Alternative Approaches

### Option 1: SQLite Database ⭐️ RECOMMENDED

**Implementation:**
```python
# ~/.claude-connect/state.db

CREATE TABLE imported_transcripts (
    jsonl_path TEXT PRIMARY KEY,
    jsonl_mtime REAL,
    imported_at TEXT,
    session_id TEXT,
    peer_email TEXT
);

CREATE TABLE committed_transcripts (
    session_id TEXT PRIMARY KEY,
    peer_email TEXT,
    committed_at TEXT,
    commit_revision TEXT
);
```

**Pros:**
✅ No file littering - single database file
✅ Atomic transactions - can't get inconsistent state
✅ Queryable - easy to see all imported/committed sessions
✅ Easy cleanup - DELETE WHERE peer_email = 'old@peer.com'
✅ Can store additional metadata (revision numbers, error logs, etc.)
✅ Better performance for bulk operations

**Cons:**
❌ Adds dependency (though sqlite3 is in Python stdlib)
❌ Slightly more complex implementation
❌ Need migration path from current marker files

**Code changes:**
```python
# sync.py
import sqlite3
from contextlib import contextmanager

class StateDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def mark_imported(self, jsonl_path: Path, session_id: str, peer_email: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO imported_transcripts VALUES (?, ?, ?, ?, ?)",
                (str(jsonl_path), jsonl_path.stat().st_mtime,
                 datetime.now().isoformat(), session_id, peer_email)
            )
            conn.commit()

    def is_imported(self, jsonl_path: Path) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT jsonl_mtime FROM imported_transcripts WHERE jsonl_path = ?",
                (str(jsonl_path),)
            )
            row = cur.fetchone()
            if not row:
                return False
            # Re-import if file has changed
            return jsonl_path.stat().st_mtime <= row[0]
```

---

### Option 2: Single JSON State File

**Implementation:**
```python
# ~/.claude-connect/state.json
{
  "imported_transcripts": {
    "/path/to/file.jsonl": {
      "mtime": 1706000000.0,
      "imported_at": "2026-01-23T12:00:00",
      "session_id": "uuid",
      "peer_email": "peer@example.com"
    }
  },
  "committed_transcripts": {
    "session-uuid-1": {
      "peer_email": "peer@example.com",
      "committed_at": "2026-01-23T12:00:00",
      "revision": "r123"
    }
  }
}
```

**Pros:**
✅ No file littering - single state file
✅ Human-readable/editable
✅ Simple implementation - just json.load/json.dump
✅ Easy to inspect and debug

**Cons:**
❌ Not atomic - race conditions possible
❌ Must read/write entire file for each operation
❌ Performance degrades with many entries
❌ No transactions - can corrupt if write fails mid-way
❌ Need file locking for concurrent access

**Code changes:**
```python
import json
import fcntl  # For file locking

class StateFile:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            path.write_text('{"imported_transcripts":{},"committed_transcripts":{}}')

    @contextmanager
    def _locked_state(self):
        with open(self.path, 'r+') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                state = json.load(f)
                yield state
                f.seek(0)
                f.truncate()
                json.dump(state, f, indent=2)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

---

### Option 3: Git Notes / SVN Properties

**Implementation:**
Use Git notes or SVN properties to track state in the version control system itself.

**Pros:**
✅ No extra files
✅ Version controlled
✅ Travels with the repo

**Cons:**
❌ Tightly coupled to VCS
❌ Can't track state for external files (Claude Code JSONL files)
❌ More complex to query/update
❌ Notes/properties are easy to lose on clone/checkout

**Verdict:** ❌ Not suitable - can't track state for files outside SVN

---

### Option 4: Modification Time Comparison (No Tracking)

**Implementation:**
Don't track at all. Just compare file modification times.

```python
# For imports:
# Always re-import if JSONL is newer than markdown transcript

# For commits:
# Always try to commit, SVN will detect if no changes
```

**Pros:**
✅ No tracking files needed
✅ Simple logic

**Cons:**
❌ Wasteful - re-processes unchanged files
❌ Doesn't work for commits (need to check SVN each time)
❌ Can't distinguish "never processed" from "already done"
❌ Creates unnecessary SVN operations

**Verdict:** ❌ Too inefficient

---

### Option 5: In-Memory Cache with Checkpoint File

**Implementation:**
Track state in memory during runtime, persist periodically.

```python
class StateCache:
    def __init__(self):
        self.imported = {}
        self.committed = {}
        self._load_checkpoint()

    def _load_checkpoint(self):
        # Load from ~/.claude-connect/state-checkpoint.json
        pass

    def _save_checkpoint(self):
        # Save every 5 minutes or on shutdown
        pass
```

**Pros:**
✅ Fast lookups (in-memory)
✅ Only one persistent file

**Cons:**
❌ State lost if crash before checkpoint
❌ Duplicate processing after restart
❌ Complex lifecycle management

**Verdict:** ❌ Not suitable - sync daemon can be killed anytime

---

## Recommendation: SQLite Database

**Migration Plan:**

1. **Phase 1: Add SQLite alongside markers**
   ```python
   # Check both marker file AND database
   if marker_exists() or state_db.is_imported():
       continue

   # Always write to both
   state_db.mark_imported()
   marker_file.write_text("")
   ```

2. **Phase 2: Migrate existing markers**
   ```python
   def migrate_markers_to_db():
       # Find all .imported markers
       for marker in find_markers():
           jsonl_path = marker.with_suffix('.jsonl')
           session_id = extract_session_id(jsonl_path)
           peer_email = extract_peer_email(jsonl_path)
           state_db.mark_imported(jsonl_path, session_id, peer_email)
           # Keep marker for now (backward compat)

       # Same for .peer-committed
   ```

3. **Phase 3: Remove marker file logic**
   ```python
   # Only use database
   if state_db.is_imported():
       continue

   state_db.mark_imported()
   # No marker file created
   ```

4. **Phase 4: Clean up old markers (optional)**
   ```python
   claudeconnect cleanup-markers  # CLI command
   ```

**Implementation Estimate:**
- StateDB class: ~100 lines
- Migration logic: ~50 lines
- Update discovery/import/commit: ~30 lines
- Tests: ~100 lines
- **Total: ~280 lines, ~2-3 hours**

**Benefits Summary:**
- Reduces clutter from dozens/hundreds of marker files to 1 database
- Better performance (no filesystem operations per transcript)
- Easier to debug (query database to see all state)
- More robust (atomic transactions)
- Enables future features (analytics, cleanup, bulk operations)

## Alternative: Keep Markers but Use Hidden Directory

**Implementation:**
```python
# Instead of:
transcript.peer-committed

# Use:
~/.claude-connect/markers/committed/session-uuid
~/.claude-connect/markers/imported/session-uuid

# Or even:
~/.claude-connect/markers/
  committed.txt  # One file with session UUIDs, one per line
  imported.txt   # One file with jsonl paths, one per line
```

**Pros:**
✅ Minimal code change
✅ No new dependencies
✅ Still human-inspectable

**Cons:**
❌ Still tracking with files (just moved)
❌ No performance improvement
❌ Still need to handle filesystem operations

**Verdict:** 🤷 Better than current, but SQLite is cleaner
