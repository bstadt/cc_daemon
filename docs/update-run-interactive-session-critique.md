# Critique: Update run_interactive_session() Plan

## What's Good ✅

### 1. Simplification
- **Removes ~50 lines of code**
- **Removes macOS-specific `script` command** (well, still macOS-only for Terminal.app, but one less dependency)
- **Single source of truth**: Claude Code owns transcript storage

### 2. Better User Experience
- **No manual commit step** - fully automatic
- **Cleaner transcripts** - JSONL → Markdown, not raw terminal output
- **Consistent with Claude Code's native flow**

### 3. Clear Migration Path
- Old `.txt` transcripts left alone (not breaking)
- New system completely separate
- Easy to test without affecting old data

---

## What's Problematic ❌

### 1. CRITICAL: We're Still macOS-Only

**Problem:** The plan doesn't address portability

**Current state:**
- Uses `osascript` (macOS only)
- Uses Terminal.app (macOS only)

**Why this matters:**
- Many users on Linux
- Some on Windows

**Alternatives:**
1. Detect OS and use appropriate terminal launcher:
   - macOS: `osascript` + Terminal.app
   - Linux: `gnome-terminal`, `xterm`, `konsole`, etc.
   - Windows: `wt.exe` (Windows Terminal), `cmd.exe`

2. Just launch Claude in current terminal (no new window):
   ```bash
   $ claudeconnect interactive peer@example.com
   # Launches Claude right here, no new window
   ```

3. Keep macOS-only but document clearly

**Recommendation:** Option 3 for now (keep simple), add TODO for cross-platform support

**Why:** Cross-platform terminal launching is complex, deserves its own PR

---

### 2. Prompt File Accumulation

**Problem:** Plan says "OS will clean /tmp/" but this isn't always true

**Reality:**
- macOS /tmp cleaned on reboot (could be months)
- Linux /tmp/claudeconnect-prompt-* files accumulate
- Not a huge issue (files are tiny) but messy

**Better approach:**
```python
# Use Python's tempfile with delete=False, but track for later cleanup
with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='cc-prompt-') as f:
    f.write(system_prompt)
    prompt_file = Path(f.name)

# Store path for potential cleanup (optional)
# Or just let OS handle it
```

**Or even better:**
```python
# Embed prompt directly in command using heredoc
terminal_cmd = f'''cd {peer_context_dir} && claude --system-prompt "$(cat <<'EOF'
{system_prompt}
EOF
)" "hi"
'''
```

**Recommendation:** Use heredoc approach (no temp file needed!)

---

### 3. Missing: Claude Code Installation Check

**Problem:** Plan mentions adding `which claude` check but doesn't include it in code

**Impact:** User gets cryptic Terminal error instead of clear message

**Fix:**
```python
# After imports
if not shutil.which("claude"):
    return False, "Claude Code (claude command) not found. Please install from https://claude.ai/download"
```

**Severity:** Medium - should definitely add this

---

### 4. No Feedback During Sync

**Problem:** User exits session, waits 90 seconds... nothing visible happens

**User experience:**
```
User: *exits Claude session*
User: *waits 90 seconds*
User: "Did it work? Is it syncing? Should I check?"
```

**Potential solutions:**
1. Add verbose sync output (but we made sync silent!)
2. Add notification when transcript imported (macOS: osascript notification)
3. Add `claudeconnect status` to show recent imports
4. Just document it clearly in UI message

**Recommendation:** Option 4 - keep sync silent, UI message already explains

**Why:** Adding notifications is scope creep, UI message is sufficient

---

### 5. Return Value Inconsistency

**Problem:** Function used to return session_id, now returns message

**Current callers:** Let me check...

**Impact:** Need to verify no code depends on the return value format

**Check needed:**
```bash
grep -r "run_interactive_session" src/ --include="*.py"
```

If called from CLI only, this is fine. If called programmatically elsewhere, might break.

---

### 6. MISSING: What if peer context dir doesn't exist after pull?

**Problem:** `pull_peer_context_http()` returns path, but what if directory is empty or has issues?

**Current code:**
```python
peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
if not peer_context_dir:
    return False, "Failed to pull context"
```

**Issue:** `pull_peer_context_http` might return a path even if no files downloaded

**Better:**
```python
peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
if not peer_context_dir:
    return False, "Failed to pull context"

# Verify directory exists and is accessible
if not peer_context_dir.exists() or not peer_context_dir.is_dir():
    return False, f"Peer context directory not found: {peer_context_dir}"
```

**Severity:** Low - `pull_peer_context_http` likely handles this, but worth verifying

---

### 7. Escaped Command Complexity

**Problem:** Escaping for AppleScript is fragile

**Current approach:**
```python
escaped_cmd = terminal_cmd.replace("\\", "\\\\").replace('"', '\\"')
```

**Issues:**
- Doesn't handle all edge cases (what if prompt contains backticks?)
- Hard to debug when it breaks

**Better approach:** Use raw strings and proper quoting

**Or:** Use osascript with stdin instead of -e:
```python
osascript_code = '''
tell application "Terminal"
    do script "cd ... && claude ..."
    activate
end tell
'''

subprocess.run(["osascript"], input=osascript_code, text=True)
```

