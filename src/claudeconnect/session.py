"""Session management for Claude Connect.

Handles peer context pulling, conversation spawning, and transcript commits.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from .config import get_config, get_tokens, Tokens
from .svn_ops import SvnClient, SvnError, email_to_repo_name, repo_url_for_email


# Cache directory for peer contexts
PEERS_DIR = Path.home() / ".claude-connect" / "peers"
SERVER_URL = "http://3.142.232.180"


def get_svn_token(id_token: str) -> str | None:
    """Exchange Google JWT for SVN Fernet token."""
    try:
        response = httpx.post(
            f"{SERVER_URL}/api/svn-token",
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        return response.json().get("svn_token")
    except Exception:
        return None


def lookup_repo(email: str) -> str | None:
    """Look up a user's repo URL by email."""
    try:
        response = httpx.get(
            f"{SERVER_URL}/api/lookup-repo",
            params={"email": email},
            timeout=30,
        )
        if response.status_code != 200:
            return None
        return response.json().get("url")
    except Exception:
        return None


def pull_peer_context(peer_email: str, svn_token: str, our_email: str) -> Path | None:
    """
    Pull or update a peer's context to local cache.

    Args:
        peer_email: The peer's email address
        svn_token: Fernet SVN token for authentication
        our_email: Our email (used as SVN username)

    Returns:
        Path to the peer's cached context, or None on failure.
    """
    peer_name = email_to_repo_name(peer_email)
    peer_dir = PEERS_DIR / peer_name
    peer_repo_url = repo_url_for_email(peer_email)

    # Ensure peers directory exists
    PEERS_DIR.mkdir(parents=True, exist_ok=True)

    svn = SvnClient(peer_dir, peer_repo_url, svn_token, our_email)

    if peer_dir.exists() and svn.is_working_copy():
        # Update existing checkout
        print(f"  Updating {peer_email}'s context...")
        try:
            updated = svn.update()
            if updated:
                print(f"  Pulled {len(updated)} updates")
            return peer_dir
        except SvnError as e:
            print(f"  Update failed: {e}")
            return None
    else:
        # Fresh checkout
        print(f"  Checking out {peer_email}'s context...")
        peer_dir.mkdir(parents=True, exist_ok=True)

        try:
            svn.checkout()
            print(f"  Checked out to {peer_dir}")
            return peer_dir
        except SvnError as e:
            print(f"  Checkout failed: {e}")
            return None


def generate_session_prompt(
    our_context_dir: Path,
    peer_context_dir: Path,
    our_email: str,
    peer_email: str,
    topic: Optional[str] = None,
) -> str:
    """
    Generate the system prompt for a session conversation.

    The prompt instructs Claude to simulate a conversation between two
    contextualized instances, using the provided context files.
    """
    our_name = our_email.split("@")[0]
    peer_name = peer_email.split("@")[0]

    prompt = f"""You are facilitating a conversation between two contextualized Claude instances.

## Participants

**{our_name}'s Claude** (initiator):
- Context directory: {our_context_dir}
- Email: {our_email}
- Read their CLAUDE.md and relevant context files to understand their perspective.

**{peer_name}'s Claude** (peer):
- Context directory: {peer_context_dir}
- Email: {peer_email}
- Read their CLAUDE.md and relevant context files to understand their perspective.

## Instructions

1. First, read both CLAUDE.md files to understand each person's context system, values, and current focus.

2. Then facilitate a natural conversation between the two instances. Each instance should:
   - Speak from their person's perspective and values
   - Reference relevant context from their files
   - Be helpful, curious, and constructive

3. Format the conversation as a transcript:
   ```
   **{our_name}'s Claude**: [message]

   **{peer_name}'s Claude**: [response]
   ```

4. The conversation should be substantive - 5-10 exchanges minimum.

5. End the conversation naturally when it reaches a good stopping point.

"""
    if topic:
        prompt += f"## Topic\n\nThe initiator wants to discuss: {topic}\n\n"
    else:
        prompt += "## Topic\n\nStart with a natural greeting and see where the conversation goes based on current interests/projects.\n\n"

    prompt += "Begin the conversation now."

    return prompt


