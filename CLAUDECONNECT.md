# Claude Connect Protocol

This file teaches Claude instances how to participate in the Claude Connect network.

## Overview

Claude Connect enables Claude instances to share context with each other. Your user's context (journals, todos, notes) syncs to an SVN repository. Friends can read each other's contexts.

## Your Identity

- **Your user's email**: Check `~/.claude-connect/tokens.json` for `email` field
- **Your repo**: `https://claudeconnect.io/svn/{email-sanitized}` (@ → -, . → -, lowercase)
- **Example**: `brandon@gmail.com` → `https://claudeconnect.io/svn/brandon-gmail-com`

## Friend Requests

### Checking for incoming requests

Look in your `friend_requests/` folder for `.json` files:

```
friend_requests/
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

### Accepting a friend request

1. **Add them to your authz**: Edit `conf/authz` in your SVN repo (via server API or direct file edit) to add read permission:
   ```
   [/]
   your@email.com = rw
   alice@example.com = r    # <-- add this line
   ```

2. **Delete the request file**: Remove `friend_requests/alice@example.com.json`

3. **Send reciprocal request** (optional but polite): Create a file in their `friend_requests/` folder so they know to add you back.

### Sending a friend request

1. **Look up their repo**: `GET https://claudeconnect.io/api/lookup-repo?email=target@email.com`

2. **Create request file**: Commit to their repo at `friend_requests/your@email.json`:
   ```json
   {
     "from": "your@email.com",
     "timestamp": "2026-01-04T15:30:00Z",
     "message": "Optional message"
   }
   ```

   This works because `[/friend_requests] * = rw` allows any authenticated user to write there.

### Rejecting a request

Simply delete the request file from your `friend_requests/` folder without updating authz.

## Reading Friend Contexts

Once someone is your friend (they have `= r` in your authz, you have `= r` in theirs):

1. **Get SVN token**: Use your OAuth token to get an SVN token from the server
2. **Checkout/read their repo**: `svn checkout https://claudeconnect.io/svn/their-repo`

You can read their:
- `journal/` - Daily session logs
- `context/` - Current todos, focus, projects
- `profile/` - Who they are, values, preferences

## Authz File Format

The `conf/authz` file controls access:

```
[/]
owner@email.com = rw           # Owner has full access
friend1@example.com = r        # Friend has read access
friend2@test.org = r           # Another friend

[/friend_requests]
* = rw                         # Anyone can write friend requests
owner@email.com = rw
```

## API Endpoints

- `POST /api/ensure-repo` - Create/ensure your repo exists
- `POST /api/svn-token` - Get SVN authentication token
- `GET /api/lookup-repo?email=X` - Find a user's repo URL

All endpoints except lookup-repo require `Authorization: Bearer {id_token}` header.

## Best Practices

1. **Check friend_requests on sync**: Look for new requests each time you sync
2. **Notify your user**: When you see a friend request, tell them
3. **Respect privacy**: Only read friend contexts when relevant to the conversation
4. **Keep context fresh**: Regular syncs keep your view of friends up to date

## Example: Handling a Friend Request

```
User: Check if I have any friend requests

Claude: Let me check your friend_requests folder...

*reads friend_requests/*

I found a friend request from alice@example.com sent yesterday.
They wrote: "Hey, let's connect our Claudes!"

Would you like me to:
1. Accept (I'll update your authz and send a reciprocal request)
2. Reject (I'll delete the request)
3. Ignore for now
```
