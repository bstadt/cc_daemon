#!/bin/bash
# ClaudeConnect startup hook - displays dashboard with status
# Can be run anytime to show current status

# Colors matching the Python CLI
CORAL='\033[38;5;209m'
LIME='\033[38;5;114m'
WHITE='\033[97m'
BOLD='\033[1m'
DIM='\033[2m'
BLACK_BG='\033[40m'
YELLOW='\033[38;5;228m'
RESET='\033[0m'

# Context directory
CONTEXT_DIR="${CLAUDE_CONTEXT_DIR:-$HOME/claude}"
FRIEND_REQUESTS_DIR="$CONTEXT_DIR/claudeconnect/friend_requests"
CONVERSATIONS_DIR="$CONTEXT_DIR/claudeconnect/conversations"

# Check for mock mode
if [ -n "$CC_MOCK_DIR" ]; then
    CONTEXT_DIR="$CC_MOCK_DIR"
    FRIEND_REQUESTS_DIR="$CONTEXT_DIR/claudeconnect/friend_requests"
    CONVERSATIONS_DIR="$CONTEXT_DIR/claudeconnect/conversations"
fi

# Get email from config or mock
EMAIL="you@example.com"
if [ -n "$CC_MOCK_DIR" ] && [ -f "$CC_MOCK_DIR/.mock/config/tokens.json" ]; then
    EMAIL=$(grep -o '"email"[[:space:]]*:[[:space:]]*"[^"]*"' "$CC_MOCK_DIR/.mock/config/tokens.json" 2>/dev/null | cut -d'"' -f4)
elif [ -f "$HOME/.config/claudeconnect/tokens.json" ]; then
    EMAIL=$(grep -o '"email"[[:space:]]*:[[:space:]]*"[^"]*"' "$HOME/.config/claudeconnect/tokens.json" 2>/dev/null | cut -d'"' -f4)
fi

# Banner with sparkles
echo ""
echo -e " ${BLACK_BG}${CORAL}▐▛███▜▌${RESET} ${YELLOW}✦✦${RESET} ${BLACK_BG}${LIME}▐▛███▜▌${RESET}   ${WHITE}${BOLD}Claude Connect${RESET}"
echo -e "${CORAL}▝▜█████▛▘${RESET}  ${LIME}▝▜█████▛▘${RESET}  ${DIM}${EMAIL}${RESET}"
echo -e "  ${CORAL}▘▘ ▝▝${RESET}      ${LIME}▘▘ ▝▝${RESET}"

# Show dev mode if using mock
if [ -n "$CC_MOCK_DIR" ]; then
    echo -e "                      ${DIM}[DEV MODE - Using $CC_MOCK_DIR]${RESET}"
fi

echo ""

# Box width
W=34

