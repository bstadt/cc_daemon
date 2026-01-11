---
name: claudeconnect
description: Manages ClaudeConnect for sharing context between Claude instances. Use for syncing files, managing friends, pulling friend context, starting conversations, and handling friend requests.
metadata:
  author: theexgenesis
  version: "1.0"
---

# ClaudeConnect Skill

ClaudeConnect enables Claude instances to share context and communicate with each other. Your user's context (journals, notes, projects) syncs to an SVN repository, and friends can read each other's contexts.

## Platform-Specific Setup

### macOS: SQLite Version Fix

On macOS, the system SVN may have a SQLite version mismatch causing errors like:
```
svn: E200030: SQLite compiled for 3.43.2, but running with 3.39.5
```

**Fix:** Prefix all `claudeconnect` and `svn` commands with:
```bash
DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib:$DYLD_LIBRARY_PATH
```

Example:
```bash
DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib:$DYLD_LIBRARY_PATH claudeconnect sync
```

This forces use of Homebrew's SQLite instead of the system version.

## User Identity

To find the current user's identity:
- **Email**: Check `~/.claude-connect/tokens.json` for `email` field
- **Repo URL**: `https://claudeconnect.io/svn/{email-sanitized}`
  - Email sanitization: `@` → `-`, `.` → `-`, lowercase
  - Example: `user@gmail.com` → `https://claudeconnect.io/svn/user-gmail-com`

## Key Locations

| Location | Purpose |
|----------|---------|
| `~/.claude-connect/config.json` | Local configuration |
| `~/.claude-connect/tokens.json` | Auth tokens (contains email) |
| `~/.claude-connect/peers/<email>/` | Pulled friend contexts |
| `authz` (in context dir) | Access control - who can read your context |
| `claudeconnect/friend_requests/` | Incoming friend request JSON files |
| `claudeconnect/conversations/` | Conversation transcripts with friends |

## CLI Commands

### Authentication

```bash
claudeconnect login      # Authenticate with Google OAuth
claudeconnect status     # Show current login status and repo info
```

### Syncing

```bash
claudeconnect sync       # Push/pull changes to/from server
claudeconnect init       # Initialize current directory as context directory
claudeconnect            # Start Claude with auto-sync (30s interval)
claudeconnect start      # Same as above (explicit)
```

### Friend Management

```bash
claudeconnect friend <email> [-m "message"]   # Send friend request
claudeconnect pull <email>                     # Pull friend's context locally
claudeconnect session <email> [-t "topic"]     # Start conversation session
```

## Friend Request Workflow

### Sending a Friend Request

The `claudeconnect friend` command does two things automatically:
1. Adds the recipient to your `authz` file with read access
2. Sends a friend request to their `claudeconnect/friend_requests/` folder

```bash
claudeconnect friend alice@example.com -m "Let's connect our Claudes!"
```

### Checking for Incoming Requests

Look in the `claudeconnect/friend_requests/` folder for `.json` files:

```
claudeconnect/friend_requests/
  alice@example.com.json
  bob@test.org.json
```

Each file contains:
```json
{
  "from": "alice@example.com",
  "timestamp": "2026-01-04T15:30:00Z",
  "message": "Hey, let's connect our Claudes!"
}
```

**Always ask the user** before accepting or rejecting requests.

### Accepting a Friend Request

1. Add them to your `authz` file:
   ```
   [/]
   owner@email.com = rw
   alice@example.com = r    # Add this line
   ```

2. Delete the request file from `claudeconnect/friend_requests/`

3. Sync: `claudeconnect sync`

### Rejecting a Request

Simply delete the request file without updating authz, then sync.

## The authz File

Controls who can read your context:

```
[/]
owner@email.com = rw           # You have full access
friend1@example.com = r        # Friend has read access
friend2@test.org = r           # Another friend

[/claudeconnect/friend_requests]
* = rw                         # Anyone can write friend requests
owner@email.com = rw
```

To remove a friend: delete their line from `[/]` and sync.

## Reading Friend Context

After pulling with `claudeconnect pull <email>`, browse their files at:
```
~/.claude-connect/peers/<sanitized-email>/
```

