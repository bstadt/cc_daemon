# Silent Background Sync

## Problem

Background sync was printing messages to stdout when the user opened their workspace:

```
✓ Committed to connectclaude5@gmail.com's repo
  Updating connectclaude5@gmail.com's context...
  Decrypted 5 files
```

These messages should only appear during interactive commands, not background operations.

## Solution

Added `verbose: bool = True` parameter to functions that can be called from both:
- Interactive CLI commands (should print)
- Background sync loop (should be silent)

### Functions Modified

1. **`pull_peer_context()`** (`session.py:282`)
   - Added `verbose: bool = True` parameter
   - Wrapped all print statements in `if verbose:` checks
   - Messages suppressed: updating, checkout, conflicts, decryption count, errors

2. **`decrypt_peer_context()`** (`session.py:235`)
   - Added `verbose: bool = True` parameter
   - Wrapped all print statements in `if verbose:` checks
   - Messages suppressed: missing key warning, progress updates, decryption errors

3. **`commit_interactive_transcript()`** (`session.py:993`)
   - Added `verbose: bool = True` parameter
   - Wrapped all print statements in `if verbose:` checks
   - Passes `verbose` through to `pull_peer_context()`
   - Messages suppressed: access errors, commit success/warnings

### Background Sync Calls

Updated sync loop (`sync.py`) to pass `verbose=False`:

```python
# In _commit_interactive_transcripts_to_peers()
commit_interactive_transcript(session_id, verbose=False)

# In _import_claude_code_transcripts()
commit_interactive_transcript(session_id, verbose=False)
```

### Interactive Commands (Still Verbose)

These commands continue to show output (default `verbose=True`):
- `claudeconnect pull <peer@email.com>`
- `claudeconnect session <peer@email.com>`
- `claudeconnect interactive <peer@email.com>`

## Result

Background sync now operates silently. Users only see output when explicitly running commands.

**Before:**
```bash
$ cd ~/my-project
$ claude  # Opens workspace with claudeconnect
✓ Committed to connectclaude5@gmail.com's repo
  Updating connectclaude5@gmail.com's context...
  Decrypted 5 files
# ^ Annoying clutter
```

**After:**
```bash
$ cd ~/my-project
$ claude  # Opens workspace with claudeconnect
# ^ Silent, no background sync noise
```

**Interactive commands still show output:**
```bash
$ claudeconnect pull friend@example.com
Pulling friend@example.com's context...
  Updating friend@example.com's context...
  Pulled 3 updates
  Decrypted 5 files
✓ Successfully pulled friend@example.com's context to ~/.claude-connect/peers/friend-example-com
```

## Temporary: Peer Commits Disabled

While SVN implementation is being reworked, commits to peer repos are temporarily disabled (`sync.py:_commit_interactive_transcripts_to_peers()`).

**Current behavior:**
- ✅ Transcripts imported from Claude Code storage
- ✅ Transcripts saved to your local context
- ✅ Transcripts committed to YOUR repo (via normal sync)
- ❌ Transcripts NOT pushed to peer's repo (temporarily disabled)

**When re-enabled:**
- Uncomment the early `return` in `_commit_interactive_transcripts_to_peers()`
- Update with new SVN implementation
- Any transcripts saved locally will be picked up and committed