def create_transcript_header(
    our_email: str,
    peer_email: str,
    session_id: str,
    topic: Optional[str] = None,
) -> str:
    """Create the markdown header for a conversation transcript."""
    timestamp = datetime.now().isoformat()

    header = f"""# Conversation: {our_email.split('@')[0]} <-> {peer_email.split('@')[0]}

**Session ID**: {session_id}
**Date**: {timestamp}
**Initiated by**: {our_email}
**Participants**: {our_email}, {peer_email}
"""
    if topic:
        header += f"**Topic**: {topic}\n"

    header += "\n---\n\n"
    return header


async def run_session(
    peer_email: str,
    topic: Optional[str] = None,
    max_turns: int = 20,
) -> tuple[bool, str | None]:
    """
    Run a conversation session with a peer.

    Args:
        peer_email: The peer's email address
        topic: Optional conversation topic
        max_turns: Maximum conversation turns

    Returns:
        Tuple of (success, transcript_path or error message)
    """
    from .cli import get_valid_token, get_svn_token as cli_get_svn_token

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Get SVN token
    svn_token = cli_get_svn_token(tokens.id_token)
    if not svn_token:
        return False, "Failed to get SVN token"

    # Pull peer's context
    print(f"\nPreparing session with {peer_email}...")
    peer_context_dir = pull_peer_context(peer_email, svn_token, our_email)
    if not peer_context_dir:
        return False, f"Failed to pull {peer_email}'s context"

    # Generate session ID
    session_id = datetime.now().strftime("%Y-%m-%d") + "_" + uuid4().hex[:8]

    # Create the prompt
    system_prompt = generate_session_prompt(
        our_context_dir,
        peer_context_dir,
        our_email,
        peer_email,
        topic,
    )

    # Run Claude with the prompt
    print("\nStarting conversation...")

    try:
        # Run claude with --print for non-interactive output
        # Use --add-dir to give access to peer's context
        # Pass prompt via stdin (long prompts can't go as arguments)
        result = subprocess.run(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--add-dir", str(peer_context_dir),
            ],
            input=system_prompt,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=our_context_dir,  # Run from our context
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            return False, f"Claude failed: {error_msg}"

        conversation = result.stdout

    except subprocess.TimeoutExpired:
        return False, "Conversation timed out"
    except FileNotFoundError:
        return False, "Claude CLI not found"

    # Create transcript
    header = create_transcript_header(our_email, peer_email, session_id, topic)
    transcript = header + conversation

    # Create conversation directories if needed
    our_conv_dir = our_context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
    our_conv_dir.mkdir(parents=True, exist_ok=True)

    # Save transcript locally (in our context)
    transcript_filename = f"{session_id}.md"
    our_transcript_path = our_conv_dir / transcript_filename
    our_transcript_path.write_text(transcript)
    print(f"\nSaved transcript: {our_transcript_path}")

    # Commit to our repo
    print("\nCommitting to your repo...")
    our_svn = SvnClient(our_context_dir, repo_url_for_email(our_email), svn_token, our_email)
    try:
        our_svn.add(our_transcript_path.relative_to(our_context_dir), parents=True)
        our_svn.commit(f"Session with {peer_email}: {session_id}")
        print("  Committed to your repo")
    except SvnError as e:
        print(f"  Warning: Failed to commit to your repo: {e}")

    # Commit to peer's repo
    print(f"\nCommitting to {peer_email}'s repo...")
    peer_conv_dir = peer_context_dir / "claudeconnect" / f"with-{email_to_repo_name(our_email)}"
    peer_conv_dir.mkdir(parents=True, exist_ok=True)
    peer_transcript_path = peer_conv_dir / transcript_filename
    peer_transcript_path.write_text(transcript)

    peer_svn = SvnClient(peer_context_dir, repo_url_for_email(peer_email), svn_token, our_email)
    try:
        peer_svn.add(peer_transcript_path.relative_to(peer_context_dir), parents=True)
        peer_svn.commit(f"Session with {our_email}: {session_id}")
        print(f"  Committed to {peer_email}'s repo")
    except SvnError as e:
        print(f"  Warning: Failed to commit to peer's repo: {e}")
        print(f"  (This is expected if you don't have write access to their with-{email_to_repo_name(our_email)} folder)")

    return True, str(our_transcript_path)


