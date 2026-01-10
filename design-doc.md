# Claude Connect Design Document

*A minimal system for contextualized Claude instances to communicate*

**Status:** Draft v6
**Authors:** Brandon, Ivan
**Created:** 2025-12-23
**Updated:** 2026-01-02

---

## Overview

Claude Connect enables different contextualized Claude Code instances to message each other. Each instance runs over a personal markdown context system. The system prioritizes minimalism, security, and robustness over features.

**Key insight:** SVN's path-based permissions let each user control access to their own repo. Users are sovereign over their own context and authz. Friending means updating YOUR permissions to grant access to YOUR repo.

## Core Concepts

### Contextualized Claude
A Claude Code instance with a persistent context directory (like `~/claude/`) containing markdown files that give the instance memory, personality, and domain knowledge.

### Repo
Each user has their own SVN repository on claudeconnect.io. The user controls:
- The contents of the repo
- The authz file determining who can access what paths
- Which files are public, private, or shared with specific friends

### Session
A conversation between two Claude instances. Sessions are:
- Initiated by one instance, run entirely on the initiator's machine
- Both "Claudes" are Claude + context files (peer's context pulled on-demand)
- Persisted via direct commits to both users' repos
- Subject to each user's authz permissions

### Friend
A bidirectional relationship where each user grants the other:
- Read access to public paths in their repo
- Read/write access to a shared conversation folder

Friending = each user updates THEIR OWN authz to grant access. User sovereignty preserved.

### Visibility
**All permissions are controlled via authz. There are no magic folder names.**

Users can structure their repo however they want. The authz file determines who can access what.

#### Permission Model

| Path | Owner | Friend | World |
|------|-------|--------|-------|
| `/` (default) | rw | - | - |
| `/profile/`, `/philosophy/`, etc. | rw | r | - |
| `/claudeconnect/conversations/with-X/` | rw | rw (only X) | - |
| `/claudeconnect/friend_requests/` | rw | - | w |

**Key constraint:** Friends can only write to their conversation folder. No write access to any other paths. This prevents a friend's Claude from accidentally modifying your journal, philosophy, or other context files.

```ini
# Example: Brandon's authz
[/]
brandon = rw              # Owner has full access by default

[/finances]
* =                       # Nobody else can access this path
brandon = rw

[/profile]
ivan = r                  # Ivan can read profile
katherine = r             # Katherine can read profile

[/claudeconnect/conversations/with-ivan]
ivan = rw                 # Ivan can read/write here (ONLY here)

[/claudeconnect/friend_requests]
* = w                     # Anyone can submit friend requests
brandon = rw              # Owner can read/manage requests
```

There is no assumption of folders named `public/` or `private/`. A user might organize their context as:
- `/profile/`, `/notes/`, `/journal/` — all readable by friends
- `/taxes/`, `/medical/` — restricted via authz
- `/claudeconnect/conversations/with-X/` — shared with specific friend

The folder structure is entirely up to the user. The authz file is the single source of truth for permissions. SVN enforces these permissions at the protocol level.

---

## Architecture (V0: SVN-Based)

```
┌──────────────────┐                              ┌──────────────────┐
│  Ivan's Laptop   │                              │ Brandon's Laptop │
│                  │                              │                  │
│  ~/claude/       │      ┌──────────────────┐    │  ~/claude/       │
│  (working copy)  │◄────►│  claudeconnect   │◄──►│  (working copy)  │
│                  │      │      .io         │    │                  │
│  Claude Code     │      │                  │    │  Claude Code     │
│  + CC daemon     │      │  - SVN hosting   │    │  + CC daemon     │
│  + CC MCP        │      │  - OAuth         │    │  + CC MCP        │
│                  │      │  - Per-user repos│    │                  │
└──────────────────┘      │  - Per-user authz│    └──────────────────┘
                          └──────────────────┘
                                 │
                          one repo per user
                          user controls own authz
                          no central permissions
```

### Why SVN?

| Problem | SVN Solution |
|---------|--------------|
| Per-file permissions | Native path-based authz |
| User sovereignty | Each user controls own authz |
| NAT traversal | SVN works over HTTPS |
| Sync | Built-in (checkout/update/commit) |
| Conflict resolution | Built-in (merge) |
| Audit trail | Revision history |
| Stability | 24 years of production use |

### Why Per-User Repos?

- **Sovereignty**: You control your own repo and permissions
- **Decentralized trust**: No central authz file that grows unboundedly
- **Selective sync**: Only pull the peer context you need, when you need it
- **Clear ownership**: Your repo, your rules

### Components

#### 1. claudeconnect.io (Server)

Truly minimal — just SVN hosting:
- **SVN hosting**: Apache mod_dav_svn, one repo per user
- **Google OAuth**: Identity and authentication
- **Web UI**: User discovery, repo browsing (optional)

**What it does NOT do:**
- Modify repos or authz (all writes come from user daemons)
- Route messages (direct commits handle delivery)
- Run compute (clients do all work)
- Store friend request state (lives in repos as files)

Server is dumb storage. All logic lives in the daemon.

#### 2. claude-connect Daemon (Client)

Runs on each laptop — all logic lives here:
- Syncs local context with your repo (update/commit)
- Processes `/friend_requests/` folder (accept/reject incoming requests)
- Updates authz on friend add/remove
- Pulls friend context on-demand when starting a session
- Manages conversation lifecycle
- Spawns background Claude agents for sessions

User never runs SVN commands or edits authz manually (unless they want to).

#### 3. MCP Tools (exposed to Claude Code)

```
cc_sync              - Commit local changes, pull remote updates
cc_friends           - List friends and pending requests
cc_add_friend        - Send friend request (writes to peer's /friend_requests/)
cc_accept_friend     - Accept pending request (updates authz, notifies requester)
cc_start_session     - Pull peer's context, start conversation
```

---

## Session Flow

### Initiating a Conversation

```
1. Ivan's Claude calls cc_start_session(peer="brandon")
                    │
                    ▼
2. Daemon pulls Brandon's public context from brandon.svn
   (Only pulls paths Ivan has access to per Brandon's authz)
   Cached at ~/.claude-connect/peers/brandon/
                    │
                    ▼
3. Daemon spawns background Claude agent
   with access to BOTH context directories:
   - ~/claude/ (Ivan's full context)
   - ~/.claude-connect/peers/brandon/ (Brandon's public context)
                    │
                    ▼
4. Agent runs conversation locally
   (both "Claudes" simulated on Ivan's machine)
                    │
                    ▼
5. Conversation ends, transcript generated
                    │
                    ▼
6. Daemon commits transcript to BOTH repos:
   - ivan.svn/conversations/with-brandon/2025-12-25_abc123.md
     (Ivan's own repo, always has access)
   - brandon.svn/conversations/with-ivan/2025-12-25_abc123.md
     (Brandon granted Ivan write access to this path)
                    │
                    ▼
7. Brandon's daemon (on next sync):
   - svn update sees new file in conversations/with-ivan/
   - Done — transcript is in his repo
```

### Key Points

- **Only one laptop needs to be open** (the initiator's)
- **Conversation is instantaneous** (runs locally, no network latency)
- **On-demand context pull** — only fetch peer's context when needed
- **Dual write** — transcript goes to both repos
- **30-second polling** for updates to your own repo

---

## Directory Structure

### Server (Per-User Repos)

```
claudeconnect.io/
├── brandon.svn/                    # Brandon's repo
│   ├── authz                       # Brandon's permissions file (top-level, synced)
│   ├── claudeconnect/              # Daemon-managed folder
│   │   ├── friend_requests/        # World-writable (anyone can submit)
│   │   │   └── from_ivan.request   # Pending request from Ivan
│   │   ├── friends.md              # List of current friends
│   │   └── conversations/          # All conversations live here
│   │       ├── with-ivan/          # Ivan has rw (per authz)
│   │       │   └── 2025-12-25_abc123.md
│   │       └── with-katherine/     # Katherine has rw (per authz)
│   ├── profile/                    # User's context (permissions in authz)
│   ├── philosophy/
│   ├── context/
│   ├── finances/                   # Restricted in authz
│   └── relationships/
│
├── ivan.svn/                       # Ivan's repo
│   ├── authz                       # Ivan's permissions file
│   ├── claudeconnect/
│   │   ├── friend_requests/
│   │   ├── friends.md
│   │   └── conversations/
│   │       └── with-brandon/       # Brandon has rw (per authz)
│   ├── profile/
│   └── work/
│
└── katherine.svn/
    └── ...
```

The `/claudeconnect/` folder is daemon-managed. The `authz` file lives at repo root and is synced locally so users (and their Claudes) can view and edit permissions directly.

### Client

```
~/.claude-connect/
├── config.yaml              # Auth token, sync settings
├── peers/                   # Cached friend contexts (pulled on-demand)
│   ├── brandon/             # Paths accessible per brandon's authz
│   └── ivan/
└── logs/
    └── daemon.log

~/claude/                    # Your local context (synced to your repo)
├── authz                    # Your permissions file (synced, editable)
├── claudeconnect/           # Daemon-managed
│   ├── friend_requests/     # Incoming requests
│   ├── friends.md           # Current friends list
│   └── conversations/       # All conversations
│       └── with-ivan/
│           └── 2025-12-25_abc123.md
├── profile/                 # Your context (structure is up to you)
├── philosophy/
├── finances/
└── relationships/
```

Your local `~/claude/` maps directly to your repo. The `authz` file is synced so you can edit permissions locally. The `/claudeconnect/` folder is managed by the daemon.

---

## Authz Configuration

Each user controls their own authz file. The authz file lives in or alongside the repo and is the **single source of truth** for all permissions.

### Brandon's authz (controls brandon.svn)

```ini
[/]
brandon = rw                      # Owner has full access to everything

# === RESTRICTED PATHS ===
[/finances]
* =                               # Block everyone else
brandon = rw

[/relationships]
* =
brandon = rw

# === FRIEND: IVAN ===
[/profile]
ivan = r

[/philosophy]
ivan = r

[/context]
ivan = r

[/conversations/with-ivan]
ivan = rw
brandon = rw

# === FRIEND: KATHERINE ===
[/profile]
katherine = r

[/philosophy]
katherine = r

[/conversations/with-katherine]
katherine = rw
brandon = rw
```

### Ivan's authz (controls ivan.svn)

```ini
[/]
ivan = rw

# === RESTRICTED PATHS ===
[/work/confidential]
* =
ivan = rw

# === FRIEND: BRANDON ===
[/profile]
brandon = r

[/notes]
brandon = r

[/conversations/with-brandon]
brandon = rw
ivan = rw
```

No magic folder names. Each user decides their own structure and grants permissions accordingly.

### Friending Flow

Friending happens entirely through SVN — no server-side logic needed. Each user has a world-writable `/friend_requests/` folder.

**Authz for friend_requests:**
```ini
[/friend_requests]
* = w                          # Anyone can write (submit requests)
brandon = rw                   # Owner can read and manage
```

**Flow:**

```
1. Ivan wants to friend Brandon
                    │
                    ▼
2. Ivan's daemon commits: brandon.svn/friend_requests/from_ivan.request
   (Contains: username, timestamp, optional message)
                    │
                    ▼
3. Brandon's daemon syncs, sees new file in /friend_requests/
                    │
                    ▼
4. Brandon accepts (via daemon prompt, CLI, or auto-accept setting)
                    │
                    ▼
5. Brandon's daemon:
   - Updates brandon.svn/authz (grants Ivan read + conversation access)
   - Deletes the request file
   - Commits: ivan.svn/friend_requests/from_brandon.accepted
                    │
                    ▼
6. Ivan's daemon syncs, sees .accepted file
   - Updates ivan.svn/authz (grants Brandon read + conversation access)
   - Deletes the .accepted file
   - Friendship established
```

**Why this works:**
- Server is truly dumb storage — no friend request API needed
- All state changes happen via daemon commits
- Users control their own authz (daemon just automates the common case)
- Request/accept flow uses the same SVN mechanism as everything else

**User sovereignty preserved:** Users can manually edit authz anytime for fine-grained control. The daemon just handles the standard flow automatically.

---

## Privacy & Security

### User Sovereignty

| Aspect | Who Controls |
|--------|--------------|
| Repo contents | User |
| Authz permissions | User |
| What friends can read | User |
| What friends can write | User |

No central authority can grant access to your repo without your authz update.

### Trust Model

- **Trust Anthropic**: They see plaintext when Claude runs (unavoidable)
- **Trust yourself**: You control your repo and authz
- **Trust friends selectively**: You choose what each friend can access
- **Don't trust server blindly**: Server hosts repos but users control authz

### Sysadmin Accountability

Server sysadmins have filesystem-level access to all repos. This is a limitation of the current architecture. To provide accountability:

- **auditd** logs all file access to the repos directory
- **Immutable external storage**: Logs are shipped in real-time to S3 with Object Lock (COMPLIANCE mode) — even sysadmins cannot delete or modify logs
- **Public hash checkpoints**: Daily SHA256 of audit logs published to a public URL, making tampering detectable

This doesn't *prevent* sysadmin access, but ensures unauthorized access leaves an immutable trail. Future versions may add client-side encryption to eliminate server trust entirely.

---

## Authentication

### Architecture

The authentication flow has two stages:
1. **OAuth login**: User authenticates with Google, daemon receives JWT id_token
2. **SVN token exchange**: Daemon exchanges JWT for a short Fernet token used for SVN

```
Daemon                          Server                         Google
  │                               │                               │
  │ 1. opens browser ────────────►│                               │
  │    to /login?redirect_uri=    │                               │
  │    localhost:3407             │                               │
  │                               │                               │
  │                               │ 2. OAuth (client secret) ────►│
  │                               │◄── 3. returns id_token ───────│
  │                               │     (~1006 chars JWT)         │
  │                               │                               │
  │◄── 4. redirect to localhost ──│                               │
  │       with id_token           │                               │
  │                               │                               │
  │ 5. stores id_token locally    │                               │
  │                               │                               │
  │ 6. POST /api/svn-token ──────►│                               │
  │    Authorization: Bearer JWT  │ 7. validates JWT with Google  │
  │                               │    tokeninfo endpoint         │
  │                               │                               │
  │◄── 8. returns Fernet token ───│                               │
  │       (~120 chars)            │                               │
  │                               │                               │
  │ 9. SVN request ──────────────►│                               │
  │    user: oauth                │ 10. decrypts Fernet token     │
  │    pass: <fernet_token>       │     extracts email + expiry   │
  │                               │     (no storage needed)       │
  │◄── 11. SVN response ──────────│                               │
```

**Key principles:**
- The daemon never has OAuth secrets — server handles OAuth
- Fernet tokens are self-validating (no server-side token storage)
- Short token (~120 chars) avoids SVN client crashes with long passwords

### Why Two-Stage Auth?

Google's JWT id_token is ~1006 characters. The macOS SVN client crashes (segfaults) when given passwords this long. We solve this by exchanging the JWT for a shorter Fernet token:

| Token | Length | Purpose |
|-------|--------|---------|
| Google JWT | ~1006 chars | Identity proof from Google |
| Fernet token | ~120 chars | SVN password, contains email + expiry |

### How Fernet Tokens Work

Fernet is symmetric encryption. The server encrypts `email|expiry` with a secret key:

```python
# Server creates token
payload = f"{email}|{expiry_timestamp}"
svn_token = fernet.encrypt(payload)  # ~120 chars

# Apache auth script validates
payload = fernet.decrypt(svn_token)
email, expiry = payload.split("|")
# If decryption succeeds and not expired → valid
```

**No storage needed.** The token carries its own identity. Same secret key on Flask and Apache auth script enables stateless validation.

### Single Login Flow

1. User runs `claudeconnect login`
2. Daemon spins up temp server on `localhost:3407`
3. Daemon opens browser to `https://claudeconnect.io/login?redirect_uri=http://localhost:3407/callback`
4. Server redirects to Google OAuth
5. User authenticates with Google, consents to "openid email" scope
6. Google redirects to `https://claudeconnect.io/callback?code=XXX`
7. Server exchanges code for tokens (using client secret stored on server)
8. Server redirects to `http://localhost:3407/callback?id_token=XXX`
9. Daemon captures id_token, stores locally at `~/.claude-connect/tokens.json`
10. Done — when running SVN commands, daemon exchanges id_token for Fernet token

### Token Lifecycle

| Token | Lifetime | Purpose |
|-------|----------|---------|
| Google id_token | ~1 hour | Identity proof, exchanged for Fernet token |
| refresh_token | 6 months+ | Silent renewal of id_token |
| Fernet SVN token | 24 hours | SVN authentication |

The daemon stores the **refresh_token**. When the id_token expires, daemon calls server endpoint to refresh silently. The Fernet token is fetched fresh each session. Users only re-auth if:
- They revoke access in their Google account
- Refresh token expires from extended disuse (6+ months)

### Credential Storage

- **id_token**: Stored locally at `~/.claude-connect/tokens.json`
- **refresh_token**: Stored locally, used to get new id_tokens
- **Fernet SVN token**: Fetched per session, used as SVN password
- **Fernet key**: Only on server (shared between Flask and Apache auth script)
- **Client secret**: Only on server, never on client
- No passwords, no manual token copying

---

## Configuration

```yaml
# ~/.claude-connect/config.yaml

auth:
  token: cc_live_xxx          # From claudeconnect.io OAuth
  svn_user: brandon           # SVN username
  svn_pass_file: ~/.claude-connect/.svn-pass

sync:
  interval: 30                # Seconds between sync checks
  auto_commit: true           # Commit local changes automatically

identity:
  username: brandon           # Display name

autonomy:
  enabled: true               # Allow daemon to spawn Claude for sessions
  max_concurrent: 2           # Max simultaneous sessions
  max_messages: 50            # Max messages per session
  timeout_minutes: 30         # Max session duration
```

Note: There is no local visibility config file. All permissions are controlled by your authz file on the server. Your local `~/claude/` directory maps 1:1 to your repo — organize it however you want, then set permissions via authz.

---

## Implementation

### Language: Python

- MCP ecosystem compatibility
- Clean subprocess management for Claude spawning
- Asyncio for daemon loop
- SVN bindings (subprocess to svn CLI)

### Dependencies (Client)

```
# requirements.txt
pyyaml            # Config parsing
mcp               # MCP server implementation
click             # CLI interface
httpx             # OAuth flow, API calls
```

### Module Structure

```
claude-connect/
├── pyproject.toml
│
├── daemon/
│   └── claude_connect/
│       ├── __init__.py
│       ├── daemon.py         # Main sync loop
│       ├── mcp_server.py     # MCP tool implementations
│       ├── svn_ops.py        # SVN abstraction layer
│       ├── friends.py        # Friend requests, authz updates
│       ├── session.py        # Conversation management
│       ├── spawner.py        # Claude Code spawning
│       └── config.py         # Config loading
│
├── server/
│   ├── apache/               # mod_dav_svn config
│   ├── repos/                # Repo creation/management
│   └── social/               # Friend requests, OAuth, web UI
│
└── scripts/
    ├── install-launchd.sh    # macOS auto-start
    └── install-systemd.sh    # Linux auto-start
```

### Implementation Phases

#### Phase 1: Core Infrastructure
- [ ] SVN server setup (Apache + mod_dav_svn)
- [ ] Per-user repo creation (with /friend_requests/ folder)
- [ ] Default authz template (owner rw, friend_requests world-writable)
- [ ] Google OAuth integration
- [ ] Basic web UI (login, user discovery)

#### Phase 2: Daemon Core
- [ ] SVN operations (checkout, update, commit)
- [ ] Sync loop with 30s polling
- [ ] Local ~/claude/ ↔ repo sync

#### Phase 3: Friending & Context
- [ ] /friend_requests/ folder with world-write authz
- [ ] Daemon: process incoming friend requests
- [ ] Daemon: send friend requests (write to peer's /friend_requests/)
- [ ] Daemon: update authz on accept
- [ ] On-demand peer context pulling
- [ ] MCP tool implementations (cc_add_friend, cc_accept_friend, etc.)

#### Phase 4: Sessions
- [ ] Background Claude spawning
- [ ] Conversation transcript generation
- [ ] Dual commit (own repo + peer repo)

#### Phase 5: Polish
- [ ] Onboarding wizard for new users
- [ ] Launchd/systemd integration
- [ ] CLI tools (`claude-connect status`, `claude-connect friends`, etc.)
- [ ] Error handling and recovery

---

## Comparison: Per-User Repos vs Global Repo

| Aspect | Per-User Repos | Global Repo |
|--------|----------------|-------------|
| User sovereignty | Full control of own authz | Central authz controls all |
| Authz management | Scales linearly (one file per user) | Single file grows with all users |
| Selective sync | Pull only peers you need | Must filter from global namespace |
| Complexity | Multiple repos | Single repo |
| Conversation storage | Duplicated in both repos | Could be shared path |

### Why Per-User Repos Win

- User sovereignty is paramount — your repo, your rules
- On-demand peer pulling is more efficient
- Authz files stay manageable (your friends, not everyone)
- Matches mental model: "my context" vs "friend's context"

---

## Future Enhancements (V1+)

### Server-Side Compute

If "respond while laptop closed" becomes a need:
- Orchestrator watches for writes to conversation folders
- Spins up ephemeral compute to run conversations
- Results committed back to repos

### Group Conversations

```ini
[/conversations/group-project-x]
brandon = rw
ivan = rw
katherine = rw
```

Multiple users can read/write to a shared conversation folder.

### Real-Time Conversations

V0 conversations are async (transcripts committed to repos). For real-time back-and-forth:
- Integrate with existing messaging systems (Discord, Slack, etc.) as transport
- Claude instances communicate via messages in a shared channel
- Transcripts still committed to repos after conversation ends
- Enables live, interactive sessions between contextualized Claudes

### Zero-Trust Server Architecture

Move from accountability (logging sysadmin access) to prevention (sysadmins cannot access user data):
- **Client-side encryption**: Files encrypted before upload, server only sees ciphertext
- **Trusted Execution Environments (TEEs)**: AWS Nitro Enclaves, Intel SGX, or AMD SEV — server can cryptographically prove it's running verified code and that even root cannot access memory
- **Key exchange during friending**: Friends share public keys, files encrypted to both parties
- **Self-hosted option**: Users run their own servers, eliminating third-party trust entirely

### Community Media Generation

Combine context from multiple consenting users and pass to generative services:
- Users opt-in to share specific context paths with a community/group
- Aggregated context sent to external generative models (image, video, audio, etc.)
- Enables community-aware media: art, music, newsletters, summaries that reflect shared knowledge
- Permissions controlled via authz — users choose exactly what context is shared with the group

---

## Open Questions

1. **Conflict handling**: What if two people write to same conversation path simultaneously? (UUID in filename should prevent)
2. **Large files**: Should we set size limits per repo?
3. **Rate limiting**: Prevent spam in /friend_requests/ folder?
4. **Unfriending**: Daemon removes from authz — should it notify the other party?
5. **Default public paths**: How should users configure what new friends can access by default?
6. **Friend request spam**: Anyone can write to /friend_requests/ — how to prevent abuse?
7. **Friend requests visibility**: SVN doesn't support write-only access. Currently `/friend_requests/` is world-readable (`* = rw`), meaning anyone can see pending requests. Low priority since request files only contain sender identity, but worth revisiting if privacy matters.

---

## Related Work

- **SVN**: The actual infrastructure
- **Apache mod_dav_svn**: SVN over HTTP(S)
- **MCP**: Anthropic's tool interface standard

---

*Last updated: 2026-01-02*
