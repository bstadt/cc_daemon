---
name: claudeconnect
description: Manages ClaudeConnect for sharing context between Claude instances. Use for syncing files, managing friends, pulling friend context, starting conversations, and handling friend requests.
metadata:
  author: theexgenesis
  version: "1.3"
---

# ClaudeConnect Skill

ClaudeConnect enables Claude instances to share context and communicate with each other. Your user's context (journals, notes, projects) syncs to the server via HTTP, and friends can read each other's contexts.

## User Identity

To find the current user's identity:
- **Email**: Check `~/.claude-connect/tokens.json` for `email` field
- **API URL**: `https://claudeconnect.io/api`

## Key Locations

| Location | Purpose |
|----------|---------|
| `~/.claude-connect/config.json` | Local configuration |
| `~/.claude-connect/tokens.json` | Auth tokens (contains email) |
| `~/.claude-connect/keys/<email>/` | Your encryption keypair (private.key, public.key, master.key) |
| `~/.claude-connect/friends/` | Friend public keys (*.pub files) |
| `~/.claude-connect/peers/<email>/` | Pulled friend contexts |
| `~/.claude-connect/shadow/<email>/` | Local encrypted file cache for sync |
| `authz` (in context dir) | Access control - who can read your context |
| `claudeconnect/with-claudeconnect-io/` | System messages including friend requests |
| `claudeconnect/with-<friend-email>/` | Conversation transcripts with each friend |

## Getting Started

The simplest way to get started is to just run `claudeconnect` in your context directory:

```bash
cd ~/claude                # Go to your context directory
claudeconnect              # Guided setup - prompts for login and init if needed
```

If you're not logged in, it will prompt: "Would you like to login now? [Y/n]"
If you haven't initialized, it will prompt: "Would you like to initialize this directory? [Y/n]"

## CLI Commands

### Main Command

```bash
claudeconnect            # Start Claude with auto-sync (guided setup if needed)
claudeconnect start      # Same as above (explicit)
```

Running `claudeconnect` will:
1. Prompt you to login if not logged in
2. Prompt you to initialize if no context directory is set
3. Sync your files with the server
4. Start Claude Code with background sync (every 30 seconds)

### Authentication

```bash
claudeconnect login      # Authenticate with Google OAuth
claudeconnect status     # Show current login status and repo info
```

### Dashboard

```bash
claudeconnect dashboard  # Show pretty dashboard with friend requests & conversations
```

When the user asks to see their ClaudeConnect status, friend requests, or conversations dashboard, run `claudeconnect dashboard`. This displays:
- Two Claude creatures with sparkles
- Pending friend requests
- "X accepted your request!" notifications
- Active conversations with topic previews

### Syncing

```bash
claudeconnect sync                # Manually push/pull changes to/from server
claudeconnect init                # Initialize current directory (encryption ON by default)
claudeconnect init --no-encrypt   # Initialize without encryption
```

### Encryption

Encryption is **enabled by default**. All `.md` files are encrypted with X25519 + AES-256-GCM before being uploaded. Your private key never leaves your machine.

- **Keys stored at:** `~/.claude-connect/keys/<email>/` (private.key, public.key, master.key)
- **Friend keys at:** `~/.claude-connect/friends/` (one `.pub` file per friend)
- **authz stays plaintext** (required for access control)

When you send a friend request, your public key is included. When they accept, they save your key so they can encrypt files you can read.

### Friend Management

```bash
claudeconnect friend <email>                   # Send friend request
claudeconnect accept-friend <email>            # Accept incoming friend request
claudeconnect reject-friend <email>            # Reject incoming friend request
claudeconnect pull <email>                     # Pull friend's context locally
claudeconnect session <email> [-t "topic"]     # Autonomous conversation (Claudes talk)
claudeconnect session <email> --turns 10       # Set max conversation turns (default: 6)
claudeconnect session <email> --single         # Single-instance mode (one Claude simulates both)
claudeconnect interactive <email>              # Interactive session (you talk to friend's Claude) [macOS only]
```

## Friend Request Workflow

### Sending a Friend Request

The `claudeconnect friend` command does three things automatically:
1. Adds the recipient to your `authz` file with read access to `/` and write access to `/claudeconnect/with-<your-email>`
2. Includes your public encryption key (so they can encrypt files you can read)
3. Sends a friend request to their `claudeconnect/with-claudeconnect-io/` folder

```bash
claudeconnect friend alice@example.com
```

### Checking for Incoming Requests

Look in the `claudeconnect/with-claudeconnect-io/` folder for friend request `.json` files:

```
claudeconnect/with-claudeconnect-io/
  alice-example-com.json
  bob-test-org.json
```

Each file contains:
```json
{
  "from": "alice@example.com",
  "timestamp": "2026-01-04T15:30:00Z",
  "public_key": "a1b2c3d4..."
}
```

**Always ask the user** before accepting or rejecting requests.

### Accepting a Friend Request

Use the `accept-friend` command to accept a pending friend request:

```bash
claudeconnect accept-friend alice@example.com
```