**Recommendation:** Current approach probably fine, but worth testing with complex prompts

---

## What's Missing 🤔

### 1. User Confirmation Mechanism

**Scenario:** User accidentally runs `claudeconnect interactive peer@example.com` for wrong peer

**Current:** Terminal window opens immediately

**Better:** Show preview and confirm?
```
About to start interactive session with peer@example.com

Peer context: ~/.claude-connect/peers/peer-example-com
Files available: 42 files (last updated: 2 mins ago)

Continue? [Y/n]:
```

**Recommendation:** Skip for now - too much friction for common use case

---

### 2. Concurrent Session Handling

**Scenario:** User starts session with alice@example.com, then (before exiting) starts another with bob@example.com

**Question:** Will both work correctly?

**Answer:** Should be fine:
- Each Claude instance runs in separate terminal
- Each gets unique UUID from Claude Code
- Each saves to different project directory
- Discovery/import handles multiple sessions

**Verification needed:** Test manually

---

### 3. Session Cancellation

**Scenario:** User opens terminal, realizes they picked wrong peer, closes window immediately

**Question:** Does this create a broken/empty JSONL?

**Answer:** Probably not - Claude Code likely only saves when conversation starts

**Verification needed:** Test this edge case

---

### 4. Network Failure During Pull

**Scenario:** `pull_peer_context_http()` partially fails - downloads some files but not all

**Question:** Will session work with incomplete context?

**Answer:** Yes, Claude will work with whatever files are present

**Better:** Add warning if pull had errors?

**Recommendation:** Out of scope - pull function should handle this

---

## Critical Design Questions

### Q1: Should we require `claude` to be installed?

**Current plan:** Yes, add `which claude` check

**Alternative:** Provide fallback or better error message

**Decision:** ✅ Add check (good UX)

---

### Q2: Should we keep TRANSCRIPTS_DIR for anything?

**Current plan:** No, completely unused

**Alternative:** Store prompt files there instead of /tmp?

**Decision:** ✅ No, remove it entirely (simplify)

**But:** Check if TRANSCRIPTS_DIR is referenced elsewhere in codebase!

---

### Q3: Should we add a `--wait` flag to wait for sync?

**Example:**
```bash
$ claudeconnect interactive peer@example.com --wait
# ... user has session ...
# ... user exits ...
⏳ Waiting for sync...
✓ Transcript synced to both repos!
```

**Pros:** Better UX, user knows when it's done

**Cons:** Adds complexity, requires polling ~/.claude/projects/

**Decision:** ⏸️ Skip for now - nice to have, not MVP

---

### Q4: What if user doesn't have write permission to peer's repo?

**Scenario:** Peer gave us read-only access

**Current behavior:** Upload to peer repo silently fails

**Better:** Warn user during session start?
```
⚠ Note: You have read-only access to peer's repo.
  Transcript will be saved locally but not uploaded to their repo.
```

**Challenge:** Would need to check permissions before session starts

**Decision:** ⏸️ Skip for now - rare case, silent failure is OK

---

## Recommended Changes to Plan

### 1. Add Claude Installation Check
```python
# Add after platform check
if not shutil.which("claude"):
    return False, "Claude Code not found. Install from https://claude.ai/download"
```

### 2. Use Heredoc for System Prompt (Avoid Temp File)
```python
# Instead of writing to temp file:
terminal_cmd = f"""cd {peer_context_dir} && claude --system-prompt "$(cat <<'EOF'
{system_prompt}
EOF
)" "hi" """
```

### 3. Remove TRANSCRIPTS_DIR Constant (If Unused Elsewhere)
```python
# Remove this line from session.py:
TRANSCRIPTS_DIR = Path.home() / ".claude-connect" / "transcripts"
```

### 4. Add Peer Context Directory Validation
```python
if not peer_context_dir.exists():
    return False, f"Peer context directory not found: {peer_context_dir}"
```

### 5. Update Return Value Comment
```python
Returns:
    Tuple of (success: bool, message: str)
    # Note: Previously returned session_id, now returns confirmation message
```

---

## Testing Checklist

Before merging, verify:

- [ ] `which claude` check works (test without Claude installed)
- [ ] Terminal opens with correct working directory
- [ ] System prompt is passed correctly
- [ ] Conversation appears in `~/.claude/projects/`
- [ ] Transcript imported to local context within 90s
- [ ] Transcript uploaded to our repo
- [ ] Transcript uploaded to peer's repo
- [ ] Multiple concurrent sessions work
- [ ] Empty session (exit immediately) doesn't break
- [ ] Long session (100+ exchanges) works
- [ ] Special characters in prompt don't break escaping
- [ ] Graceful error when peer not found
- [ ] Graceful error when not logged in

---

## Bottom Line

**The plan is 90% solid.** Main improvements needed:

1. ✅ Add `claude` installation check
2. ✅ Consider heredoc for prompt (avoid temp file)
3. ⚠️ Verify TRANSCRIPTS_DIR removal safe
4. ⚠️ Test escaping with complex prompts
5. ℹ️ Document that it's macOS-only (for now)

**Risk level:** Low
- Mostly removing code (safer than adding)
- Core logic (transcript discovery/import) already implemented
- Easy to test manually

**Confidence:** High that this will work as designed
