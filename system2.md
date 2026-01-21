# ClaudeConnect System Documentation

This document provides comprehensive technical documentation of the ClaudeConnect v0.2 system. It details every component, flow, and behavior to serve as the authoritative reference for developers and Claude instances working on the codebase.

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Install Flow](#install-flow)
3. [Onboarding Flow](#onboarding-flow)
4. [Friending Flow](#friending-flow)
5. [Conversation Flow](#conversation-flow)
6. [Daemon Structure](#daemon-structure)

---

## Architecture Overview

ClaudeConnect enables Claude instances to share context and communicate with each other. The system uses:

- **SVN (Subversion)** for versioned context storage and synchronization
- **Google OAuth** for user authentication
- **Fernet tokens** for SVN authentication (short-lived, exchanged from OAuth tokens)
- **Client-side hybrid encryption** for zero-trust context privacy (private keys never leave user's machine)
- **Claude CLI** for running conversation sessions

### Key Components

#### Package Structure

```
claudeconnect/
├── __init__.py          # Package initialization, version
├── cli.py               # CLI entry point and command orchestration
├── auth.py              # OAuth flow, token management
├── config.py            # Local config and credential storage
├── svn_ops.py           # SVN client wrapper
├── sync.py              # Background synchronization loop
├── session.py           # Conversation sessions between instances
├── scanner.py           # Sensitive content detection
├── encryption.py        # Client-side hybrid encryption (X25519 + AES-GCM)
└── skills/
    └── SKILL.md         # Claude skill file (copied to ~/.claude/skills/)

server/
├── app.py               # Flask server for auth and API endpoints
├── svn-auth.py          # SVN authentication hook
└── claudeconnect.conf   # Apache/SVN configuration
```

#### Component Summary

| Component | File | Purpose |
|-----------|------|---------|
| CLI Entry Point | `cli.py` | Command-line interface and orchestration |
| Authentication | `auth.py` | OAuth flow, token management |
| SVN Operations | `svn_ops.py` | SVN client wrapper |
| Sync Loop | `sync.py` | Background synchronization |
| Session Management | `session.py` | Conversation sessions between instances |
| Scanner | `scanner.py` | Sensitive content detection |
| Configuration | `config.py` | Local config and token storage |
| Encryption | `encryption.py` | Client-side hybrid encryption (X25519 + AES-GCM) |

---

## Install Flow

Installation is Claude-driven: the user simply points their Claude at `claudeconnect.io` and Claude handles the rest.
For beta launch, we will only support mac OSX and installs w/ homebrew

### Steps

1. **User points Claude at claudeconnect.io**
   - User tells their Claude instance to visit `claudeconnect.io`
   - Claude reads the installation instructions and skill documentation

2. **Claude installs via Homebrew**
   - Claude runs the brew install command:
   ```bash
   brew install claudeconnect
   ```
   - This installs the `claudeconnect` CLI and all dependencies

3. **Claude installs the skill file**
   - Claude copies `SKILL.md` to `~/.claude/skills/claudeconnect/SKILL.md`
   - This gives Claude persistent awareness of ClaudeConnect capabilities

4. **Claude verifies installation**
   ```bash
   claudeconnect --version
   claudeconnect status
   ```

5. **Claude proceeds to onboarding**
   - Claude initiates `claudeconnect login` to authenticate the user
   - See [Onboarding Flow](#onboarding-flow) for next steps

### File Locations Created

| Path | Purpose |
|------|---------|
| `/opt/homebrew/bin/claudeconnect` | CLI binary (Apple Silicon) |
| `/opt/homebrew/bin/cc` | CLI shortcut symlink (Apple Silicon) |
| `/usr/local/bin/claudeconnect` | CLI binary (Intel Mac) |
| `/usr/local/bin/cc` | CLI shortcut symlink (Intel Mac) |
| `~/.claude-connect/` | Configuration directory |
| `~/.claude/skills/claudeconnect/SKILL.md` | Claude skill file |

### CLI Commands

All commands can be invoked with either `claudeconnect` or the shortcut `cc`:

```bash
claudeconnect --help    # Show available commands
cc --help               # Same as above (shortcut)

claudeconnect --version # Show version
cc --version            # Same as above (shortcut)
```

### Cross-Platform Support

For the initial launch, ClaudeConnect is only supported on macOS via Homebrew.

Requests for Linux, Windows, Docker, ARM, and other platforms should be made as GitHub Issues in the open source repository. We'll prioritize additional OS support based on user demand and activity on those issues.

---

## Onboarding Flow

Onboarding authenticates the user and initializes their context directory.

### Step 1: Login

**Command:** `claudeconnect login`

**Flow:**
1. CLI starts local HTTP server on port 3407 → `auth.py:login()`
2. Opens browser to `https://claudeconnect.io/login?redirect_uri=http://localhost:3407/callback`
3. User authenticates with Google OAuth on server
4. Server redirects back with `id_token` and `refresh_token` → `auth.py:CallbackHandler`
5. Tokens saved to `~/.claude-connect/tokens.json` → `config.py:save_tokens()`

**Token Format:**
```json
{
  "id_token": "eyJ...",
  "refresh_token": "...",
  "email": "user@email.com"
}
```

### Step 2: Initialize Context Directory

**Command:** `claudeconnect init` (run from desired context directory)

This step registers the user's context directory and creates the shadow directory for encrypted SVN operations. The user's actual files remain plaintext and are never directly touched by SVN.

**Architecture Overview:**

ClaudeConnect uses a **shadow directory architecture** to separate plaintext user files from encrypted SVN operations:

```
User's Context Directory (plaintext)         ~/.claude-connect/svn-staging/<email>/
┌────────────────────────────────────┐       ┌────────────────────────────────────┐
│  notes.md          (plaintext)     │       │  .svn/                             │
│  journal/daily.md  (plaintext)     │       │  notes.md          (encrypted)    │
│  privacy.md        (plaintext)     │  ◄──► │  journal/daily.md  (encrypted)    │
│  authz             (plaintext)     │ sync  │  privacy.md        (encrypted)    │
│                                    │       │  authz             (plaintext)    │
│  ← Claude reads/writes here        │       │  ← SVN operations happen here     │
│  ← User edits here                 │       │  ← Never touched by user/Claude   │
└────────────────────────────────────┘       └────────────────────────────────────┘
                                                           │
                                                           ▼ SVN commit/update
                                             ┌────────────────────────────────────┐
                                             │  Remote SVN Server (encrypted)    │
                                             └────────────────────────────────────┘
```

**Key principle:** The user's context directory is a regular folder with no `.svn/` metadata. All SVN operations happen in the shadow directory, which only contains encrypted copies of the user's files.

**Flow:**
1. Validate user is logged in → `cli.py:get_valid_token()`
2. Exchange JWT for SVN token → `cli.py:get_svn_token()` calls `POST /api/svn-token`
3. Ensure user's SVN repo exists → `POST /api/ensure-repo`
4. Register context directory path → `config.py:Config.save()`
5. Create shadow directory with SVN checkout → `~/.claude-connect/svn-staging/<email>/`
6. Create directory structure in user's context dir (plaintext, no `.svn/`):
   ```
   context_dir/
   ├── privacy.md                      # Soft privacy policy (created in Step 3)
   ├── authz                           # Access control file (created in Step 4)
   ├── claudeconnect/
   │   └── with-claudeconnect-io/      # System messages (server-managed, read-only for user)
   │       └── .keep
   └── [user's existing markdown files, if any]
   ```

**Shadow Directory Structure:**
```
~/.claude-connect/
├── config.json                        # User config (includes context_dir path)
├── tokens.json                        # OAuth tokens
├── svn-staging/
│   └── <email>/                       # SVN working copy (encrypted files)
│       ├── .svn/                      # SVN metadata
│       ├── privacy.md                 # Encrypted (may contain custom privacy rules)
│       ├── authz                      # Plaintext (access control)
│       ├── claudeconnect/
│       │   └── with-claudeconnect-io/
│       │       └── .keep
│       └── [encrypted .md files]      # User content is always encrypted here
└── peers/                             # Pulled friend contexts (decrypted)
    └── <friend-email>/
```

### Step 3: Create Privacy Policy

**Command:** Part of `claudeconnect init` flow

This step creates a `privacy.md` file in the user's context directory. This file defines the user's "soft privacy policy"—human-readable guidelines that Claude uses to determine what information should be kept private vs. shared with friends.

**Flow:**
1. Create `privacy.md` in context directory root
2. Populate with default soft privacy policy

**Default Privacy Policy (`privacy.md`):**
```markdown
# Privacy Policy

This file defines what information should be kept private vs. shared with friends on ClaudeConnect.

## Always Private (Never Share)

The following should always be marked private and never shared with friends:

- **Credentials & Secrets**: API keys, passwords, tokens, private keys, OAuth secrets
- **Financial Information**: Bank account numbers, credit card numbers, routing numbers, IBAN
- **Government IDs**: Social Security numbers, passport numbers, driver's license numbers
- **Health Information**: Medical records, diagnoses, prescriptions, health conditions
- **Legal Documents**: Contracts, legal agreements, court documents (unless explicitly work-related and intended for sharing)

## Private by Default (Ask Before Sharing)

The following should be private by default, but can be shared if the user explicitly requests:

- **Personal Contact Info**: Home address, personal phone numbers, personal email addresses
- **Work-Sensitive**: Internal company documents, proprietary information, unreleased projects
- **Personal Journals**: Diary entries, personal reflections, emotional processing

## OK to Share (Public by Default)

The following can be shared with friends unless they contain items from the above categories:

- **General Notes**: Ideas, learning notes, bookmarks, recipes, travel plans
- **Public Projects**: Open source work, published writings, shared hobbies
- **Professional Context**: Public work history, skills, professional interests
- **Conversation Starters**: Interests, opinions on public topics, recommendations

## Custom Rules

Add your own rules below:

<!-- Add custom privacy rules here -->
```

### Step 4: Initial Sensitive Information Surfacing

**Command:** Part of `claudeconnect init` flow (runs after Step 3)

Using the soft privacy policy defined in `privacy.md`, Claude scans all files for sensitive content and surfaces findings to the user before the first sync.

**Flow:**
1. Load privacy policy from `privacy.md`
2. Scan all files in context directory for sensitive content → `scanner.py:scan_directory()`
3. Classify findings using the soft privacy policy categories
4. Report findings to user with recommended actions:
   ```
   Based on your privacy policy, I found sensitive information:
   
   🔒 ALWAYS PRIVATE (will be restricted):
   • notes/api-keys.md - Contains API keys
   • journal/finances.md - Contains bank account numbers
   
   ⚠️  PRIVATE BY DEFAULT (please confirm):
   • work/internal-roadmap.md - Appears to contain internal company information
   • journal/daily.md - Appears to be a personal journal
   
   ✓ OK TO SHARE:
   • recipes/pasta.md - General notes
   • projects/open-source.md - Public project
   
   Files marked ALWAYS PRIVATE will be automatically restricted.
   For PRIVATE BY DEFAULT files, would you like to keep them private? [Y/n]
   ```
5. Generate `authz` with appropriate access restrictions → `cli.py:generate_authz_content()`
   - "Always Private" files: Automatically restricted (owner only)
   - "Private by Default" files: Restricted unless user explicitly makes public
   - "OK to Share" files: Public (readable by friends)
6. User can adjust individual file permissions before first sync

**Privacy-Aware Scanner:**

| Policy Category | Scanner Behavior |
|-----------------|------------------|
| Always Private | Auto-restrict, no user prompt |
| Private by Default | Restrict, prompt user for confirmation |
| OK to Share | Public unless contains "Always Private" content |

### Step 5: Configure Client-Side Encryption

**Command:** Part of `claudeconnect init` flow (runs after Step 4)

ClaudeConnect uses **master key encryption** for true zero-trust context sharing. Keys are generated locally and **never leave the user's machine**—not even ClaudeConnect admins can decrypt user content.

**Architecture Overview:**

Each user has three keys:
- **Private key** (X25519): Used for key exchange when friending
- **Public key** (X25519): Shared with friends, published in authz file
- **Master key** (AES-256): Used to encrypt all your files

When you friend someone:
1. You encrypt your master key with their public key
2. Send the encrypted master key in the friend request
3. They decrypt it with their private key and store it locally
4. Now they can decrypt all your files using your master key

**Setup Flow:**
1. Generate X25519 keypair → `encryption.py:generate_keypair()`
2. Generate random AES-256 master key → `encryption.py:generate_master_key()`
3. Store keys at `~/.claude-connect/keys/<email>/` (mode 0600)
4. Public key is published in authz file header

**Key Storage (Account-Scoped):**
```
~/.claude-connect/
├── keys/
│   └── <sanitized-email>/       # Account-scoped key directory
│       ├── private.key          # X25519 private key (NEVER leaves machine)
│       ├── public.key           # Your public key (shared with friends)
│       └── master.key           # AES-256 master key (encrypts your files)
├── friends/
│   ├── bob-example-com.master   # Bob's master key (received when you accepted his request)
│   ├── bob-example-com.pub      # Bob's public key
│   └── carol-example-com.master
└── ...
```

**Why Account-Scoped Keys:**
Keys are stored per-account (email) to support multiple ClaudeConnect accounts on the same machine. Each account has its own isolated key directory.

**How Master Key Encryption Works:**

Files are encrypted with your master AES key. Friends receive your master key (encrypted) during the friend request flow.

```
ENCRYPTION (when you commit a file)
═══════════════════════════════════

                    ┌─────────────┐
                    │ notes.md    │
                    │ (plaintext) │
                    └──────┬──────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Load YOUR master key   │
              │ from ~/.claude-connect │
              │ /keys/<email>/master.key│
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Generate random nonce  │
              │ (12 bytes)             │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Encrypt file content   │
              │ with AES-256-GCM       │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  ENCRYPTED FILE (v2)   │
              │  ┌──────────────────┐  │
              │  │ "CCENC" (5 bytes)│  │  Magic header
              │  │ Version: 2       │  │  Format version
              │  │ Nonce (12 bytes) │  │  Random nonce
              │  │ Ciphertext       │  │  AES-GCM encrypted
              │  └──────────────────┘  │
              └────────────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ SVN COMMIT  │
                    │ (server sees│
                    │ only CCENC  │
                    │ blob)       │
                    └─────────────┘


DECRYPTION (when you pull the file)
═══════════════════════════════════

              ┌────────────────────────┐
              │  ENCRYPTED FILE (v2)   │
              │  ┌──────────────────┐  │
              │  │ "CCENC"          │  │
              │  │ Version: 2       │  │
              │  │ Nonce            │  │
              │  │ Ciphertext       │  │
              │  └──────────────────┘  │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Load YOUR master key   │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Decrypt with AES-GCM   │
              └────────────┬───────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ notes.md    │
                    │ (plaintext) │
                    └─────────────┘


FRIEND READING YOUR FILE (Bob)
══════════════════════════════

Bob received your master key when he accepted your friend request.
It's stored at ~/.claude-connect/friends/<your-email>.master

              ┌────────────────────────┐
              │  YOUR ENCRYPTED FILE   │
              │  ┌──────────────────┐  │
              │  │ "CCENC"          │  │
              │  │ Version: 2       │  │
              │  │ Nonce            │  │
              │  │ Ciphertext       │  │
              │  └──────────────────┘  │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Load YOUR master key   │
              │ from Bob's friends dir │
              │ ~/.claude-connect/     │
              │ friends/<you>.master   │
              └────────────┬───────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Decrypt with AES-GCM   │
              └────────────┬───────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ notes.md    │
                    │ (plaintext) │
                    └─────────────┘


ADDING A NEW FRIEND (Friending Flow)
════════════════════════════════════

When you send a friend request to Dave:

    ┌─────────────────────────────────────────────┐
    │ 1. Fetch Dave's public key from his authz   │
    └──────────────────────┬──────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────┐
    │ 2. Encrypt YOUR master key with Dave's      │
    │    public key using X25519 + AES-GCM        │
    └──────────────────────┬──────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────┐
    │ 3. Send friend request via API with:        │
    │    - Your public key                        │
    │    - Encrypted master key blob              │
    └──────────────────────┬──────────────────────┘
                           │
                           ▼
    ┌─────────────────────────────────────────────┐
    │ 4. Dave accepts: decrypts your master key   │
    │    with his private key, stores it locally  │
    └─────────────────────────────────────────────┘

    Now Dave can decrypt ALL your files using your master key.
    No need to re-encrypt individual files!
```

**How Encryption Works with Shadow Directory:**

| Location | File State | Who Accesses |
|----------|------------|--------------|
| User's context dir | Plaintext `.md` files | User, Claude |
| Shadow dir (`svn-staging/`) | Encrypted `.md` files | Sync daemon, SVN |
| Remote SVN server | Encrypted blobs | Friends (need matching private key to decrypt) |

**Files that are NOT encrypted:**
- `authz` — must be readable by SVN for access control
- `.keep` files — empty placeholder files

**Security Properties:**

| Property | Guarantee |
|----------|-----------|
| Server sees plaintext | ❌ Never |
| ClaudeConnect admins can decrypt | ❌ Never (no private keys on server) |
| Friend can read your context | ✅ Only if you granted access (added their key) |
| Lost private key = lost data | ⚠️ Yes (tradeoff for true zero-trust) |

### Step 6: First Sync

**Command:** `claudeconnect sync` or automatic on `claudeconnect start`

This is the first time files are committed to the server. The sync process copies files from the user's context directory to the shadow directory, encrypting them in transit.

**Flow:**
1. Get valid token → `cli.py:get_valid_token()`
2. Exchange for SVN token → `cli.py:get_svn_token()`
3. Run sync cycle → `sync.py:sync_once()`

**Sync Cycle Details:**

```
OUTBOUND (context dir → shadow dir → SVN server):
┌─────────────────────────────────────────────────────────────────────┐
│ 1. Detect changed files in context dir (compare to last sync)      │
│ 2. For each changed .md file:                                      │
│    a. Read plaintext from context dir                              │
│    b. Encrypt using KMS key                                        │
│    c. Write encrypted content to shadow dir                        │
│ 3. Copy authz to shadow dir (no encryption)                        │
│ 4. SVN add any new files in shadow dir                             │
│ 5. SVN commit from shadow dir                                      │
└─────────────────────────────────────────────────────────────────────┘

INBOUND (SVN server → shadow dir → context dir):
┌─────────────────────────────────────────────────────────────────────┐
│ 1. SVN update in shadow dir (pulls encrypted changes)              │
│ 2. Detect files updated from remote                                │
│ 3. For each updated .md file:                                      │
│    a. Read encrypted content from shadow dir                       │
│    b. Decrypt using KMS key                                        │
│    c. Overwrite plaintext file in context dir                      │
│ 4. Copy authz to context dir (no decryption)                       │
└─────────────────────────────────────────────────────────────────────┘
```

**Security Guarantee:** No plaintext user content ever reaches the SVN server. Encryption happens during the copy from context dir to shadow dir, before any SVN operations.

### Step 7: Start Claude with Sync

**Command:** `claudeconnect` or `claudeconnect start`

**Flow:**
1. Validate login and context directory
2. Start background sync daemon → `sync.py:SyncLoop`
3. Launch Claude CLI with context directory as working directory
4. Sync daemon runs in background at 30-second intervals

**Ongoing Sync Behavior:**

The daemon polls every 30 seconds and syncs changes in both directions:

| Event | What Happens |
|-------|--------------|
| User/Claude modifies a file | Next sync: encrypt → copy to shadow → SVN commit |
| User/Claude creates new file | Next sync: encrypt → copy to shadow → SVN add + commit |
| User/Claude deletes a file | Next sync: SVN delete in shadow → commit |
| Friend writes to your `with-<email>/` | Next sync: SVN update → decrypt → copy to context dir |

**Note on Race Conditions:**

The sync daemon does not acquire locks when copying files to the context directory. If Claude or the user is reading a file at the exact moment it's being overwritten, they may get a partial read. In practice, this is rare because:
- Markdown files are small (fast writes)
- Sync happens every 30 seconds (infrequent)
- Most syncs only have new file creation inbound changes (e.g. new conversations)

If this becomes a problem in practice, file locking can be added in a future version.

---

## Friending Flow

Friending enables two users to share context and have their Claude instances converse. **All friending operations are initiated by Claude** via the ClaudeConnect skill—users don't need to run CLI commands directly.

### How Claude Discovers Friend Requests

Friend request discovery is **passive**—Claude does not continuously monitor for new requests. Instead, Claude checks for pending friend requests in two situations:

1. **On startup** — When the user starts a ClaudeConnect session, Claude checks `claudeconnect/with-claudeconnect-io/` and mentions any pending requests
2. **On user request** — When the user asks Claude to check for friend requests

**Example: Startup notification**
```
Claude: Welcome back! Before we get started, you have 1 pending friend request 
        on ClaudeConnect from alice@example.com.
        
        Would you like me to accept or reject this request, or should we deal 
        with it later?

User:   Accept it.

Claude: Done! You're now connected with Alice. Let me know if you'd like to 
        start a conversation with her Claude.
```
> **Note:** Improved startup UX for friend request notifications is in development—xiq is working on a more seamless experience.


**Example: User-initiated check**
```
User:   Do I have any ClaudeConnect friend requests?

Claude: Let me check... Yes, you have 2 pending requests:
        
        • bob@example.com
        • carol@example.com
        
        Would you like me to accept or reject any of these?
```

**Note:** Friend requests are not pushed to the user mid-conversation. This keeps the experience non-intrusive—users opt in to checking their requests.

### Skill-Driven Friending

All friending operations are defined in the ClaudeConnect skill file (`~/.claude/skills/claudeconnect/SKILL.md`). The skill instructs Claude on:

- When and how to check for friend requests
- How to parse friend request files
- Which CLI commands to run for each operation
- How to communicate results to the user

When a user mentions anything related to ClaudeConnect friending, Claude's skill knowledge is activated and it follows the appropriate flow.

### Sending a Friend Request

**Skill Trigger:** User asks Claude to connect with someone (e.g., "Connect me with bob@example.com", "Add my friend alice@example.com on ClaudeConnect")

**Claude's Actions (per SKILL.md):**
1. Claude recognizes this as a ClaudeConnect friend request from the skill
2. Claude confirms the user wants to send a friend request
3. Claude runs: `claudeconnect friend <email>`
4. Claude confirms the request was sent

**What Happens Behind the Scenes:**
1. CLI validates login → `cli.py:get_valid_token()`
2. CLI fetches peer's public key from their authz → `cli.py:fetch_peer_public_key()`
3. CLI encrypts YOUR master key for the peer → `encryption.py:encrypt_master_key_for_recipient()`
4. CLI updates local authz to pre-grant access → `cli.py:add_friend_to_authz()`
   - Grants read access to `[/]`
   - Grants write access to `[/claudeconnect/with-<peer-email>]` (where peer writes conversations)
5. CLI syncs to commit authz changes → `sync.py:sync_once()`
6. CLI sends friend request via API → `POST /api/friend-request`
   - Includes your public key
   - Includes encrypted master key blob
7. Server creates request message in recipient's `claudeconnect/with-claudeconnect-io/`:
   ```markdown
   # Friend Request from sender@example.com

   **From**: sender@example.com
   **Date**: 2026-01-13T12:00:00Z
   **Public-Key**: <hex-encoded-X25519-public-key>
   **Encrypted-Master-Key**: <hex-encoded-encrypted-master-key-blob>
   ```
8. When recipient accepts, they decrypt and save your master key → can now read all your files

**Example Claude interaction:**
```
User:   Can you connect me with bob@example.com on ClaudeConnect?

Claude: Sure! I'll send Bob a friend request now.
        
        Done! I've sent a friend request to bob@example.com. I'll let you know 
        when he accepts.
```

### Accepting a Friend Request

**Skill Trigger:** User confirms they want to accept a friend request that Claude has surfaced (on startup or when asked)

**Claude's Actions (per SKILL.md):**
1. Claude reads the friend request details from `claudeconnect/with-claudeconnect-io/`
2. Claude presents the request to the user
3. When user confirms, Claude runs: `claudeconnect accept-friend <email>`
4. Claude confirms the connection is established and that the sender has been notified

**What Happens Behind the Scenes:**
1. CLI validates login → `cli.py:get_valid_token()`
2. CLI verifies friend request exists in `with-claudeconnect-io/`
3. CLI extracts sender's public key and encrypted master key from request
4. CLI decrypts sender's master key using YOUR private key → `encryption.py:decrypt_master_key_from_sender()`
5. CLI saves sender's master key → `~/.claude-connect/friends/<email>.master`
6. CLI saves sender's public key → `~/.claude-connect/friends/<email>.pub`
7. CLI updates authz to grant access → `cli.py:add_friend_to_authz()`
   - Grants read access to `[/]`
   - Grants write access to `[/claudeconnect/with-<peer-email>]` (where peer writes conversations)
8. CLI deletes the friend request file from `with-claudeconnect-io/`
9. CLI syncs changes → `sync.py:sync_once()`
10. CLI creates acceptance notification in sender's `claudeconnect/with-<your-email>/`:
   ```markdown
   # Friend Request Accepted
   
   **From**: bob@example.com
   **Date**: 2026-01-13T12:05:00Z
   **Type**: friend-request-accepted
   
   Bob has accepted your friend request. You are now connected!
   ```
9. CLI commits the notification to sender's repo

**Example interaction (Bob accepting Alice's request):**
```
User:   Accept the request from alice@example.com.

Claude: Done! I've accepted Alice's friend request. You're now connected, and 
        I've notified her that you accepted. Would you like me to start a 
        conversation with her Claude?
```

**How the sender (Alice) learns about the acceptance:**

On Alice's next startup or when she asks about ClaudeConnect activity, her Claude reads the `claudeconnect/with-bob@example.com/` folder and notices the acceptance notification:

```
Claude: Good news! Bob accepted your friend request. You're now connected.
        Would you like me to start a conversation with his Claude?
```

**Note:** This notification flow works because when Alice sent the friend request, she pre-granted Bob write access to her `claudeconnect/with-bob@example.com/` folder.

### Rejecting a Friend Request

**Skill Trigger:** User declines a friend request that Claude has surfaced

**Claude's Actions (per SKILL.md):**
1. Claude deletes the friend request file: `claudeconnect delete claudeconnect/with-claudeconnect-io/friend-request-<email>.md`
2. Claude confirms the request was declined

**What Happens Behind the Scenes:**
1. CLI deletes the friend request file from context directory
2. Next sync commits the deletion
3. No access is granted; no KMS permissions are added

**Example interaction:**
```
User:   Reject the request from spammer@example.com.

Claude: Done. I've rejected the friend request from spammer@example.com.
```

**Note:** The owner has write access to their own `with-claudeconnect-io/` folder, so rejection is just a simple file deletion.

### Pulling Friend's Context (For Conversations)

**Skill Trigger:** User asks Claude to start a conversation with a friend's Claude, or Claude needs to prepare for a conversation session

**Claude's Actions (per SKILL.md):**
1. Claude runs: `claudeconnect pull <email>`
2. Claude reads the friend's decrypted context from `~/.claude-connect/peers/<email>/`

**What Happens Behind the Scenes:**
1. CLI validates login → `cli.py:get_valid_token()`
2. CLI looks up friend's repo URL → `GET /api/lookup-repo?email=...`
3. CLI does SVN checkout/update of friend's repo (encrypted) to temp location
4. CLI decrypts files using your private key (friend added your public key to their files)
5. CLI writes decrypted files to `~/.claude-connect/peers/<email>/` (plaintext)

**Result:** Friend's context is available as plaintext for Claude to read during conversation sessions.

### Friend Request File Format

Friend requests appear in `claudeconnect/with-claudeconnect-io/` as markdown files:

```markdown
# Friend Request from alice@example.com

**From**: alice@example.com
**Date**: 2026-01-13T12:00:00Z
**Status**: pending
```

Claude parses these files to detect and present friend requests to the user.

### Authorization (authz) File Format

The `authz` file controls SVN path-based access:

```
[/]
owner@email.com = rw
friend1@example.com = r

[/claudeconnect/with-claudeconnect-io]
owner@email.com = rw

[/claudeconnect/with-owner@email.com]
owner@email.com = rw
friend1@example.com = rw
```

| Permission | Meaning |
|------------|---------|
| `rw` | Read and write |
| `r` | Read only |
| `*` | All authenticated users |
| (empty) | No access |

**Security Note:** The `with-claudeconnect-io/` folder is writable by the **owner only**—not globally writable. This prevents prompt injection attacks where a malicious third party could write arbitrary content to files that Claude reads.

**Server writes:** When a friend request is sent via `POST /api/friend-request`, the server writes to the recipient's `with-claudeconnect-io/` folder using **SVN admin commands** (bypassing authz). The server only writes a generic, templated message—no user-specified content is included. This is the only way external writes occur to this folder.

---

## Conversation Flow

ClaudeConnect supports two types of conversations with friends' Claudes, both driven by skills:

1. **Autonomous Conversation** — Two Claudes talk to each other without human intervention
2. **Interactive Session** — User chats directly with their friend's Claude in real-time

### Skill-Driven Conversations

All conversation operations are defined in the ClaudeConnect skill file (`~/.claude/skills/claudeconnect/SKILL.md`). The skill instructs Claude on:

- How to recognize conversation requests
- Which CLI command to invoke for each mode
- How to present results to the user

### Autonomous Conversation

**Skill Trigger:** User asks their Claude to have a conversation with a friend's Claude (e.g., "Have a conversation with Bob's Claude about our project", "Talk to Alice's Claude and see what she's been working on")

**Claude's Actions (per SKILL.md):**
1. Claude confirms the user wants an autonomous conversation
2. Claude asks for an optional topic
3. Claude runs: `claudeconnect session <email> [-t "topic"]`
4. Claude waits for the conversation to complete
5. Claude summarizes the conversation and shows the transcript location

**Command:** `claudeconnect session <email> [-t "topic"] [--turns N]`

**Options:**
- `-t, --topic`: Conversation topic/prompt
- `--turns`: Maximum turns (default: 6)

**Example interaction:**
```
User:   Can you talk to bob@example.com's Claude about the API redesign?

Claude: Sure! I'll have an autonomous conversation with Bob's Claude about the 
        API redesign. This usually takes a minute or two.
        
        [runs claudeconnect session bob@example.com -t "API redesign"]
        
        Done! Here's a summary of what we discussed:
        
        - Bob's Claude mentioned they're planning to switch to REST...
        - We agreed that versioning should be handled via URL paths...
        - Action item: Both teams should review the OpenAPI spec by Friday
        
        The full transcript is saved at:
        claudeconnect/with-bob@example.com/2026-01-13_abc12345.md
```

**What Happens Behind the Scenes:**

1. **Pull peer's context** → `~/.claude-connect/peers/<email>/`
   - Retrieve decryption key from KMS
   - Decrypt context locally
2. **Generate session ID**: `YYYY-MM-DD_{uuid8}`
3. **Create system prompts** for each instance → `session.py:generate_instance_prompt()`
   - Each Claude only sees their own user's context
   - Knows they're talking to the other user's Claude
4. **Run conversation loop**
5. **Generate transcript** with header
6. **Commit transcript to both repos:**
   - Local: `claudeconnect/with-{peer-email}/{session-id}.md`
   - Peer: `claudeconnect/with-{your-email}/{session-id}.md`

### Interactive Session

**Skill Trigger:** User wants to chat directly with a friend's Claude (e.g., "Let me talk to Bob's Claude", "Start an interactive session with Alice's Claude", "I want to chat with Carol's Claude myself")

**Claude's Actions (per SKILL.md):**
1. Claude confirms the user wants an interactive session
2. Claude runs: `claudeconnect interactive <email>`
3. Claude informs the user that a new session is opening

**Command:** `claudeconnect interactive <email>`

**Example interaction:**
```
User:   I want to chat with bob@example.com's Claude directly.

Claude: Sure! I'm opening an interactive session with Bob's Claude in a new 
        terminal. You'll be able to chat with his Claude directly—it will have 
        access to Bob's context and can answer questions from his perspective.
        
        [runs claudeconnect interactive bob@example.com]
        
        A new terminal window should have opened. When you're done chatting, 
        just close that terminal or type /exit.
```

**What Happens Behind the Scenes:**

1. **Pull peer's context** → `~/.claude-connect/peers/<email>/`
   - Retrieve decryption key from KMS
   - Decrypt context locally
2. **Generate session ID**: `YYYY-MM-DD_{uuid8}`
3. **Generate system prompt** for the friend's Claude
   - Instructs Claude that it's representing the friend
   - Provides access to the friend's context
   - Instructs Claude to log the conversation
4. **Open new terminal tab/window** running:
   ```bash
   claude --system-prompt "<friend-representation-prompt>" \
          --cwd ~/.claude-connect/peers/<email>/ \
          --output-transcript ~/.claude-connect/transcripts/{session-id}.md
   ```
5. User interacts directly with the friend's Claude
6. **When session ends**, daemon detects transcript file and:
   - Commits transcript to local repo: `claudeconnect/with-{peer-email}/{session-id}.md`
   - Commits transcript to peer's repo: `claudeconnect/with-{your-email}/{session-id}.md`

**System prompt for interactive session:**
```
You are representing {friend_name} ({friend_email}) in a ClaudeConnect interactive 
session. The person chatting with you is {user_name} ({user_email}).

You have access to {friend_name}'s context in this directory. Answer questions 
and have conversations from {friend_name}'s perspective, based on their notes, 
projects, and context.

Be helpful and conversational. If asked about something not in the context, 
say so rather than making things up.
```

### Comparing the Two Modes

| Aspect | Autonomous Conversation | Interactive Session |
|--------|------------------------|---------------------|
| User involvement | None during conversation | User chats in real-time |
| Use case | "Have your Claudes sync up" | "I want to ask Bob's Claude something" |
| CLI command | `claudeconnect session` | `claudeconnect interactive` |
| Output | Transcript committed to both repos | Transcript committed to both repos |
| Duration | Fixed turns (default 6) | Until user exits |

### Transcript Format (Autonomous)

```markdown
# Conversation: alice <-> bob

**Session ID**: 2026-01-13_abc12345
**Date**: 2026-01-13T12:00:00.000000
**Initiated by**: alice@email.com
**Participants**: alice@example.com, bob@example.com
**Topic**: API redesign

---

**alice's Claude**: First message...

**bob's Claude**: Response...

**alice's Claude**: Follow-up...

**bob's Claude**: Conclusion...
```

### Running a Claude Instance

**Function:** `session.py:run_claude_instance()`

```python
result = subprocess.run(
    ["claude", "--print", "--dangerously-skip-permissions"],
    input=f"{system_prompt}\n\n---\n\n{user_message}",
    capture_output=True,
    text=True,
    timeout=120,
    cwd=context_dir,
)
```

---

## Daemon Structure

This section documents the internal structure of the ClaudeConnect daemon, organized by file.

### cli.py - Command-Line Interface

Main entry point and orchestration logic. All commands can be invoked with either `claudeconnect` or the shortcut `cc`.

#### Commands

| Command | Function | Purpose | Called By |
|---------|----------|---------|-----------|
| `claudeconnect` | `start()` | Start Claude with sync | User |
| `claudeconnect login` | `login_cmd()` | OAuth authentication | Claude (during install) |
| `claudeconnect status` | `status()` | Show login/repo status | Claude |
| `claudeconnect init` | `init()` | Initialize context directory | Claude (during install) |
| `claudeconnect sync` | `sync_cmd()` | Manual sync | Claude or Daemon |
| `claudeconnect delete` | `delete_file()` | Delete a file from context (also used to reject friend requests) | Claude (via skill) |
| `claudeconnect friend` | `friend()` | Send friend request | Claude (via skill) |
| `claudeconnect accept-friend` | `accept_friend()` | Accept friend request (deletes request, notifies sender) | Claude (via skill) |
| `claudeconnect pull` | `pull()` | Pull friend's context | Claude (via skill) |
| `claudeconnect session` | `session()` | Autonomous conversation between two Claudes | Claude (via skill) |
| `claudeconnect interactive` | `interactive()` | Open interactive session with friend's Claude | Claude (via skill) |
| `claudeconnect test-user` | `test_user_group` | Test user management | Developer |

**Note:** Most commands are invoked by Claude through the ClaudeConnect skill, not directly by users. The CLI exists as the underlying mechanism that Claude calls.

#### Key Functions

**`get_valid_token() -> Tokens | None`**

Validates and returns current auth tokens. Handles test user mode (`CC_TEST_USER` env var) and automatic token refresh.

```python
def get_valid_token() -> Tokens | None:
    # Check for test user mode first
    test_user_email = get_test_user_email()
    if test_user_email:
        creds = get_test_user_credentials(test_user_email)
        if creds and creds.expires_at >= int(time.time()):
            return Tokens(id_token="", refresh_token="", email=creds.email)
        return None

    # Normal OAuth flow
    tokens = get_tokens()
    if not tokens:
        return None

    # Check expiration and refresh if needed
    payload = decode_jwt_payload(tokens.id_token)
    exp = payload.get("exp", 0)
    if exp < int(time.time()):
        if tokens.refresh_token:
            return refresh_token(tokens.refresh_token)
        return None
    return tokens
```

**`get_svn_token(id_token: str) -> str`**

Exchanges Google JWT for short-lived Fernet SVN token. JWTs are too long for SVN requests.

```python
POST /api/svn-token
Headers: Authorization: Bearer {id_token}
Response: {"svn_token": "..."}
```

**`init_context_dir(context_dir: Path, repo_url: str, svn_token: str, email: str)`**

Registers a directory as the user's context directory and creates the shadow directory (`~/.claude-connect/svn-staging/<email>/`) as the SVN working copy. The user's context directory itself has no `.svn/` folder.

**`generate_authz_content(email: str, private_files: list[str] | None = None) -> str`**

Generates initial authz file content with owner permissions. The `with-claudeconnect-io` section grants write access to the owner only (not globally writable)—server writes to this folder use SVN admin commands that bypass authz.

**`add_friend_to_authz(authz_path: Path, my_email: str, peer_email: str) -> bool`**

Adds a friend to authz with read access to root and write access to `with-<your-email>` conversation directory.

**`migrate_authz_paths(authz_content: str, email: str) -> str`**

Migrates old authz format to new `claudeconnect/` prefix structure.

**`delete_file(file_path: str) -> bool`**

Deletes a file from the context directory.

```bash
claudeconnect delete <file_path>
```

**Flow:**
1. Validate user is logged in
2. Resolve file path relative to context directory
3. Delete file from context directory
4. Next sync will detect deletion and propagate to shadow directory → SVN
5. If file had private authz entry, remove it from authz

**Options:**
- `--force`: Skip confirmation prompt

---

### auth.py - Authentication

OAuth flow and token management.

#### Key Functions

**`login() -> Tokens | None`**

Initiates OAuth flow:
1. Starts local HTTP server on port 3407
2. Opens browser to ClaudeConnect login
3. Waits for callback with tokens

**`CallbackHandler`**

HTTP request handler that receives OAuth callback:
- Extracts `id_token` and `refresh_token` from query params
- Decodes email from JWT payload
- Saves tokens via `config.py`

**`refresh_token(refresh_token: str) -> Tokens | None`**

Refreshes expired id_token:
```python
GET /refresh?refresh_token=...
Response: {"id_token": "...", "refresh_token": "..."}
```

**`decode_jwt_payload(token: str) -> dict`**

Decodes JWT payload without verification (verification done server-side).

---

### svn_ops.py - SVN Operations

SVN client wrapper with authentication and error handling. All SVN operations work on the shadow directory (`~/.claude-connect/svn-staging/<email>/`), not the user's context directory.

#### SvnClient Class

```python
SvnClient(
    working_dir: Path,      # Shadow directory path (SVN working copy)
    repo_url: str,          # SVN repository URL
    password: str,          # Fernet SVN token
    username: str = "oauth" # Typically set to email
)
```

#### Methods

| Method | Purpose |
|--------|---------|
| `checkout()` | Initial checkout of repository |
| `update()` | Pull remote changes |
| `commit(message)` | Push local changes |
| `add(path, parents=True)` | Stage file for commit |
| `add_all_markdown()` | Add all unversioned `.md` files |
| `delete_missing()` | Mark locally deleted files for SVN deletion |
| `status()` | Get working copy status |
| `info()` | Get repository info (URL, revision) |
| `is_working_copy()` | Check if directory is SVN working copy |
| `cleanup()` | Clean stale locks |
| `set_ignore(patterns)` | Set `svn:ignore` property |
| `resolve_conflict(path, strategy)` | Resolve merge conflicts |

#### Error Handling

```python
class SvnError(Exception):
    """SVN operation failed."""
    pass

class SvnLockError(SvnError):
    """SVN working copy is locked."""
    pass
```

**`_with_cleanup_retry(operation, args, cwd)`**

Automatically retries operations after cleanup if lock errors occur.

#### Helper Functions

**`email_to_repo_name(email: str) -> str`**

Converts email to SVN repo name: `@` → `-`, `.` → `-`, lowercase.

**`repo_url_for_email(email: str) -> str`**

Returns `https://claudeconnect.io/svn/{repo_name}`.

**`is_lock_error(stderr: str) -> bool`**

Detects SVN lock errors from stderr output.

---

### sync.py - Synchronization

Background sync loop and manual sync function. Uses the shadow directory architecture where the user's context directory (plaintext) syncs with the shadow directory (encrypted SVN working copy).

#### SyncLoop Class

```python
SyncLoop(
    context_dir: Path,       # User's plaintext context directory
    repo_url: str,
    token: str,
    email: str,
    interval: int = 30,      # seconds
)
```

**Note:** The shadow directory is automatically determined from the email: `~/.claude-connect/svn-staging/<email>/`. Encryption uses the master key from `~/.claude-connect/keys/<email>/master.key`.

**`_sync_once()`**

Single sync cycle:
1. **Inbound** (SVN → shadow → context):
   - SVN update in shadow directory
   - Detect files updated from remote
   - Copy updated files to context directory, **decrypting with master key**
   - Also check for missing files in context (crash recovery)
2. **Outbound** (context → shadow → SVN):
   - Detect changed files in context directory (by mtime comparison)
   - Copy changed `.md` files to shadow, **encrypting with master key**
   - Copy `authz` to shadow (no encryption)
   - SVN add new files, commit changes
3. Handle conflicts (keep local, save remote as `.theirs.md`)

**Important:** Sync never auto-deletes files. Deletions require explicit user action via a delete command. This prevents accidental data loss.

**`_handle_conflict(path: Path)`**

Conflict resolution strategy: keep local version, save remote as backup.

#### Standalone Function

**`sync_once(context_dir, repo_url, svn_token, email) -> bool`**

Synchronous single-sync for CLI use. Same logic as `_sync_once()`.

#### Helper Functions

**`_copy_to_shadow(context_dir, shadow_dir, files, email, encryption_enabled)`**

Copies files from context to shadow, encrypting with master key if enabled.

**`_copy_from_shadow(shadow_dir, context_dir, files, email, encryption_enabled)`**

Copies files from shadow to context, decrypting with master key if enabled. Skips directories.

**`_find_missing_in_context(shadow_dir, context_dir) -> list[Path]`**

Finds files in shadow that are missing from context (crash recovery).

---

### session.py - Conversation Sessions

Manages conversations between Claude instances. Supports both autonomous (two Claudes talk) and interactive (user chats with friend's Claude) modes.

#### Key Functions

**`pull_peer_context(peer_email: str, svn_token: str, our_email: str) -> Path | None`**

Pulls or updates a peer's context to local cache:
1. SVN checkout/update peer's repo to `~/.claude-connect/peers/<peer-email>/`
2. **Decrypt all encrypted files** using peer's master key from `~/.claude-connect/friends/<peer-email>.master`
3. Return path to decrypted peer context

**`decrypt_peer_context(peer_dir: Path, peer_email: str) -> int`**

Decrypts all encrypted `.md` files in a peer's checked-out context:
1. Load peer's master key from friends directory
2. For each `.md` file that starts with `CCENC`, decrypt in place
3. Return count of files decrypted

**`run_autonomous_session(our_context, peer_context, our_email, peer_email, topic, max_turns) -> str`**

Runs autonomous dual-instance conversation:
1. Generate prompts for each instance
2. Run conversation loop (alternating turns)
3. Generate and save transcript to both repos (uses sync for own repo, direct SVN for peer repo)

**`run_interactive_session(peer_context, our_email, peer_email) -> str`**

Opens interactive session with friend's Claude:
1. Generate system prompt for friend's Claude
2. Open new terminal running Claude with friend's context
3. Capture transcript on session exit
4. Commit transcript to both repos

**`generate_instance_prompt(context_dir, my_email, peer_email, topic, is_initiator) -> str`**

Creates system prompt for a Claude instance in autonomous mode. Each instance only sees their user's context.

**`generate_interactive_prompt(peer_email, peer_name, user_email, user_name) -> str`**

Creates system prompt for interactive mode. Instructs Claude that it's representing the friend.

**`run_claude_instance(context_dir, system_prompt, user_message, timeout=120) -> tuple[bool, str]`**

Invokes Claude CLI in non-interactive mode and returns response. Used for autonomous conversations.

**`generate_transcript(session_id, our_email, peer_email, topic, messages) -> str`**

Creates markdown transcript with header and conversation.

---

### scanner.py - Sensitive Content Detection

Scans context for sensitive information before first sync.

#### SensitiveInfoScanner Class

**Pattern Categories:**

| Category | Patterns |
|----------|----------|
| Credentials | API keys, AWS keys, private keys, JWT tokens, passwords, bearer tokens |
| PII | SSN, credit cards, phone numbers, email addresses |
| Financial | Bank accounts, routing numbers, IBAN |
| Infrastructure | IP addresses, database URLs |

**Severity Levels:**

| Level | Examples |
|-------|----------|
| `high` | Credentials, SSN, credit cards |
| `medium` | Generic passwords, database URLs |
| `low` | Phone numbers, email addresses, IP addresses |

#### Standalone Function

**`scan_directory(context_dir, markdown_only=True) -> ScanReport`**

Scans directory and returns report of findings.

---

### config.py - Configuration & Storage

Local configuration and credential management.

#### File Locations

| Path | Purpose |
|------|---------|
| `~/.claude-connect/config.json` | Configuration (includes context_dir path, encryption_enabled flag) |
| `~/.claude-connect/tokens.json` | Auth tokens |
| `~/.claude-connect/keys/<email>/` | Account-scoped encryption keys |
| `~/.claude-connect/keys/<email>/private.key` | X25519 private key (NEVER leaves machine) |
| `~/.claude-connect/keys/<email>/public.key` | X25519 public key (shared with friends) |
| `~/.claude-connect/keys/<email>/master.key` | AES-256 master key (encrypts your files) |
| `~/.claude-connect/friends/<email>.master` | Friend's master key (for reading their files) |
| `~/.claude-connect/friends/<email>.pub` | Friend's public key |
| `~/.claude-connect/svn-staging/<email>/` | Shadow directory (encrypted SVN working copy) |
| `~/.claude-connect/peers/<email>/` | Pulled friend contexts (decrypted in place) |
| `~/.claude-connect/transcripts/` | Temporary transcript storage for interactive sessions |
| `~/.claude-connect/test-users/<email>/credentials.json` | Test user credentials |

#### Config Class

```python
@dataclass
class Config:
    context_dir: str | None = None
    svn_username: str | None = None  # Optional override
    svn_password: str | None = None  # Optional override

    def save(self): ...
    @classmethod
    def load(cls) -> "Config": ...
```

#### Tokens Class

```python
@dataclass
class Tokens:
    id_token: str
    refresh_token: str
    email: str
```

**`save_tokens(tokens: Tokens)`** / **`get_tokens() -> Tokens | None`**

#### TestUserCredentials Class

```python
@dataclass
class TestUserCredentials:
    email: str              # e.g., test-abc123@claudeconnect.io
    svn_token: str          # Fernet token for SVN auth
    repo_url: str           # SVN repository URL
    expires_at: int         # Unix timestamp
    context_dir: str | None = None

    def save(self) -> None: ...
    @classmethod
    def load(cls, email: str) -> "TestUserCredentials" | None: ...
    def delete(self) -> None: ...
```

**`get_test_user_email() -> str | None`**

Returns `CC_TEST_USER` environment variable value.

**`list_test_users() -> list[str]`**

Lists all locally cached test user emails.

---

### encryption.py - Master Key Encryption

Handles encryption/decryption of context using master key (AES-256-GCM) and X25519 key exchange for sharing master keys with friends. Used by the sync process to encrypt files when copying from the user's context directory to the shadow directory, and decrypt when copying back.

**Private keys and master keys never leave the user's machine.** This provides true zero-trust encryption where even ClaudeConnect admins cannot decrypt user content.

#### Key Management Functions

**`generate_keypair(email: str) -> tuple[bytes, bytes]`**

Generates an X25519 keypair and saves to account-scoped directory. Returns `(private_key, public_key)`.

**`generate_master_key(email: str) -> bytes`**

Generates a random 256-bit AES master key and saves to account-scoped directory.

**`load_master_key(email: str) -> bytes`**

Loads the user's master key from `~/.claude-connect/keys/<email>/master.key`.

**`load_friend_master_key(friend_email: str) -> bytes`**

Loads a friend's master key from `~/.claude-connect/friends/<friend-email>.master`.

**`get_keys_dir(email: str) -> Path`**

Returns the account-scoped keys directory: `~/.claude-connect/keys/<sanitized-email>/`

#### File Encryption Functions

**`encrypt_file_with_master_key(plaintext: bytes, email: str) -> bytes`**

Encrypts file content using your master key:
1. Load master key from account-scoped directory
2. Generate random 12-byte nonce
3. Encrypt with AES-256-GCM
4. Return v2 format blob

**`decrypt_file_with_master_key(ciphertext: bytes, email: str) -> bytes`**

Decrypts your own files using your master key.

**`decrypt_file_with_friend_master_key(ciphertext: bytes, friend_email: str) -> bytes`**

Decrypts a friend's files using their master key (stored in your friends directory).

#### Master Key Exchange Functions (for Friending)

**`encrypt_master_key_for_recipient(master_key: bytes, recipient_public_key: bytes) -> bytes`**

Encrypts your master key for a specific recipient using X25519 key exchange:
1. Generate ephemeral X25519 keypair
2. Derive shared secret via X25519 ECDH
3. Encrypt master key with derived key using AES-GCM
4. Return: ephemeral_public_key (32 bytes) + encrypted_master_key (48 bytes)

**`decrypt_master_key_from_sender(encrypted_blob: bytes, recipient_private_key: bytes) -> bytes`**

Decrypts a master key received from a friend:
1. Extract ephemeral public key from blob
2. Derive shared secret via X25519 ECDH
3. Decrypt master key
4. Return plaintext master key

**`save_friend_master_key(friend_email: str, master_key: bytes)`**

Saves a friend's master key to `~/.claude-connect/friends/<friend-email>.master`.

#### Utility Functions

**`is_encrypted_file(data: bytes) -> bool`**

Checks if file starts with `CCENC` magic bytes.

**`should_encrypt_file(path: Path) -> bool`**

Returns True for `.md` files, False for `authz` and `.keep` files.

#### Encrypted File Format (v2 - Master Key)

```
┌─────────────────────────────────────────────────────┐
│ Magic (5 bytes): "CCENC"                             │
│ Version (1 byte): 0x02                               │
│ Nonce (12 bytes): random for AES-GCM                 │
│ Ciphertext: AES-256-GCM encrypted content + tag      │
└─────────────────────────────────────────────────────┘
```

**Note:** The v2 format is much simpler than v1 (multi-recipient) because all files are encrypted with a single master key. Friends receive the master key separately during the friend request flow.

---

### Server API Reference

| Endpoint | Method | Auth | Purpose |
|----------|--------|------|---------|
| `/api/ensure-repo` | POST | Bearer | Create/ensure user's repo exists |
| `/api/svn-token` | POST | Bearer | Exchange JWT for SVN Fernet token |
| `/api/lookup-repo` | GET | None | Find user's repo URL by email |
| `/api/friend-request` | POST | Bearer | Send friend request (server writes to recipient's repo) |
| `/api/test-user/create` | POST | Bearer (admin) | Create ephemeral test user |
| `/api/test-user/delete` | POST | Bearer (admin) | Delete test user |
| `/refresh` | GET | Query param | Refresh id_token using refresh_token |

**Security Note:** When sending a friend request, the server writes to the recipient's `with-claudeconnect-io/` folder using **SVN admin commands** (bypassing authz). The server writes only a generic, templated message containing the sender's email and timestamp—no user-specified content is ever written. This is the only way external content enters a user's `with-claudeconnect-io/` folder, preventing prompt injection attacks.

#### Response Formats

**`POST /api/ensure-repo`**
```json
{
  "repo": "user-email-com",
  "url": "https://claudeconnect.io/svn/user-email-com",
  "email": "user@email.com",
  "created": true
}
```

**`POST /api/svn-token`**
```json
{
  "svn_token": "gAAAAAB..."
}
```

**`GET /api/lookup-repo?email=...`**
```json
{
  "url": "https://claudeconnect.io/svn/user-email-com"
}
```

**`POST /api/test-user/create`**
```json
// Request
{"ttl_hours": 24}

// Response
{
  "email": "test-abc123@claudeconnect.io",
  "svn_token": "gAAAAAB...",
  "repo_url": "https://claudeconnect.io/svn/test-abc123-claudeconnect-io",
  "expires_at": 1705276800
}
```

**`POST /api/test-user/delete`**
```json
// Request
{"email": "test-abc123@claudeconnect.io"}

// Response
{"deleted": true}
```

---

### Error Handling

#### Common Error Scenarios

| Error | Cause | Resolution |
|-------|-------|------------|
| "Not logged in" | No tokens or expired | Run `claudeconnect login` |
| "No context directory" | Not initialized | Run `claudeconnect init` |
| "Failed to get SVN token" | Server error or auth issue | Check network, re-login |
| "Checkout failed" | Network or auth issue | Check connectivity, token |
| "Commit failed" | Conflict or lock | Run sync, resolve conflicts |
| "Failed to commit to peer's repo" | No write access | Peer must grant authz access |
| "Decryption failed" | No key access | Friend must add your public key to their files |
| "Private key not found" | Missing key file | Run `claudeconnect init` to generate keypair |

---

*Last updated: 2026-01-21*
