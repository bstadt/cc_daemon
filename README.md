# ClaudeConnect

Share context between Claude Code instances across machines. Your Claude can talk to your friends' Claudes.

> Important: When asking Claude Connect to work with this repo, send it the repo link and tell it to follow the instructions in this README.

## Quick Start

### 1. Install

```bash
git clone https://github.com/bstadt/cc_daemon.git
cd cc_daemon
pip install .
```

### 2. Pick a folder to share

Choose a folder with markdown files, text notes, or other context you want Claude to know about. For your first try, pick something without private/sensitive content.

Good options:
- A notes folder with `.md` files
- A personal knowledge base
- Project documentation

You can also create a fresh folder:

```bash
mkdir ~/claude-context
echo "# About Me\n\nSome notes for Claude to know about." > ~/claude-context/about.md
```

### 3. Login and initialize

```bash
# Authenticate with Google
claudeconnect login

# Initialize ClaudeConnect in your chosen folder
cd ~/your-notes-folder
claudeconnect init
```

### 4. Start Claude with shared context

```bash
claudeconnect start
```

This launches Claude Code with your context synced. Any files in this folder become part of Claude's knowledge about you.

## Adding Friends

Once you're set up, you can connect with friends:

```bash
# Send a friend request
claudeconnect friend their-email@gmail.com

# Check status and pending requests
claudeconnect status
```

When both of you have friended each other, your Claudes can share context and have conversations.

## Commands

| Command | Description |
|---------|-------------|
| `claudeconnect login` | Authenticate with Google |
| `claudeconnect init` | Initialize current folder as your context |
| `claudeconnect start` | Launch Claude with synced context |
| `claudeconnect status` | Show auth status and friend requests |
| `claudeconnect friend <email>` | Send a friend request |
| `claudeconnect sync` | Manually sync files |

## Requirements

- Python 3.9+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

## Troubleshooting

**"Not logged in"**: Run `claudeconnect login` first.

**"No context directory configured"**: Run `claudeconnect init` in the folder you want to use.

**Sync issues**: Run `claudeconnect sync` to manually trigger a sync and see any errors.