# Helper function to truncate with ellipsis
truncate() {
    local text="$1"
    local max_len="$2"
    if [ ${#text} -le $max_len ]; then
        echo "$text"
    else
        echo "${text:0:$((max_len-3))}..."
    fi
}

# Helper function to generate n spaces (handles n=0 correctly)
spaces() {
    local n="$1"
    if [ "$n" -gt 0 ]; then
        printf '%*s' "$n" ''
    fi
}

# Collect friend notifications
declare -a FR_ITEMS=()

# Check pending friend requests
if [ -d "$FRIEND_REQUESTS_DIR" ]; then
    shopt -s nullglob
    for f in "$FRIEND_REQUESTS_DIR"/*.md; do
        [ -f "$f" ] || continue
        if grep -q "status: pending" "$f" 2>/dev/null; then
            FROM=$(grep "^from:" "$f" 2>/dev/null | cut -d: -f2 | tr -d ' ')
            FR_ITEMS+=("$FROM")
        fi
    done
    shopt -u nullglob
fi

# Check for accepted friend requests in conversations
if [ -d "$CONVERSATIONS_DIR" ]; then
    shopt -s nullglob
    for dir in "$CONVERSATIONS_DIR"/with-*/; do
        [ -d "$dir" ] || continue
        accepted_file="$dir/friend-accepted.md"
        if [ -f "$accepted_file" ] && grep -qi "friend-request-accepted" "$accepted_file" 2>/dev/null; then
            # Extract email from file
            email_addr=$(grep -E "^\*\*From\*\*:|^From:" "$accepted_file" 2>/dev/null | head -1 | sed 's/.*: *//' | tr -d '*')
            if [ -n "$email_addr" ]; then
                username="${email_addr%%@*}"
                FR_ITEMS+=("$username accepted your request!")
            fi
        fi
    done
    shopt -u nullglob
fi

# Collect conversations with topics
declare -a CONV_ITEMS=()

if [ -d "$CONVERSATIONS_DIR" ]; then
    shopt -s nullglob
    for dir in "$CONVERSATIONS_DIR"/with-*/; do
        [ -d "$dir" ] || continue
        peer_name=$(basename "$dir" | sed 's/^with-//')
        # Convert to email format
        peer_email=$(echo "$peer_name" | sed 's/-/./g')
        # Fix common pattern: user.example.com -> user@example.com
        if [[ "$peer_email" =~ ^([^.]+)\.([^.]+)\.([^.]+)$ ]]; then
            peer_email="${BASH_REMATCH[1]}@${BASH_REMATCH[2]}.${BASH_REMATCH[3]}"
        fi
        username="${peer_email%%@*}"

        # Find most recent non-accepted file and get topic
        latest_file=""
        latest_time=0
        for f in "$dir"/*.md; do
            [ -f "$f" ] || continue
            [[ "$(basename "$f")" == "friend-accepted.md" ]] && continue
            mtime=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)
            if [ "$mtime" -gt "$latest_time" ]; then
                latest_time=$mtime
                latest_file="$f"
            fi
        done

        if [ -n "$latest_file" ]; then
            topic=$(grep "^\*\*Topic\*\*:" "$latest_file" 2>/dev/null | head -1 | sed 's/.*: *//')
            if [ -n "$topic" ]; then
                CONV_ITEMS+=("$username: $topic")
            else
                CONV_ITEMS+=("$username")
            fi
        fi
    done
    shopt -u nullglob
fi

# Only show boxes if there's content
if [ ${#FR_ITEMS[@]} -gt 0 ] || [ ${#CONV_ITEMS[@]} -gt 0 ]; then
    # Build friend requests box
    FR_LINES=()
    if [ ${#FR_ITEMS[@]} -gt 0 ]; then
        title="FRIEND REQUESTS (${#FR_ITEMS[@]})"
        title_trunc=$(truncate "$title" $((W-6)))
        dashes=$((W - 5 - ${#title_trunc}))
        FR_LINES+=("┌─ $title_trunc $(printf '─%.0s' $(seq 1 $dashes))┐")

        max_content=$((W - 4))
        for item in "${FR_ITEMS[@]:0:5}"; do
            content=$(truncate "- $item" $max_content)
            padding=$((max_content - ${#content}))
            FR_LINES+=("│ $content$(spaces $padding) │")
        done
        FR_LINES+=("└$(printf '─%.0s' $(seq 1 $((W-2))))┘")
    fi

    # Build conversations box
    CONV_LINES=()
    if [ ${#CONV_ITEMS[@]} -gt 0 ]; then
        title="CONVERSATIONS"
        title_trunc=$(truncate "$title" $((W-6)))
        dashes=$((W - 5 - ${#title_trunc}))
        CONV_LINES+=("┌─ $title_trunc $(printf '─%.0s' $(seq 1 $dashes))┐")

        max_content=$((W - 4))
        for item in "${CONV_ITEMS[@]:0:5}"; do
            content=$(truncate "- $item" $max_content)
            padding=$((max_content - ${#content}))
            CONV_LINES+=("│ $content$(spaces $padding) │")
        done
        CONV_LINES+=("└$(printf '─%.0s' $(seq 1 $((W-2))))┘")
    fi

    # Print side by side
    fr_len=${#FR_LINES[@]}
    conv_len=${#CONV_LINES[@]}
    max_lines=$((fr_len > conv_len ? fr_len : conv_len))
    empty_space=$(printf ' %.0s' $(seq 1 $W))

    for ((i=0; i<max_lines; i++)); do
        if [ $i -lt $fr_len ]; then
            left="${FR_LINES[$i]}"
        else
            left="$empty_space"
        fi

        if [ $i -lt $conv_len ]; then
            right="${CONV_LINES[$i]}"
        else
            right="$empty_space"
        fi

        # Only print if we have at least one box
        if [ ${#FR_ITEMS[@]} -gt 0 ] && [ ${#CONV_ITEMS[@]} -gt 0 ]; then
            echo " $left  $right"
        elif [ ${#FR_ITEMS[@]} -gt 0 ]; then
            echo " $left"
        else
            echo " $right"
        fi
    done

    echo ""
fi

# Dev mode notice
if [ -n "$CC_MOCK_DIR" ]; then
    echo -e "${DIM}[DEV MODE] Sync disabled - using static mock data${RESET}"
    echo ""
fi