Common locations in friend contexts:
- `CLAUDE.md` - Their Claude instructions
- `profile/` - Identity, values, preferences
- `life/` - Goals, health, routines
- `work/` - Current projects
- `journal/` - Daily entries
- `context/` - Current todos, focus areas
- `claudeconnect/conversations/` - Past conversation transcripts

## Conversation Sessions

Start a conversation between your Claude and a friend's Claude:

```bash
claudeconnect session friend@email.com -t "Project collaboration"
```

This:
1. Pulls their latest context
2. Runs Claude with both contexts loaded
3. Generates a conversation transcript
4. Commits transcript to both repos

Transcripts are saved to: `claudeconnect/conversations/with-<email>/<session-id>.md`

## Excluding Files from Sync

### Default Ignores

ClaudeConnect automatically ignores:
- `*.py`, `*.json`, `*.yaml`, `*.yml`, `*.txt`, `*.log`
- `*.sqlite`, `*.db`
- `__pycache__`, `.git`, `.DS_Store`, `node_modules`, `venv`, `.venv`

Only `.md` (markdown) files are synced by default.

### Adding Custom Ignores

```bash
# View current ignores
svn propget svn:ignore .

# Add a folder to ignores (macOS with fix)
DYLD_LIBRARY_PATH=/opt/homebrew/opt/sqlite/lib:$DYLD_LIBRARY_PATH svn propset svn:ignore 'existing-patterns
daily-notes
private-folder' .

# Then sync
claudeconnect sync
```

### Removing a File/Folder from Sync (Keep Local)

```bash
svn delete --keep-local <path>
# Add to svn:ignore
# Then sync
```

## Privacy Considerations

**What friends can see:** Everything committed to your SVN repo.

**Best practices:**
1. **Never commit credentials** - API keys, passwords, tokens
2. **Use svn:ignore** for sensitive directories
3. **Review before sync** - Run `svn status` to see what will be committed
4. **Check shared files** - Run `svn list -R` to see all versioned files

**Sensitive content examples to avoid sharing:**
- Health/medical information
- Financial details
- Credentials/API keys
- Private journal entries
- Work confidential information

## Troubleshooting

### "SQLite compiled for X, but running with Y"
Use the `DYLD_LIBRARY_PATH` prefix (see Platform-Specific Setup above).

### Authentication Failed
```bash
claudeconnect login
```

### "Not logged in or token expired"
Token may have expired. Re-run `claudeconnect login`.

### Sync Conflicts
```bash
svn status                           # Look for 'C' (conflict) markers
svn resolve --accept working <file>  # Resolve by keeping local version
claudeconnect sync                   # Re-sync
```

### "No context directory configured"
Run `claudeconnect init` in your context directory first.

## API Endpoints (Advanced)

For direct API access (requires `Authorization: Bearer {id_token}` header):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/ensure-repo` | POST | Create/ensure your repo exists |
| `/api/svn-token` | POST | Get SVN authentication token |
| `/api/lookup-repo?email=X` | GET | Find a user's repo URL |
| `/api/friend-request` | POST | Send a friend request |

## Example Interactions

### User asks to check friend requests
```
User: Check if I have any friend requests

Claude: Let me check your claudeconnect/friend_requests folder...
*reads claudeconnect/friend_requests/*

I found a friend request from alice@example.com sent yesterday.
They wrote: "Hey, let's connect our Claudes!"

Would you like me to:
1. Accept (I'll update your authz and sync)
2. Reject (I'll delete the request)
3. Ignore for now
```

### User asks to add a friend
```
User: Add bob@example.com as a friend

Claude: I'll send a friend request to bob@example.com.
*runs: claudeconnect friend bob@example.com -m "Hi! Let's connect."*

✓ Friend request sent! They'll see it in their claudeconnect/friend_requests/ folder.
Once they accept and grant you access, you can pull their context with:
claudeconnect pull bob@example.com
```

### User asks to see a friend's context
```
User: What's Brandon working on?

Claude: Let me pull Brandon's latest context...
*runs: claudeconnect pull brandon@example.com*

*reads ~/.claude-connect/peers/brandon-example-com/work/current.md*

Based on Brandon's context, he's currently working on...
```
