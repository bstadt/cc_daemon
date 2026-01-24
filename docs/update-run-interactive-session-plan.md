# Plan: Update run_interactive_session() for HTTP Sync

## Current Implementation (lines 846-950)

**What it does:**
1. ✅ Pulls peer's context via `pull_peer_context_http()`
2. ❌ Generates session_id: `YYYY-MM-DD_{uuid[:8]}`
3. ❌ Creates TRANSCRIPTS_DIR / `{session_id}.txt`
4. ✅ Generates interactive system prompt
5. ❌ Writes prompt to temp file
6. ❌ Uses `script -Fq {transcript_path}` to capture terminal output
7. ✅ Opens Terminal.app with `osascript`
8. ❌ Shows instructions to manually run `claudeconnect commit {session_id}`

**Problems:**
- Uses macOS-specific `script` command (not portable)
- Captures raw terminal output (ANSI codes, noise)
- Requires manual commit step
- Duplicates Claude Code's native transcript storage
- Creates `.txt` files instead of using Claude's `.jsonl`

---

## New Implementation Design

### Core Principle
**Let Claude Code handle transcript storage entirely. We just discover and import it later.**

### What We Need to Change

#### 1. Remove Session ID Generation
**Why:** Claude Code generates UUIDs automatically for each conversation

**Before:**
```python
session_id = datetime.now().strftime("%Y-%m-%d") + "_" + uuid4().hex[:8]
```

**After:**
```python
# No session_id needed - Claude Code will generate one
```

#### 2. Remove TRANSCRIPTS_DIR Usage
**Why:** We're using Claude Code's `~/.claude/projects/` storage

**Before:**
```python
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
transcript_path = TRANSCRIPTS_DIR / f"{session_id}.txt"
```

**After:**
```python
# No local transcript file - Claude Code saves to ~/.claude/projects/
```

#### 3. Remove `script` Command
**Why:** Claude Code's native JSONL storage is cleaner

**Before:**
```python
terminal_cmd = f"script -Fq {transcript_path} bash -c 'cd {peer_context_dir} && claude ...'"
```

**After:**
```python
terminal_cmd = f"cd {peer_context_dir} && claude --system-prompt \"$(cat {prompt_file})\" \"hi\""
```

#### 4. Keep System Prompt File (with cleanup)
**Why:** We still need to pass the interactive prompt to Claude

**Current:**
```python
prompt_file = TRANSCRIPTS_DIR / f"{session_id}_prompt.txt"
prompt_file.write_text(system_prompt)
```

**After:**
```python
# Use temp file with unique name
import tempfile
with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
    f.write(system_prompt)
    prompt_file = f.name
```

**Or simpler:**
```python
# Just write to /tmp with timestamp
prompt_file = Path(f"/tmp/claudeconnect-prompt-{uuid4().hex[:8]}.txt")
prompt_file.write_text(system_prompt)
```

#### 5. Update UI Messages
**Why:** User needs to know transcripts are automatic now

**Before:**
```
✓ Interactive session started!

  You're now chatting with peer@example.com's Claude in the new window.
  When you're done, press Ctrl+D twice to exit.

  After the session ends, run:
    claudeconnect commit {session_id}
```

**After:**
```
✓ Interactive session started!

  You're now chatting with peer@example.com's Claude in the new window.
  When you're done, press Ctrl+D to exit.

  The conversation will be automatically saved and synced to both repos
  within 60-90 seconds after you exit.
```

#### 6. Remove `script` Command Check
**Why:** We don't use it anymore

**Before:**
```python
# Check for script command
if not shutil.which("script"):
    return False, "The 'script' command is not available. Cannot capture transcript."
```

**After:**
```python
# No script check needed
```

#### 7. Simplify Return Value
**Why:** We don't have a session_id to return anymore

**Before:**
```python
return True, session_id
```

**After:**
```python
return True, f"Interactive session with {peer_email} started"
```

**Or:**
```python
return True, str(peer_context_dir)  # Return the peer context path
```

---

## Complete New Implementation

```python
def run_interactive_session(
    peer_email: str,
) -> tuple[bool, str]:
    """
    Start an interactive session with a peer's Claude in a new terminal window.

    Opens a new Terminal.app window (macOS only) where the user can chat directly
    with a Claude instance that has access to the peer's context.

    The conversation is automatically saved by Claude Code to ~/.claude/projects/
    and will be synced to both repos within 60-90 seconds after the session ends.

    Args:
        peer_email: The peer's email address

    Returns:
        Tuple of (success, message)
    """
    from .cli import get_valid_token

    # Check platform
    if platform.system() != "Darwin":
        return False, "Interactive sessions are only supported on macOS. Use `claudeconnect session` for autonomous conversations."

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_email = tokens.email

    # Pull peer's context
    print(f"\nPreparing interactive session with {peer_email}...")
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
    if not peer_context_dir:
        return False, f"Failed to pull {peer_email}'s context. Are you connected as friends?"

    # Generate system prompt
    system_prompt = generate_interactive_prompt(peer_email, our_email)

    # Write system prompt to a temp file (will be cleaned up eventually by OS)
    prompt_file = Path(f"/tmp/claudeconnect-prompt-{uuid4().hex[:8]}.txt")
    prompt_file.write_text(system_prompt)

    # Build the command to run in the new terminal
    # cd to peer context first so Claude sees it as its working directory
    # Pass "hi" as initial prompt to trigger Claude's greeting
    terminal_cmd = f'cd {peer_context_dir} && claude --system-prompt "$(cat {prompt_file})" "hi"'

    # Escape for AppleScript: backslash-escape double quotes and backslashes
    escaped_cmd = terminal_cmd.replace("\\", "\\\\").replace('"', '\\"')

    # Open new Terminal window with osascript
    osascript_cmd = f'''
    tell application "Terminal"
        do script "{escaped_cmd}"
        activate
    end tell
    '''

    print(f"\nOpening interactive session in new Terminal window...")
    print(f"  Peer context: {peer_context_dir}")

    try:
        result = subprocess.run(
            ["osascript", "-e", osascript_cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            error = result.stderr or "Unknown error"
            return False, f"Failed to open Terminal: {error}"

    except subprocess.TimeoutExpired:
        return False, "Timed out trying to open Terminal"
    except FileNotFoundError:
        return False, "osascript not found. Are you on macOS?"

    print(f"\n✓ Interactive session started!")
    print(f"\n  You're now chatting with {peer_email}'s Claude in the new window.")
    print(f"  When you're done, press Ctrl+D to exit.")
    print(f"\n  The conversation will be automatically saved and synced to both repos")
    print(f"  within 60-90 seconds after you exit.")

    return True, f"Interactive session with {peer_email} started"
```