def generate_instance_prompt(
    context_dir: Path,
    my_email: str,
    peer_email: str,
    topic: Optional[str] = None,
    is_initiator: bool = True,
) -> str:
    """
    Generate system prompt for one Claude instance in a dual-instance conversation.

    Each instance only sees their own user's context and knows they're talking
    to the other user's Claude instance.
    """
    my_name = my_email.split("@")[0]
    peer_name = peer_email.split("@")[0]

    prompt = f"""You are {my_name}'s Claude instance, participating in a ClaudeConnect conversation with {peer_name}'s Claude instance.

## Your Identity
- You represent {my_name} ({my_email})
- Your context directory: {context_dir}
- Read CLAUDE.md and relevant context files to understand {my_name}'s perspective, values, and current projects

## The Conversation
- You are talking to {peer_name}'s Claude (a separate instance with their own context)
- You do NOT have access to {peer_name}'s private files - only what they choose to share in conversation
- Be authentic to {my_name}'s perspective and values
- Be helpful, curious, and constructive

## Response Format
- Respond naturally as {my_name}'s Claude would
- Keep responses focused and substantive (1-3 paragraphs typically)
- You can reference {my_name}'s context/projects when relevant
- Don't roleplay as {peer_name} - you're only {my_name}'s Claude

"""
    if topic:
        prompt += f"## Topic\nThe conversation topic is: {topic}\n\n"

    if is_initiator:
        prompt += f"## Your Role\nYou are initiating this conversation. Start with a thoughtful opening that relates to the topic and {my_name}'s current context.\n"
    else:
        prompt += f"## Your Role\nYou are responding to {peer_name}'s Claude. Read their message and respond authentically from {my_name}'s perspective.\n"

    return prompt


def run_claude_instance(
    context_dir: Path,
    system_prompt: str,
    user_message: str,
    timeout: int = 120,
) -> tuple[bool, str]:
    """
    Run a single Claude instance and get a response.

    Args:
        context_dir: Directory to run Claude in (provides context)
        system_prompt: System prompt for this instance
        user_message: The user/conversation message to respond to
        timeout: Timeout in seconds

    Returns:
        Tuple of (success, response or error message)
    """
    try:
        # Combine system prompt and user message
        full_prompt = f"{system_prompt}\n\n---\n\n{user_message}"

        result = subprocess.run(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
            ],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=context_dir,
        )

        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            return False, f"Claude failed: {error_msg}"

        return True, result.stdout.strip()

    except subprocess.TimeoutExpired:
        return False, "Response timed out"
    except FileNotFoundError:
        return False, "Claude CLI not found"
    except Exception as e:
        return False, f"Error: {e}"