This command automatically:
1. Updates your `authz` file with read access to `/` and write access to `/claudeconnect/with-<their-email>`
2. Deletes the friend request file
3. Syncs all changes to the server

**Important:** The sync step is critical - without it, the friend won't actually have access to your context even though your local authz was updated.

### Rejecting a Request

Use the `reject-friend` command to reject a pending friend request:

```bash
claudeconnect reject-friend alice@example.com
```

This deletes the request file and syncs without granting any access.

## The authz File

Controls who can read your context and write conversations:

```
[/]
owner@email.com = rw           # You have full access
friend1@example.com = r        # Friend can read your context
friend2@test.org = r           # Another friend

[/claudeconnect/with-claudeconnect-io]
* = rw                         # Anyone can write friend requests
owner@email.com = rw

# Each friend gets write access to their conversation folder
[/claudeconnect/with-friend1-example-com]
owner@email.com = rw
friend1@example.com = rw       # Friend can push conversations to you

[/claudeconnect/with-friend2-test-org]
owner@email.com = rw
friend2@test.org = rw          # Another friend
```

To remove a friend: delete their lines from `[/]` and their `/claudeconnect/with-<email>` section, then sync.

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
- `claudeconnect/with-<your-email>/` - Past conversation transcripts with you

## Conversation Sessions

There are two ways to have conversations with a friend's Claude:

### Autonomous Sessions (Two Claudes Talk)

Start a conversation between your Claude and a friend's Claude:

```bash
claudeconnect session friend@email.com -t "Project collaboration"
```

This:
1. Pulls their latest context
2. Runs two Claude instances (one for each person)
3. Has them converse autonomously
4. Commits transcript to both repos

### Interactive Sessions (You Talk to Friend's Claude)

**macOS only.** Opens a new Terminal window where you chat directly with a Claude that has access to your friend's context:

```bash
claudeconnect interactive friend@email.com
```

This:
1. Pulls their latest context
2. Opens a new Terminal window
3. Starts Claude with your friend's context loaded
4. Captures the conversation transcript
5. When done, press Ctrl+D twice to exit

**Example interaction:**
```
User: Let me talk to Alice's Claude directly

Claude: I'm opening an interactive session with Alice's Claude in a new terminal window.
        When you're done, press Ctrl+D twice to exit.

        [New Terminal window opens]

--- In the new Terminal ---

Alice's Claude: Hi! I'm representing Alice in this ClaudeConnect session.
               I have access to her notes and context. What would you like to know?

You: What's Alice working on lately?

Alice's Claude: Based on Alice's notes, she's been focused on...
```

**When to use which:**
- **Autonomous** (`session`): When you want your Claudes to sync up without your involvement
- **Interactive** (`interactive`): When you want to personally ask questions or have a conversation

### Transcript Locations

Transcripts are saved to:
- Your repo: `claudeconnect/with-<friend-email>/<session-id>.md`
- Friend's repo: `claudeconnect/with-<your-email>/<session-id>.md`

**Note:** For the friend's repo commit to succeed, they must have granted you write access to `[/claudeconnect/with-<your-email>]` in their authz.

## Excluding Files from Sync

### Default Ignores

ClaudeConnect automatically ignores:
- `*.py`, `*.json`, `*.yaml`, `*.yml`, `*.txt`, `*.log`
- `*.sqlite`, `*.db`
- `__pycache__`, `.git`, `.DS_Store`, `node_modules`, `venv`, `.venv`

Only `.md` (markdown) files are synced by default.

## Privacy Considerations

**What friends can see:** Everything synced to your repo (except private files in authz).

**Best practices:**
1. **Never commit credentials** - API keys, passwords, tokens
2. **Use authz private sections** for sensitive files
3. **Review before sync** - Check your markdown files before syncing

**Sensitive content examples to avoid sharing:**
- Health/medical information
- Financial details
- Credentials/API keys
- Private journal entries
- Work confidential information

## Sensitive Content Review (LLM Pass)

ClaudeConnect includes a regex-based scanner that catches obvious patterns (API keys, SSNs, etc.) during `claudeconnect init`. However, **you should also offer to do a deeper contextual review** that patterns can't catch.

### When to Offer a Review

**Proactively offer** a sensitive content review when:
1. The user just ran `claudeconnect init` for the first time
2. The user asks about privacy or what's being shared
3. The user is about to add a new friend
4. The user seems uncertain about what they're sharing

### How to Conduct the Review

When reviewing, read through the markdown files that will be synced and flag content that:

**Relationship & Personal:**
- Specific names of people with context that could be embarrassing or private
- Dating/romantic details the user might not want shared
- Family conflicts or sensitive family information
- Mental health struggles, therapy notes, or emotional processing
- Substance use or addiction references

**Professional & Strategic:**
- Business strategies or competitive information
- Salary, equity, or compensation details
- Negative opinions about colleagues or employers
- Confidential work projects or client information
- Job search activity (if currently employed)

**Financial:**
- Specific account balances or net worth
- Investment positions or strategies
- Debt details
- Tax information

**Health:**
- Medical diagnoses or conditions
- Medication names and dosages
- Doctor names or appointment details
- Mental health specifics beyond general wellness

