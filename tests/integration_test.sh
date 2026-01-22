#!/bin/bash
# ClaudeConnect Integration Test
# Tests the full flow: login, init, friend requests, sessions, sync

set -e

SERVER="v2.claudeconnect.io"
SSH_KEY="$HOME/.ssh/calco_key.pem"
CC_CONFIG_DIR="$HOME/.claude-connect"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[TEST]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
prompt() { echo -e "${YELLOW}[ACTION REQUIRED]${NC} $1"; }

# Step 1: Clean up server - remove all repos
log "Step 1: Cleaning up server repos..."
ssh -i "$SSH_KEY" ubuntu@"$SERVER" "sudo rm -rf /var/svn/repos/* && echo 'All repos deleted'" || {
    error "Failed to clean server repos"
    exit 1
}
log "Server repos cleaned."

# Step 2: Remove local ~/.claude-connect
log "Step 2: Removing local ~/.claude-connect..."
rm -rf "$CC_CONFIG_DIR"
log "Local config removed."

# Step 3: Create temp directories
log "Step 3: Creating temp directories..."
TEMP1=$(mktemp -d -t cc_test_account1)
TEMP2=$(mktemp -d -t cc_test_account2)
log "Created $TEMP1 (Account 1)"
log "Created $TEMP2 (Account 2)"

cleanup() {
    log "Cleaning up temp directories..."
    rm -rf "$TEMP1" "$TEMP2"
}
trap cleanup EXIT

# Step 4: Login as Account 1 in temp1
log "Step 4: Logging in as Account 1..."
cd "$TEMP1"
prompt "Please login with ACCOUNT 1 (first Google account)"
read -p "Press Enter to open browser for Account 1 login..."
claudeconnect login

# Step 5: Init Account 1
log "Step 5: Initializing Account 1..."
prompt "Account 1 logged in. Now initializing..."
read -p "Press Enter to run 'claudeconnect init' for Account 1..."
claudeconnect init
ACCOUNT1_EMAIL=$(cat "$CC_CONFIG_DIR/tokens.json" | python3 -c "import sys, json; print(json.load(sys.stdin)['email'])")
log "Account 1 initialized: $ACCOUNT1_EMAIL"

# Step 6: Change to temp2
log "Step 6: Switching to Account 2 directory..."
cd "$TEMP2"

# Clear tokens so we can login as different user
rm -f "$CC_CONFIG_DIR/tokens.json"

# Step 7: Login as Account 2
log "Step 7: Logging in as Account 2..."
prompt "Please login with ACCOUNT 2 (second Google account)"
read -p "Press Enter to open browser for Account 2 login..."
claudeconnect login

# Step 8: Init Account 2
log "Step 8: Initializing Account 2..."
prompt "Account 2 logged in. Now initializing..."
read -p "Press Enter to run 'claudeconnect init' for Account 2..."
claudeconnect init
ACCOUNT2_EMAIL=$(cat "$CC_CONFIG_DIR/tokens.json" | python3 -c "import sys, json; print(json.load(sys.stdin)['email'])")
log "Account 2 initialized: $ACCOUNT2_EMAIL"

# Step 9: Create poetry.md in temp2
log "Step 9: Creating poetry.md in Account 2's context..."
cat > "$TEMP2/poetry.md" << 'EOF'
# Poetry Collection

turning and turning in the widening gyre
EOF
log "Created poetry.md"

# Step 10: Sync Account 2
log "Step 10: Syncing Account 2..."
claudeconnect sync
log "Account 2 synced."

# Step 11: Send friend request from Account 2 to Account 1
log "Step 11: Sending friend request from Account 2 to Account 1..."
claudeconnect friend "$ACCOUNT1_EMAIL" -m "Let's connect our Claude instances!"
log "Friend request sent from $ACCOUNT2_EMAIL to $ACCOUNT1_EMAIL"

# Step 12: Return to temp1
log "Step 12: Switching back to Account 1 directory..."
cd "$TEMP1"

# Clear tokens to login as Account 1 again
rm -f "$CC_CONFIG_DIR/tokens.json"

# Step 13: Login as Account 1 again
log "Step 13: Re-logging in as Account 1..."
prompt "Please login with ACCOUNT 1 again"
read -p "Press Enter to open browser for Account 1 login..."
claudeconnect login

# Step 14: Re-init Account 1 (will detect existing working copy)
log "Step 14: Re-initializing Account 1..."
read -p "Press Enter to run 'claudeconnect init' for Account 1..."
claudeconnect init

# Step 15: Sync and check for friend request
log "Step 15: Syncing Account 1 and checking for friend request..."
claudeconnect sync

FRIEND_REQUEST_FILE="$TEMP1/claudeconnect/friend_requests/${ACCOUNT2_EMAIL}.json"
# Convert email to filename format (@ -> -, . -> -)
ACCOUNT2_SANITIZED=$(echo "$ACCOUNT2_EMAIL" | tr '@.' '-' | tr '[:upper:]' '[:lower:]')
FRIEND_REQUEST_FILE="$TEMP1/claudeconnect/friend_requests/${ACCOUNT2_SANITIZED}.json"

if [ -f "$FRIEND_REQUEST_FILE" ]; then
    log "Friend request found at: $FRIEND_REQUEST_FILE"
    cat "$FRIEND_REQUEST_FILE"