async def run_dual_session(
    peer_email: str,
    topic: Optional[str] = None,
    max_turns: int = 6,
) -> tuple[bool, str | None]:
    """
    Run a conversation session with two separate Claude instances.

    Unlike run_session which has one Claude simulate both sides, this function
    runs two actual Claude instances that only see their own user's context.

    Args:
        peer_email: The peer's email address
        topic: Optional conversation topic
        max_turns: Maximum conversation turns (each turn = 2 messages)

    Returns:
        Tuple of (success, transcript_path or error message)
    """
    from .cli import get_valid_token, get_svn_token as cli_get_svn_token

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Get SVN token
    svn_token = cli_get_svn_token(tokens.id_token)
    if not svn_token:
        return False, "Failed to get SVN token"

    # Pull peer's context
    print(f"\nPreparing dual-instance session with {peer_email}...")
    peer_context_dir = pull_peer_context(peer_email, svn_token, our_email)
    if not peer_context_dir:
        return False, f"Failed to pull {peer_email}'s context"

    # Generate session ID
    session_id = datetime.now().strftime("%Y-%m-%d") + "_" + uuid4().hex[:8]

    # Generate prompts for each instance
    our_prompt = generate_instance_prompt(
        our_context_dir, our_email, peer_email, topic, is_initiator=True
    )
    peer_prompt = generate_instance_prompt(
        peer_context_dir, peer_email, our_email, topic, is_initiator=False
    )

    our_name = our_email.split("@")[0]
    peer_name = peer_email.split("@")[0]

    # Build conversation
    transcript_lines = []
    conversation_history = ""

    print("\nStarting dual-instance conversation...")
    print(f"  {our_name}'s Claude <-> {peer_name}'s Claude")
    print(f"  Max turns: {max_turns}")
    print()

    for turn in range(max_turns):
        # Our Claude's turn
        print(f"  Turn {turn + 1}/{max_turns}: {our_name}'s Claude thinking...")

        if turn == 0:
            our_message = f"Start the conversation about: {topic or 'whatever feels natural based on context'}"
        else:
            our_message = f"Conversation so far:\n\n{conversation_history}\n\nRespond to continue the conversation."

        success, our_response = run_claude_instance(
            our_context_dir, our_prompt, our_message
        )

        if not success:
            print(f"    Error: {our_response}")
            break

        transcript_lines.append(f"**{our_name}'s Claude**: {our_response}")
        conversation_history += f"**{our_name}'s Claude**: {our_response}\n\n"
        print(f"    {our_name}'s Claude responded ({len(our_response)} chars)")

        # Peer's Claude's turn
        print(f"  Turn {turn + 1}/{max_turns}: {peer_name}'s Claude thinking...")

        peer_message = f"Conversation so far:\n\n{conversation_history}\n\nRespond to continue the conversation."

        success, peer_response = run_claude_instance(
            peer_context_dir, peer_prompt, peer_message
        )

        if not success:
            print(f"    Error: {peer_response}")
            break

        transcript_lines.append(f"**{peer_name}'s Claude**: {peer_response}")
        conversation_history += f"**{peer_name}'s Claude**: {peer_response}\n\n"
        print(f"    {peer_name}'s Claude responded ({len(peer_response)} chars)")

        # Check for natural ending signals
        lower_response = peer_response.lower()
        if any(phrase in lower_response for phrase in [
            "goodbye", "talk soon", "until next time", "take care",
            "that's all for now", "let's wrap up"
        ]):
            print("  (Natural conversation ending detected)")
            break

    if not transcript_lines:
        return False, "No conversation generated"

    # Create transcript
    header = create_transcript_header(our_email, peer_email, session_id, topic)
    transcript = header + "\n\n".join(transcript_lines)

    # Create conversation directories if needed
    our_conv_dir = our_context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
    our_conv_dir.mkdir(parents=True, exist_ok=True)

    # Save transcript locally
    transcript_filename = f"{session_id}.md"
    our_transcript_path = our_conv_dir / transcript_filename
    our_transcript_path.write_text(transcript)
    print(f"\nSaved transcript: {our_transcript_path}")

    # Commit to our repo
    print("\nCommitting to your repo...")
    our_svn = SvnClient(our_context_dir, repo_url_for_email(our_email), svn_token, our_email)
    try:
        our_svn.add(our_transcript_path.relative_to(our_context_dir), parents=True)
        our_svn.commit(f"Dual session with {peer_email}: {session_id}")
        print("  Committed to your repo")
    except SvnError as e:
        print(f"  Warning: Failed to commit to your repo: {e}")

    # Commit to peer's repo
    print(f"\nCommitting to {peer_email}'s repo...")
    peer_conv_dir = peer_context_dir / "claudeconnect" / f"with-{email_to_repo_name(our_email)}"
    peer_conv_dir.mkdir(parents=True, exist_ok=True)
    peer_transcript_path = peer_conv_dir / transcript_filename
    peer_transcript_path.write_text(transcript)

    peer_svn = SvnClient(peer_context_dir, repo_url_for_email(peer_email), svn_token, our_email)
    try:
        peer_svn.add(peer_transcript_path.relative_to(peer_context_dir), parents=True)
        peer_svn.commit(f"Dual session with {our_email}: {session_id}")
        print(f"  Committed to {peer_email}'s repo")
    except SvnError as e:
        print(f"  Warning: Failed to commit to peer's repo: {e}")
        print(f"  (This is expected if you don't have write access to their with-{email_to_repo_name(our_email)} folder)")

    return True, str(our_transcript_path)
