"""Session management for Claude Connect.

Handles peer context pulling, conversation spawning, and transcript commits.
"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from .config import get_config, get_tokens, Tokens, get_shadow_dir, SERVER_URL, API_BASE_URL
from .svn_ops import email_to_repo_name, repo_url_for_email

# Encryption imports (optional)
try:
    from .encryption import (
        is_encryption_available,
        is_encrypted_file,
        decrypt_file_with_friend_master_key,
        has_friend_master_key,
    )
    HAS_ENCRYPTION = is_encryption_available()
except ImportError:
    HAS_ENCRYPTION = False


# Directory for interactive session transcripts
TRANSCRIPTS_DIR = Path.home() / ".claude-connect" / "transcripts"


# Cache directory for peer contexts
PEERS_DIR = Path.home() / ".claude-connect" / "peers"


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


def decrypt_peer_context(peer_dir: Path, peer_email: str) -> int:
    """
    Decrypt all encrypted files in a peer's context using their master key.

    Args:
        peer_dir: Path to the peer's checked-out context
        peer_email: The peer's email (to look up their master key)

    Returns:
        Number of files decrypted
    """
    if not HAS_ENCRYPTION:
        return 0

    if not has_friend_master_key(peer_email):
        print(f"  Warning: No master key for {peer_email}, cannot decrypt their files")
        return 0

    # First, collect all files to decrypt for progress tracking
    md_files = [f for f in peer_dir.rglob("*.md") if ".svn" not in f.parts]
    total_files = len(md_files)

    # Only show progress for large file sets
    show_progress = total_files >= 100
    progress_interval = max(1, total_files // 10)  # ~10 progress updates

    if show_progress:
        print(f"  Decrypting {total_files} files...")

    decrypted_count = 0
    for i, md_file in enumerate(md_files, 1):
        try:
            content = md_file.read_bytes()
            if is_encrypted_file(content):
                plaintext = decrypt_file_with_friend_master_key(content, peer_email)
                md_file.write_bytes(plaintext)
                decrypted_count += 1

                # Progress output
                if show_progress and (decrypted_count % progress_interval == 0):
                    print(f"  [{decrypted_count}/{total_files}] Decrypting...")
        except Exception as e:
            print(f"  Warning: Could not decrypt {md_file.name}: {e}")

    return decrypted_count


def pull_peer_context_http(peer_email: str, id_token: str) -> Path | None:
    """
    Pull or update a peer's context to local cache using HTTP API.

    Args:
        peer_email: The peer's email address
        id_token: JWT token for authentication

    Returns:
        Path to the peer's cached context, or None on failure.
    """
    peer_name = email_to_repo_name(peer_email)
    peer_dir = PEERS_DIR / peer_name

    # Ensure peers directory exists
    PEERS_DIR.mkdir(parents=True, exist_ok=True)
    peer_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {id_token}"}

    # Get manifest from peer
    print(f"  Fetching {peer_email}'s manifest...")
    try:
        response = httpx.get(
            f"{API_BASE_URL}/manifest/{peer_email}",
            headers=headers,
            timeout=60,
        )
        if response.status_code == 404:
            print(f"  User {peer_email} not found")
            return None
        if response.status_code != 200:
            print(f"  Failed to get manifest: {response.text}")
            return None
        manifest = response.json()
    except Exception as e:
        print(f"  Error getting manifest: {e}")
        return None

    # Download files we have permission to read
    files = manifest.get("files", [])
    print(f"  Downloading {len(files)} file(s)...")
    downloaded = 0
    for file_info in files:
        path = file_info["path"]
        try:
            response = httpx.get(
                f"{API_BASE_URL}/files/{peer_email}/{path}",
                headers=headers,
                timeout=60,
            )
            if response.status_code == 200:
                local_path = peer_dir / path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(response.content)
                downloaded += 1
            elif response.status_code == 403:
                pass  # No permission, skip silently
            else:
                print(f"  Warning: Failed to download {path}: {response.status_code}")
        except Exception as e:
            print(f"  Warning: Error downloading {path}: {e}")

    print(f"  Downloaded {downloaded} file(s)")

    # Decrypt any encrypted files
    decrypted = decrypt_peer_context(peer_dir, peer_email)
    if decrypted:
        print(f"  Decrypted {decrypted} files")

    return peer_dir


def pull_peer_context(peer_email: str, svn_token: str, our_email: str) -> Path | None:
    """Legacy wrapper - redirects to HTTP implementation."""
    # Get fresh tokens for HTTP API
    tokens = get_tokens()
    if not tokens:
        print("  No valid tokens")
        return None
    return pull_peer_context_http(peer_email, tokens.id_token)


def upload_file_http(email: str, path: str, content: bytes, id_token: str, encrypt_for: str | None = None, use_friend_key: bool = False) -> bool:
    """Upload a file to a user's storage via HTTP API.

    Args:
        email: Target user's email (whose storage to write to)
        path: File path relative to user's files directory
        content: File content (plaintext)
        id_token: JWT for authentication
        encrypt_for: If set, encrypt content using this user's master key
        use_friend_key: If True, encrypt using friend's master key (for transcript delivery to peer's repo)
    """
    encrypted_content = content

    # Encrypt content before upload if requested
    if encrypt_for and HAS_ENCRYPTION:
        try:
            from .encryption import should_encrypt_file, encrypt_file_with_master_key, encrypt_file_for_friend
            if should_encrypt_file(path):
                if use_friend_key:
                    # Delivering to friend's repo - use their master key
                    encrypted_content = encrypt_file_for_friend(content, encrypt_for)
                else:
                    # Our own repo - use our master key
                    encrypted_content = encrypt_file_with_master_key(content, encrypt_for)
        except Exception as e:
            print(f"  Warning: Could not encrypt {path}: {e}")

    headers = {"Authorization": f"Bearer {id_token}"}
    try:
        response = httpx.put(
            f"{API_BASE_URL}/files/{email}/{path}",
            headers=headers,
            content=encrypted_content,
            timeout=60,
        )
        if response.status_code == 200:
            # Update shadow directory with encrypted version
            if encrypt_for:
                shadow_dir = get_shadow_dir(encrypt_for)
                shadow_path = shadow_dir / path
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                shadow_path.write_bytes(encrypted_content)
            return True
        return False
    except Exception as e:
        print(f"  Warning: Error uploading {path}: {e}")
        return False


def sync_http(context_dir: Path, email: str, id_token: str, max_workers: int = 10) -> bool:
    """Sync local files with server using HTTP API (parallel uploads)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import hashlib
    import threading

    def compute_sha256(file_path: Path) -> str:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    headers = {"Authorization": f"Bearer {id_token}"}

    # Get manifest from server
    try:
        response = httpx.get(
            f"{API_BASE_URL}/manifest/{email}",
            headers=headers,
            timeout=60,
        )
        if response.status_code != 200:
            return False
        manifest = response.json()
    except Exception:
        return False

    server_files = {f["path"]: f for f in manifest.get("files", [])}

    # Build local file manifest
    local_files = {}
    for file_path in context_dir.rglob("*"):
        if file_path.is_file():
            rel_path = str(file_path.relative_to(context_dir))
            local_files[rel_path] = {
                "path": rel_path,
                "sha256": compute_sha256(file_path),
            }

    # Find files that need uploading
    to_upload = []
    for path, local_info in local_files.items():
        server_info = server_files.get(path)
        if not server_info or server_info["sha256"] != local_info["sha256"]:
            local_path = context_dir / path
            to_upload.append((path, local_path))

    if not to_upload:
        return True

    # Progress tracking
    completed = 0
    lock = threading.Lock()
    total = len(to_upload)

    def upload_one(path: str, local_path: Path) -> bool:
        nonlocal completed
        result = upload_file_http(email, path, local_path.read_bytes(), id_token)
        with lock:
            completed += 1
            pct = int(completed / total * 100)
            print(f"\r  [{pct:3d}%] Uploading: {path[:50]:<50}", end="", flush=True)
        return result

    # Upload in parallel
    print(f"  Uploading {total} file(s)...")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(upload_one, path, local_path) for path, local_path in to_upload]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass

    print(f"\r  Uploaded {completed} file(s)" + " " * 40)
    return True


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
    from .cli import get_valid_token

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Pull peer's context
    print(f"\nPreparing session with {peer_email}...")
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
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

    # Upload transcript to our repo via HTTP
    print("\nUploading to your repo...")
    tokens = get_tokens()
    if tokens:
        transcript_rel_path = f"claudeconnect/with-{email_to_repo_name(peer_email)}/{transcript_filename}"
        if upload_file_http(our_email, transcript_rel_path, transcript.encode(), tokens.id_token, encrypt_for=our_email):
            print("  Uploaded to your repo")
        else:
            print("  Warning: Failed to upload to your repo")
    else:
        print("  Warning: No valid tokens for upload")

    # Upload transcript to peer's repo via HTTP
    print(f"\nUploading to {peer_email}'s repo...")
    if tokens:
        peer_transcript_rel_path = f"claudeconnect/with-{email_to_repo_name(our_email)}/{transcript_filename}"
        if upload_file_http(peer_email, peer_transcript_rel_path, transcript.encode(), tokens.id_token, encrypt_for=peer_email, use_friend_key=True):
            print(f"  Uploaded to {peer_email}'s repo")
        else:
            print(f"  Warning: Failed to upload to peer's repo")
            print(f"  (This is expected if you don't have write access to their with-{email_to_repo_name(our_email)} folder)")
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
    from .cli import get_valid_token

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Pull peer's context
    print(f"\nPreparing dual-instance session with {peer_email}...")
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
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
        print(f"\n  **{our_name}'s Claude**:\n  {our_response}\n")

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
        print(f"\n  **{peer_name}'s Claude**:\n  {peer_response}\n")

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

    # Upload transcript to our repo via HTTP
    print("\nUploading to your repo...")
    tokens = get_tokens()
    if tokens:
        transcript_rel_path = f"claudeconnect/with-{email_to_repo_name(peer_email)}/{transcript_filename}"
        if upload_file_http(our_email, transcript_rel_path, transcript.encode(), tokens.id_token, encrypt_for=our_email):
            print("  Uploaded to your repo")
        else:
            print("  Warning: Failed to upload to your repo")
    else:
        print("  Warning: No valid tokens for upload")

    # Upload transcript to peer's repo via HTTP
    print(f"\nUploading to {peer_email}'s repo...")
    if tokens:
        peer_transcript_rel_path = f"claudeconnect/with-{email_to_repo_name(our_email)}/{transcript_filename}"
        if upload_file_http(peer_email, peer_transcript_rel_path, transcript.encode(), tokens.id_token, encrypt_for=peer_email, use_friend_key=True):
            print(f"  Uploaded to {peer_email}'s repo")
        else:
            print(f"  Warning: Failed to upload to peer's repo")
            print(f"  (This is expected if you don't have write access to their with-{email_to_repo_name(our_email)} folder)")

    return True, str(our_transcript_path)