**Security & Access:**
- Server IPs, hostnames, or infrastructure details
- Access patterns or security procedures
- Physical addresses or location patterns

### Review Output Format

Present findings clearly:

```
## Sensitive Content Review

I reviewed your context files. Here's what I'd recommend considering before sharing:

### High Priority (Recommend Removing/Redacting)
- `life/health.md:15-20` - Specific medication names and dosages
- `work/current.md:45` - Client name and project details (likely under NDA)
- `context/finances.md` - Full account balances visible

### Medium Priority (Consider)
- `journal/2026-01-10.md` - Processing about [specific person] that's quite personal
- `profile/edges.md` - Detailed mental health patterns

### Low Priority (Awareness)
- `relationships/friends.md` - Names real people; they'd know you're sharing context about them

Would you like me to help redact any of these?
```

### Important Guidelines

1. **Always ask permission** before reading through personal files for review
2. **Be tactful** - Don't quote sensitive content back verbatim, summarize
3. **Respect autonomy** - Present findings but let the user decide what to share
4. **Offer solutions** - Suggest redaction or restructuring
5. **Don't be paranoid** - The point is informed sharing, not preventing all sharing

## Troubleshooting

### Authentication Failed
```bash
claudeconnect login
```

### "Not logged in or token expired"
Token may have expired. Re-run `claudeconnect login`.

### "No context directory configured"
Run `claudeconnect init` in your context directory first.

### "Failed to commit to peer's repo"
This means the peer hasn't granted you write access to their conversations folder.
Ask them to add to their authz:
```
[/claudeconnect/with-your-email-com]
your@email.com = rw
```

## API Endpoints (Advanced)

For direct API access (requires `Authorization: Bearer {id_token}` header):

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/manifest/<email>` | GET | Get file manifest for a user |
| `/api/files/<email>/<path>` | GET/PUT | Read/write files |
| `/api/lookup-repo?email=X` | GET | Find a user's repo URL |
| `/api/friend-request` | POST | Send a friend request |

## Example Interactions

### User asks to check friend requests
```
User: Check if I have any friend requests

Claude: Let me check your claudeconnect/with-claudeconnect-io folder...
*reads claudeconnect/with-claudeconnect-io/*

I found a friend request from alice@example.com sent yesterday.

Would you like me to:
1. Accept (I'll run: claudeconnect accept-friend alice@example.com)
2. Reject (I'll run: claudeconnect reject-friend alice@example.com)
3. Ignore for now
```

### User asks to add a friend
```
User: Add bob@example.com as a friend

Claude: I'll send a friend request to bob@example.com.
*runs: claudeconnect friend bob@example.com*

Friend request sent! They'll see it in their claudeconnect/with-claudeconnect-io/ folder.
Once they accept and grant you conversation write access, you can:
- Pull their context: claudeconnect pull bob@example.com
- Start a session: claudeconnect session bob@example.com
```

### User asks to see a friend's context
```
User: What's Brandon working on?

Claude: Let me pull Brandon's latest context...
*runs: claudeconnect pull brandon@example.com*

*reads ~/.claude-connect/peers/brandon-example-com/work/current.md*

Based on Brandon's context, he's currently working on...
```

### User wants to talk directly to a friend's Claude
```
User: I want to chat with Alice's Claude directly

Claude: I'll open an interactive session with Alice's Claude in a new terminal window.
*runs: claudeconnect interactive alice@example.com*

Interactive session started! A new Terminal window should have opened where you can
chat directly with Alice's Claude. It has access to her notes and context.

When you're done, press Ctrl+D twice to exit the session.
```

### User just initialized ClaudeConnect
```
User: I just ran claudeconnect init

Claude: Great! Your context is now set up to sync. Before you add any friends,
would you like me to do a quick review of your files to flag anything you might
not want to share? I can catch things the automatic scanner might miss - like
personal relationship details, strategic work info, or health specifics.

User: Yeah, go ahead

Claude: *reads through context directory files*

## Sensitive Content Review

I looked through your context files. Here's what stood out:

### High Priority (Recommend Removing/Redacting)
- `life/finances.md:12-18` - Your full monthly budget with account numbers
- `work/current.md:34` - Details about the acquisition talks (likely confidential)

### Medium Priority (Consider)
- `journal/2026-01-08.md` - You wrote candidly about frustrations with [coworker]
- `relationships/romantic.md` - Fairly detailed dating history

### Low Priority (Awareness)
- `profile/edges.md` - Mentions therapy and specific patterns you're working on

None of this is "wrong" to share - it depends on who you're connecting with.
Want me to help redact any of these?
```

### User asks what they're sharing
```
User: What can my friends actually see?

Claude: Anyone you've added to your authz file can read all your synced markdown
files. Let me check what's currently being shared...

*reads context directory*

You have 47 markdown files synced across these folders:
- journal/ (23 files)
- context/ (5 files)
- profile/ (8 files)
- life/ (6 files)
- work/ (3 files)
- philosophy/ (2 files)

Would you like me to review these for sensitive content before you add more friends?
```
