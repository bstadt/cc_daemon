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
SYSTEM_DIR="$CONTEXT_DIR/claudeconnect/with-claudeconnect-io"
CONVERSATIONS_DIR="$CONTEXT_DIR/claudeconnect/conversations"

# Get email from config
EMAIL="you@example.com"
if [ -f "$HOME/.config/claudeconnect/tokens.json" ]; then
    EMAIL=$(grep -o '"email"[[:space:]]*:[[:space:]]*"[^"]*"' "$HOME/.config/claudeconnect/tokens.json" 2>/dev/null | cut -d'"' -f4)
fi

# Banner with sparkles
echo ""
echo -e " ${BLACK_BG}${CORAL}▐▛███▜▌${RESET} ${YELLOW}✦${RESET} ${BLACK_BG}${LIME}▐▛███▜▌${RESET}   ${WHITE}${BOLD}Claude Connect${RESET}"
echo -e "${CORAL}▝▜█████▛▘${RESET}  ${LIME}▝▜█████▛▘${RESET}  ${DIM}${EMAIL}${RESET}"
echo -e "  ${CORAL}▘▘ ▝▝${RESET}      ${LIME}▘▘ ▝▝${RESET}"
echo ""

# Box width
W=34

# Helper function to make boxes
make_box_header() {
    local title="$1"
    local title_len=${#title}
    local dashes=$((W - 5 - title_len))
    printf "┌─ %s " "$title"
    for ((i=0; i<dashes; i++)); do printf "─"; done
    printf "┐\n"
}

make_box_item() {
    local content="$1"
    local content_len=${#content}
    local max_len=$((W - 4))
    if [ $content_len -gt $max_len ]; then
        content="${content:0:$((max_len-3))}..."
        content_len=$max_len
    fi
    local padding=$((max_len - content_len))
    printf "│ %s" "$content"
    for ((i=0; i<padding; i++)); do printf " "; done
    printf " │\n"
}

make_box_footer() {
    printf "└"
    for ((i=0; i<W-2; i++)); do printf "─"; done
    printf "┘\n"
}

# Count friend requests (in with-claudeconnect-io directory)
FR_COUNT=0
FR_ITEMS=()
if [ -d "$SYSTEM_DIR" ]; then
    for f in "$SYSTEM_DIR"/friend-request-*.md 2>/dev/null; do
        [ -f "$f" ] || continue
        # Extract sender email from filename: friend-request-user-example-com.md -> user@example.com
        basename_f=$(basename "$f" .md)
        sender_sanitized="${basename_f#friend-request-}"
        # Convert sanitized email back to normal format (approximation)
        sender=$(echo "$sender_sanitized" | sed 's/-gmail-com$/@gmail.com/' | sed 's/-example-com$/@example.com/')
        if [ -n "$sender" ]; then
            FR_ITEMS+=("∙ $sender")
            ((FR_COUNT++))
        fi
    done
fi

# Check for accepted friend notifications
if [ -d "$CONVERSATIONS_DIR" ]; then
    for conv_dir in "$CONVERSATIONS_DIR"/with-*; do
        [ -d "$conv_dir" ] || continue
        accepted_file="$conv_dir/friend-accepted.md"
        if [ -f "$accepted_file" ]; then
            if grep -qi "friend-request-accepted" "$accepted_file" 2>/dev/null; then
                email_line=$(grep -E "From:|\\*\\*From\\*\\*:" "$accepted_file" 2>/dev/null | head -1)
                email_part=$(echo "$email_line" | cut -d: -f2 | tr -d ' *')
                username=$(echo "$email_part" | cut -d@ -f1)
                if [ -n "$username" ]; then
                    FR_ITEMS+=("∙ $username accepted your request!")
                    ((FR_COUNT++))
                fi
            fi
        fi
    done
fi

# Get recent conversations
CONV_ITEMS=()
if [ -d "$CONVERSATIONS_DIR" ]; then
    month_ago=$(date -v-30d +%s 2>/dev/null || date -d "30 days ago" +%s 2>/dev/null)
    for conv_dir in "$CONVERSATIONS_DIR"/with-*; do
        [ -d "$conv_dir" ] || continue
        dir_name=$(basename "$conv_dir")
        peer_name="${dir_name#with-}"
        # Convert to email format
        peer_email="${peer_name//-/.}"

        # Find most recent .md file (not friend-accepted.md)
        latest_file=""
        latest_time=0
        for f in "$conv_dir"/*.md; do
            [ -f "$f" ] || continue
            [ "$(basename "$f")" = "friend-accepted.md" ] && continue
            mtime=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)
            if [ "$mtime" -gt "$latest_time" ]; then
                latest_time=$mtime
                latest_file=$f
            fi
        done

        if [ -n "$latest_file" ] && [ "$latest_time" -gt "$month_ago" ]; then
            topic=$(grep "^\*\*Topic\*\*:" "$latest_file" 2>/dev/null | head -1 | cut -d: -f2 | tr -d ' ')
            username=$(echo "$peer_email" | cut -d@ -f1 | cut -d. -f1)
            if [ -n "$topic" ]; then
                CONV_ITEMS+=("∙ $username: $topic")
            else
                CONV_ITEMS+=("∙ $username")
            fi
        fi
    done
fi

# Print boxes
if [ ${#FR_ITEMS[@]} -gt 0 ] || [ ${#CONV_ITEMS[@]} -gt 0 ]; then
    # Create friend requests box
    if [ ${#FR_ITEMS[@]} -gt 0 ]; then
        echo -n " "
        make_box_header "FRIEND REQUESTS ($FR_COUNT)"
        for item in "${FR_ITEMS[@]:0:5}"; do
            echo -n " "
            make_box_item "$item"
        done
        echo -n " "
        make_box_footer
    fi

    # Create conversations box
    if [ ${#CONV_ITEMS[@]} -gt 0 ]; then
        echo -n " "
        make_box_header "CONVERSATIONS"
        for item in "${CONV_ITEMS[@]:0:5}"; do
            echo -n " "
            make_box_item "$item"
        done
        echo -n " "
        make_box_footer
    fi

    echo ""
fi