def generate_interactive_prompt(
    peer_email: str,
    user_email: str,
) -> str:
    """
    Generate system prompt for an interactive session where user chats with peer's Claude.

    The peer's Claude will have access to the peer's context and represent them
    in conversation with the user.
    """
    peer_name = peer_email.split("@")[0]
    user_name = user_email.split("@")[0]

    prompt = f"""You are representing {peer_name} ({peer_email}) in a ClaudeConnect interactive session.

The person chatting with you is {user_name} ({user_email}).

You have access to {peer_name}'s context in this directory. Answer questions and have conversations from {peer_name}'s perspective, based on their notes, projects, and context.

IMPORTANT: When the conversation starts, immediately introduce yourself with a greeting like:
"Hi {user_name}! I'm representing {peer_name} in this ClaudeConnect session. I have access to their notes and context. What would you like to know?"

Guidelines:
- Be helpful and conversational
- Only share information that exists in the context files
- If asked about something not in the context, say so rather than making things up
- Respect any privacy guidelines in privacy.md

This conversation will be saved and shared with both parties.

To exit the session, the user can press Ctrl+D twice."""

    return prompt


def create_interactive_transcript_header(
    user_email: str,
    peer_email: str,
    session_id: str,
) -> str:
    """Create the markdown header for an interactive session transcript."""
    timestamp = datetime.now().isoformat()

    header = f"""# Interactive Session: {user_email.split('@')[0]} with {peer_email.split('@')[0]}'s Claude

**Session ID**: {session_id}
**Date**: {timestamp}
**User**: {user_email}
**Representing**: {peer_email}
**Type**: interactive

---

"""
    return header


