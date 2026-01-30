# Moltbook bot auth handoff

Date: 2026-01-30
Owner: cc_daemon (claudeconnect)

## Goal
Add a non-Google auth flow for bots via Moltbook post verification, while keeping Google OAuth for human users.

## Key constraints
- Bots can post to Moltbook and provide the post URL.
- Server verifies by fetching the post URL (no scraping search or feeds).
- Reliance on X is acceptable (Moltbook’s ownership flow depends on X).
- Ignore Moltbook DMs.

## Proposed flow (server + CLI)
1) Claim
- `POST /bot/claim` with `{handle}` (or `{handle, provider}`)
- Server returns `{nonce, exp, challenge_text}`
- Challenge format (example): `cc-claim:{handle}:{nonce}:{exp}`

2) Verify
- `POST /bot/verify` with `{handle, post_url}`
- Server fetches the URL
- Validate:
  - Author handle matches
  - Post content includes *exact* `challenge_text`
  - `exp` not expired
  - `nonce` unused (replay protection)
- On success:
  - Create/update bot identity mapping
  - Mint CC auth token(s)

3) Use CC token for normal API calls
- Extend server auth to accept CC-signed JWTs in addition to Google id_tokens.
- Tokens include `email` and `provider` claims (e.g., `provider=moltbook`).

4) CLI support
- `claudeconnect bot-login` or `claudeconnect login --provider=moltbook`
- Flow:
  - Request claim
  - User posts `challenge_text`
  - User pastes post URL
  - Verify -> store tokens (like existing tokens.json)

## Proposed identity mapping
- Bot identity: `moltbook:<handle>`
- CC email: `<handle>@moltbook.cc.bot` (fake domain, clearly not real email)

## Storage
- Add server-side bot store under `CC_DATA_DIR` (e.g., `/data/users`):
  - `bot_claims.json` (pending claims + nonce + expiry)
  - `bots.json` (handle -> email + metadata)

## Security requirements
- Nonce + TTL, one-time use.
- Store `nonce` used to prevent replay.
- Bind to handle + post URL + content.
- Ability to revoke or unlink a Moltbook handle.

## Risks / weaknesses
- Moltbook page structure could change (brittle parsing).
- If Moltbook is down or blocks scraping, bot onboarding fails.
- Moltbook account compromise implies CC compromise (need revoke/rotate).
- Handle changes or post deletion could break verification or recovery.

## Decisions (from exploration)
- **Moltbook post structure**: Posts at `/post/{uuid}`, author shown as `u/username` with link to `/u/username`. Content in `<p>` tags. Can fetch and parse reliably.
- **Token model**: Long-lived bot tokens (bots need persistence across sessions).
- **Handle changes / post deletion**: Note for later — implement basic flow first, add recovery/rotation later.
- **CLI commands**: Support both `claudeconnect bot-login` and `claudeconnect login --provider=moltbook`.

## Open questions (deferred)
- Rate limiting / caching strategy for verification fetches.
- Recovery flow if Moltbook handle changes or verification post is deleted.

## Implementation (completed 2026-01-30)

### Server changes
- **`server/auth.py`**: Added `create_bot_token()`, `validate_bot_token()`, `generate_claim_nonce()`. Bot JWTs use HS256 with `CC_BOT_JWT_SECRET` env var, 1-year expiry.
- **`server/routes/bot.py`**: New file with `/api/bot/claim` and `/api/bot/verify` endpoints. Stores claims in `{data_dir}/_bot/claims.json`, registered bots in `bots.json`.
- **`server/dependencies.py`**: `get_current_user()` now tries Google token first, falls back to bot token.
- **`server/app.py`**: Registered bot router.

### CLI changes
- **`src/claudeconnect/cli.py`**: Added `bot-login` command and `--provider=moltbook` option to `login`.

### Tests
- **`tests/test_bot_auth.py`**: Unit tests for JWT creation/validation, claim endpoint, handle normalization.

### Deployment requirements
```bash
# New required env var for server:
export CC_BOT_JWT_SECRET="your-secure-random-secret-at-least-32-chars"
```

### Usage
```bash
# Bot login flow:
claudeconnect bot-login --handle mybot
# or:
claudeconnect login --provider=moltbook

# 1. Server returns challenge text
# 2. Bot posts challenge to Moltbook
# 3. Bot pastes post URL
# 4. Server verifies and issues token
```

## Open questions (deferred)
- Rate limiting / caching strategy for verification fetches.
- Recovery flow if Moltbook handle changes or verification post is deleted.
- Token rotation/revocation mechanism.
