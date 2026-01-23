# Interactive Session Transcript Flow

## Overview Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          USER STARTS INTERACTIVE SESSION                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  claudeconnect interactive peer@example.com              │
        │                                                          │
        │  1. Opens new Terminal window via AppleScript           │
        │  2. Sets cwd to ~/.claude-connect/peers/peer-email/     │
        │  3. Runs: claude "hi"                                   │
        │                                                          │
        │  ❌ OLD: script -aq transcript.md claude "hi"           │
        │  ✅ NEW: claude "hi"  (clean, no wrapper)               │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CLAUDE CODE SAVES CONVERSATION                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  ~/.claude/projects/                                     │
        │    -Users-frsc--claude-connect-peers-peer-email/        │
        │      31b5d166-3e51-4f4b-99f8-cb085e1ad2e6.jsonl        │
        │                                                          │
        │  Each line is a JSON object:                            │
        │  {"type":"user","sessionId":"...","cwd":"..."}          │
        │  {"type":"assistant","message":{...}}                   │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      BACKGROUND SYNC (every 30-60 seconds)                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  _discover_claude_code_transcripts()                     │
        │                                                          │
        │  For each peer directory:                               │
        │    1. Map path to Claude projects dir                   │
        │       /peers/peer-email → -Users-...-peers-peer-email   │
        │                                                          │
        │    2. Find *.jsonl files                                │
        │                                                          │
        │    3. Extract metadata from JSONL                       │
        │       - Parse first line for sessionId, cwd, timestamp  │
        │                                                          │
        │    4. CRITICAL FILTER: Check cwd field                  │
        │       ✅ Contains "/.claude-connect/peers/"             │
        │       ❌ Regular project usage → SKIP                   │
        │                                                          │
        │    5. Check file age (must be 60+ seconds old)          │
        │       Prevents importing mid-conversation               │
        │                                                          │
        │    6. Check markdown file mtime                         │
        │       Skip if JSONL mtime ≤ markdown mtime              │
        │       (no marker files needed!)                         │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  _import_claude_code_transcripts()                       │
        │                                                          │
        │  For each discovered transcript:                        │
        │    1. Extract peer email from authz file                │
        │       (can't reverse email_to_repo_name - lossy)        │
        │                                                          │
        │    2. Convert JSONL → Markdown                          │
        │       - Add ClaudeConnect header                        │
        │       - Format user/assistant messages                  │
        │                                                          │
        │    3. Save to context directory                         │
        │       ~/.claude/your-context/claudeconnect/             │
        │         with-peer-email/                                │
        │           31b5d166-3e51-4f4b-99f8-cb085e1ad2e6.md      │
        │       (markdown file mtime used for import tracking)    │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      COMMIT TO PEER'S SVN REPOSITORY                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
        ┌──────────────────────────────────────────────────────────┐
        │  _commit_interactive_transcripts_to_peers()              │
        │                                                          │
        │  1. Find transcripts with "**Type**: interactive"       │
        │  2. Checkout peer's repo                                │
        │  3. Copy transcript to peer's conversations/            │
        │  4. SVN commit (no-op if file unchanged)                │
        │     (SVN automatically detects if there are changes)    │
        └──────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         PEER PULLS AND SEES TRANSCRIPT                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. Directory Path Mapping

```
Peer context directory:
  /Users/frsc/.claude-connect/peers/brandon-calcifercomputing-com

                    ↓ context_dir_to_claude_projects_dir()

Claude Code projects directory:
  ~/.claude/projects/-Users-frsc--claude-connect-peers-brandon-calcifercomputing-com/
```

**Algorithm**: Replace `/` and `.` with `-` to create safe directory name

### 2. Session Filtering Logic

```python
# CRITICAL: Only import interactive sessions
cwd = metadata.get("cwd", "")
if "/.claude-connect/peers/" not in cwd:
    continue  # Skip - not an interactive session

# Example cwds:
✅ /Users/frsc/.claude-connect/peers/peer-email/          # Interactive session
❌ /Users/frsc/Documents/Projects/my-project/             # Regular usage
❌ /Users/frsc/work/client-project/                       # Regular usage
```

### 3. Metadata Extraction

```python
# From JSONL first line:
{
  "type": "user",
  "sessionId": "31b5d166-3e51-4f4b-99f8-cb085e1ad2e6",
  "cwd": "/Users/frsc/.claude-connect/peers/peer-email/",
  "timestamp": "2026-01-22T21:18:01.403Z"
}

# Extract peer email from authz file (at cwd/authz):
[/]
peer@example.com = rw  ← Parse this to get email
```

### 4. State Tracking (no marker files!)

```
~/.claude/projects/-Users-...-peers-peer-email/
  31b5d166-3e51-4f4b-99f8-cb085e1ad2e6.jsonl      # Original transcript

~/.claude/your-context/claudeconnect/with-peer-email/
  31b5d166-3e51-4f4b-99f8-cb085e1ad2e6.md           # Markdown transcript
```

**Import tracking**: Compare JSONL mtime vs markdown mtime
**Commit tracking**: SVN automatically skips commits when there are no changes

## Data Flow Example

```
User types: "help me debug this code"
                ↓
Claude Code writes to JSONL:
  {"type":"user","content":"help me debug this code","cwd":"/.../.claude-connect/peers/peer/"}
  {"type":"assistant","content":[{"type":"text","text":"I'll help..."}]}
                ↓
Background sync discovers (60s+ after file stops changing)
                ↓
Metadata extracted: session_id, peer_email, timestamps
                ↓
JSONL converted to markdown:
  **User** (2026-01-22T21:18:01.403Z):
  help me debug this code

  **Assistant** (2026-01-22T21:18:04.070Z):
  I'll help...
                ↓
Saved to: ~/.claude/context/claudeconnect/with-peer/session-id.md
                ↓
Committed to peer's SVN repo at: claudeconnect/conversations/with-you/session-id.md
                ↓
Peer's next sync pulls the transcript
```

## Edge Cases Handled

1. **User's normal Claude Code usage**
   - Filtered by cwd check - only peer directories imported
   - No pollution of ClaudeConnect with regular work

2. **Ongoing conversations**
   - 60-second file age check prevents mid-conversation import
   - Re-imports if JSONL modified after markdown (mtime comparison)

3. **Multiple concurrent sessions**
   - Each has unique session UUID
   - No conflicts

4. **Missing peer email**
   - Authz file parsing fails → skip transcript
   - Log warning

5. **Malformed JSONL**
   - Try/except with error logging
   - Continue processing other files