---

## Testing Plan

### Manual Test
1. Run `claudeconnect interactive peer@example.com`
2. New Terminal window should open
3. Chat with Claude (3-5 exchanges)
4. Exit with Ctrl+D
5. Wait 90 seconds
6. Check `~/.claude/projects/` for new JSONL file
7. Check local context `claudeconnect/with-{peer}/` for imported markdown
8. Check our repo on server (should have the transcript)
9. Check peer's repo on server (should have the transcript)

### Edge Cases
1. **User exits immediately** - Should still create JSONL, import normally
2. **User has long conversation** - JSONL should be complete
3. **Network failure during upload** - Should retry on next sync
4. **Peer doesn't exist** - Should fail gracefully with clear message

---

## Files to Modify

### `src/claudeconnect/session.py`
**Function:** `run_interactive_session()` (lines 846-950)

**Changes:**
- Remove: `session_id` generation
- Remove: `TRANSCRIPTS_DIR` usage
- Remove: `script` command check
- Remove: `script -Fq` wrapper
- Update: prompt file to use `/tmp/`
- Update: UI messages
- Update: return value

**Estimated lines changed:** ~50 lines removed, ~10 lines modified

---

## Potential Issues

### 1. Prompt File Cleanup

**Issue:** Temp files in `/tmp/` accumulate over time

**Options:**
1. Leave them (OS cleans /tmp on reboot)
2. Use Python's tempfile with delete=False and track for cleanup
3. Use tempfile.mkstemp() for better control

**Recommendation:** Option 1 (simple, OS handles it)

Files are small (~500 bytes) and /tmp is regularly cleaned.

### 2. No Session ID for User Reference

**Issue:** User can't reference a specific session by ID anymore

**Impact:** Low - transcripts have timestamps in filename

**Mitigation:** User can find transcripts by date/time in `claudeconnect/with-{peer}/`

### 3. Detecting Session Completion

**Issue:** We don't know when user exits the session

**Impact:** None - we poll every 60 seconds anyway

**Current behavior:** Import happens when JSONL is stable (60s no modification)

### 4. Multiple Concurrent Sessions

**Issue:** User starts multiple sessions with same peer before first one syncs

**Impact:** None - each gets unique UUID from Claude Code

**Behavior:** All will be discovered and imported separately

---

## CLI Usage Changes

### Before (Old)
```bash
$ claudeconnect interactive peer@example.com
✓ Interactive session started!
  Session ID: 2026-01-23_abc12345

  After the session ends, run:
    claudeconnect commit 2026-01-23_abc12345
```

### After (New)
```bash
$ claudeconnect interactive peer@example.com
✓ Interactive session started!

  You're now chatting with peer@example.com's Claude in the new window.
  When you're done, press Ctrl+D to exit.

  The conversation will be automatically saved and synced to both repos
  within 60-90 seconds after you exit.
```

---

## Backwards Compatibility

### Question: What about old `.txt` transcripts in TRANSCRIPTS_DIR?

**Answer:** They won't be imported by the new system (which only looks at JSONL files)

**Options:**
1. Leave them alone (user can manually view if needed)
2. Add migration script to convert .txt → .md
3. Document that old transcripts need manual handling

**Recommendation:** Option 1 - old transcripts are rare, not worth migration code

---

## Success Criteria

✅ User can start interactive session with one command
✅ No manual commit step required
✅ Transcripts automatically appear in local context
✅ Transcripts automatically uploaded to both repos
✅ Works without `script` command (more portable)
✅ Cleaner transcript format (JSONL → Markdown conversion)
✅ Timestamp-based filenames for easy browsing

---

## Implementation Steps

1. ✅ Read current implementation
2. ✅ Write detailed plan (this document)
3. ⏳ Update `run_interactive_session()` function
4. ⏳ Test manually with a peer
5. ⏳ Verify transcript appears locally
6. ⏳ Verify transcript uploaded to both repos
7. ⏳ Update documentation
8. ⏳ Commit changes

---

## Open Questions

1. **Should we show the Claude Code project directory name?**
   - Could help user find the JSONL if they want to see it
   - Might be confusing/too technical
   - **Decision:** No, keep UI simple

2. **Should we validate that Claude Code is installed?**
   - Run `which claude` before opening terminal?
   - **Decision:** Yes, add check - fail fast if Claude not available

3. **Should we clean up prompt files on exit?**
   - Could add cleanup logic
   - **Decision:** No, keep simple - /tmp is auto-cleaned

4. **Should we support non-macOS platforms?**
   - Would need different terminal launching logic
   - **Decision:** No, out of scope - keep macOS-only for now