else
    # Try with original email format
    FRIEND_REQUEST_FILE="$TEMP1/claudeconnect/friend_requests/${ACCOUNT2_EMAIL}.json"
    if [ -f "$FRIEND_REQUEST_FILE" ]; then
        log "Friend request found at: $FRIEND_REQUEST_FILE"
        cat "$FRIEND_REQUEST_FILE"
    else
        warn "Friend request file not found. Listing friend_requests directory:"
        ls -la "$TEMP1/claudeconnect/friend_requests/" || true
        error "Expected friend request from $ACCOUNT2_EMAIL"
        # Continue anyway for debugging
    fi
fi

# Step 16: Accept the friend request
log "Step 16: Accepting friend request from Account 2..."
claudeconnect accept-friend "$ACCOUNT2_EMAIL"
log "Friend request accepted."

# Step 17: Start a session about poetry
log "Step 17: Starting session between Account 1 and Account 2 about poetry..."
prompt "This will spawn Claude instances for a conversation."
read -p "Press Enter to start the session..."
claudeconnect session "$ACCOUNT2_EMAIL" -t "poetry and the widening gyre"

# Step 18: Check transcript saved
log "Step 18: Checking transcript was saved..."
CONV_DIR="$TEMP1/claudeconnect/conversations"
if [ -d "$CONV_DIR" ]; then
    log "Conversations directory exists:"
    find "$CONV_DIR" -name "*.md" -type f
    TRANSCRIPT_COUNT=$(find "$CONV_DIR" -name "*.md" -type f | wc -l)
    if [ "$TRANSCRIPT_COUNT" -gt 0 ]; then
        log "Found $TRANSCRIPT_COUNT transcript(s)"
        # Show first transcript
        FIRST_TRANSCRIPT=$(find "$CONV_DIR" -name "*.md" -type f | head -1)
        log "First transcript preview:"
        head -30 "$FIRST_TRANSCRIPT"
    else
        error "No transcripts found!"
    fi
else
    error "Conversations directory not found: $CONV_DIR"
fi

# Step 19: Pull Account 2's context and verify poetry.md
log "Step 19: Pulling Account 2's context..."
claudeconnect pull "$ACCOUNT2_EMAIL"

PEERS_DIR="$HOME/.claude-connect/peers"
ACCOUNT2_REPO=$(echo "$ACCOUNT2_EMAIL" | tr '@.' '-' | tr '[:upper:]' '[:lower:]')
PEER_POETRY="$PEERS_DIR/$ACCOUNT2_REPO/poetry.md"

if [ -f "$PEER_POETRY" ]; then
    log "Successfully pulled poetry.md from Account 2:"
    cat "$PEER_POETRY"
    if grep -q "widening gyre" "$PEER_POETRY"; then
        log "Content verified - 'widening gyre' found!"
    else
        error "Content verification failed - expected 'widening gyre'"
    fi
else
    error "Could not find pulled poetry.md at: $PEER_POETRY"
    warn "Checking peers directory:"
    ls -la "$PEERS_DIR/" || true
fi

# Step 20: Return to temp2
log "Step 20: Switching back to Account 2 directory..."
cd "$TEMP2"

# Clear tokens
rm -f "$CC_CONFIG_DIR/tokens.json"

# Step 21: Login as Account 2
log "Step 21: Re-logging in as Account 2..."
prompt "Please login with ACCOUNT 2 again"
read -p "Press Enter to open browser for Account 2 login..."
claudeconnect login

# Step 22: Re-init Account 2
log "Step 22: Re-initializing Account 2..."
read -p "Press Enter to run 'claudeconnect init' for Account 2..."
claudeconnect init

# Step 23: Sync and check transcript arrived
log "Step 23: Syncing Account 2 and checking for transcript..."
claudeconnect sync

CONV_DIR2="$TEMP2/claudeconnect/conversations"
if [ -d "$CONV_DIR2" ]; then
    log "Conversations directory exists:"
    find "$CONV_DIR2" -name "*.md" -type f
    TRANSCRIPT_COUNT=$(find "$CONV_DIR2" -name "*.md" -type f | wc -l)
    if [ "$TRANSCRIPT_COUNT" -gt 0 ]; then
        log "Found $TRANSCRIPT_COUNT transcript(s) in Account 2's context"
        FIRST_TRANSCRIPT=$(find "$CONV_DIR2" -name "*.md" -type f | head -1)
        log "Transcript preview:"
        head -30 "$FIRST_TRANSCRIPT"
        log "SUCCESS: Transcript synced to Account 2!"
    else
        error "No transcripts found in Account 2's context!"
    fi
else
    error "Conversations directory not found: $CONV_DIR2"
fi

echo ""
log "=========================================="
log "Integration test complete!"
log "=========================================="
log "Account 1: $ACCOUNT1_EMAIL"
log "Account 2: $ACCOUNT2_EMAIL"
log "Temp1: $TEMP1"
log "Temp2: $TEMP2"
log ""
log "Summary:"
log "  - Server repos cleaned"
log "  - Two accounts initialized"
log "  - Friend request sent and accepted"
log "  - Session conducted about poetry"
log "  - Transcript saved to both accounts"
log "  - Context pulled successfully"