def run_interactive_session(
    peer_email: str,
) -> tuple[bool, str]:
    """
    Start an interactive session with a peer's Claude in a new terminal window.

    Opens a new Terminal.app window (macOS only) where the user can chat directly
    with a Claude instance that has access to the peer's context.

    Args:
        peer_email: The peer's email address

    Returns:
        Tuple of (success, session_id or error message)
    """
    from .cli import get_valid_token

    # Check platform
    if platform.system() != "Darwin":
        return False, "Interactive sessions are only supported on macOS. Use `claudeconnect session` for autonomous conversations."

    # Check for script command
    if not shutil.which("script"):
        return False, "The 'script' command is not available. Cannot capture transcript."

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Pull peer's context
    print(f"\nPreparing interactive session with {peer_email}...")
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token)
    if not peer_context_dir:
        return False, f"Failed to pull {peer_email}'s context. Are you connected as friends?"

    # Generate session ID
    session_id = datetime.now().strftime("%Y-%m-%d") + "_" + uuid4().hex[:8]

    # Create transcripts directory
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    transcript_path = TRANSCRIPTS_DIR / f"{session_id}.txt"

    # Generate system prompt
    system_prompt = generate_interactive_prompt(peer_email, our_email)

    # Write system prompt to a temp file to avoid escaping issues
    prompt_file = TRANSCRIPTS_DIR / f"{session_id}_prompt.txt"
    prompt_file.write_text(system_prompt)

    # Build the command to run in the new terminal
    # We use script to capture the session, then claude with the system prompt from file
    # cd to peer context first so Claude sees it as its working directory
    # Pass "hi" as initial prompt to trigger Claude's greeting
    terminal_cmd = f"script -Fq {transcript_path} bash -c 'cd {peer_context_dir} && claude --system-prompt \"$(cat {prompt_file})\" \"hi\"'"

    # Escape for AppleScript: backslash-escape double quotes and backslashes
    escaped_cmd = terminal_cmd.replace("\\", "\\\\").replace('"', '\\"')

    # Open new Terminal window with osascript
    osascript_cmd = f'''
    tell application "Terminal"
        do script "{escaped_cmd}"
        activate
    end tell
    '''

    print(f"\nOpening interactive session in new Terminal window...")
    print(f"  Session ID: {session_id}")
    print(f"  Peer context: {peer_context_dir}")
    print(f"  Transcript will be saved to: {transcript_path}")

    try:
        result = subprocess.run(
            ["osascript", "-e", osascript_cmd],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            error = result.stderr or "Unknown error"
            return False, f"Failed to open Terminal: {error}"

    except subprocess.TimeoutExpired:
        return False, "Timed out trying to open Terminal"
    except FileNotFoundError:
        return False, "osascript not found. Are you on macOS?"

    print(f"\n✓ Interactive session started!")
    print(f"\n  You're now chatting with {peer_email}'s Claude in the new window.")
    print(f"  When you're done, press Ctrl+D twice to exit.")
    print(f"\n  After the session ends, run:")
    print(f"    claudeconnect commit-transcript {session_id}")
    print(f"  to save and share the transcript.")

    return True, session_id


def commit_interactive_transcript(
    session_id: str,
) -> tuple[bool, str]:
    """
    Commit an interactive session transcript to both repos.

    Reads the raw transcript, adds a header, and commits to both the user's
    and peer's repos.

    Args:
        session_id: The session ID from run_interactive_session

    Returns:
        Tuple of (success, transcript_path or error message)
    """
    from .cli import get_valid_token

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_context_dir = Path(config.context_dir)
    our_email = tokens.email

    # Find the transcript file
    transcript_path = TRANSCRIPTS_DIR / f"{session_id}.txt"
    if not transcript_path.exists():
        return False, f"Transcript not found: {transcript_path}"

    # Read the raw transcript
    raw_transcript = transcript_path.read_text()

    if len(raw_transcript.strip()) < 100:
        return False, "Transcript appears to be empty or too short"

    # We need to determine the peer email from the session
    # For now, we'll ask the user to provide it or parse from the transcript
    # This is a limitation - we should store session metadata

    # For now, return success with instructions
    print(f"\nTranscript found: {transcript_path}")
    print(f"  Size: {len(raw_transcript)} bytes")
    print(f"\n  To commit this transcript, the peer email is needed.")
    print(f"  This will be automated in a future version.")

    return True, str(transcript_path)
