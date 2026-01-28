"""Session management for Claude Connect.

Handles peer context pulling, conversation spawning, and transcript commits.
"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import httpx

from .config import (
    get_config, get_tokens, Tokens, get_shadow_dir, get_peers_dir,
    SERVER_URL, API_BASE_URL, email_to_repo_name, get_active_account,
    get_pending_sessions_dir,
)

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


def decrypt_peer_context(peer_dir: Path, peer_email: str, our_email: str) -> int:
    """
    Decrypt all encrypted files in a peer's context using their master key.

    Args:
        peer_dir: Path to the peer's checked-out context
        peer_email: The peer's email (to look up their master key)
        our_email: Our email (for account-scoped key storage)

    Returns:
        Number of files decrypted
    """
    if not HAS_ENCRYPTION:
        return 0

    if not has_friend_master_key(peer_email, our_email):
        print(f"  Warning: No master key for {peer_email}, cannot decrypt their files")
        return 0

    # First, collect all files to decrypt for progress tracking
    md_files = list(peer_dir.rglob("*.md"))
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
                plaintext = decrypt_file_with_friend_master_key(content, peer_email, our_email)
                md_file.write_bytes(plaintext)
                decrypted_count += 1

                # Progress output
                if show_progress and (decrypted_count % progress_interval == 0):
                    print(f"  [{decrypted_count}/{total_files}] Decrypting...")
        except Exception as e:
            print(f"  Warning: Could not decrypt {md_file.name}: {e}")

    return decrypted_count


def _load_peer_manifest(manifest_path: Path) -> dict[str, dict]:
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict) and "files" in data and isinstance(data["files"], list):
        return {f["path"]: f for f in data["files"] if isinstance(f, dict) and "path" in f}
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    return {}


def _write_peer_manifest(manifest_path: Path, peer_email: str, files: dict[str, dict]) -> None:
    payload = {"user": peer_email, "files": list(files.values())}
    manifest_path.write_text(json.dumps(payload, indent=2))


def _compute_peer_pull_plan(
    server_files: dict[str, dict],
    cached_files: dict[str, dict],
) -> tuple[list[str], list[str]]:
    to_download: list[str] = []
    for path, info in server_files.items():
        cached = cached_files.get(path)
        if not cached:
            to_download.append(path)
            continue
        server_sha = info.get("sha256")
        cached_sha = cached.get("sha256")
        if server_sha and cached_sha:
            if server_sha != cached_sha:
                to_download.append(path)
            continue
        if info.get("size") != cached.get("size") or info.get("mtime") != cached.get("mtime"):
            to_download.append(path)
    removed = [path for path in cached_files if path not in server_files]
    return to_download, removed


def pull_peer_context_http(peer_email: str, id_token: str, our_email: str, max_workers: int = 10) -> Path | None:
    """
    Pull or update a peer's context to local cache using HTTP API (parallel downloads).

    Args:
        peer_email: The peer's email address
        id_token: JWT token for authentication
        our_email: Our email (to determine which account's peers dir to use)
        max_workers: Number of parallel download threads

    Returns:
        Path to the peer's cached context, or None on failure.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    peers_dir = get_peers_dir(our_email)
    peer_name = email_to_repo_name(peer_email)
    peer_dir = peers_dir / peer_name
    peer_shadow_dir = get_shadow_dir(our_email) / "peers" / peer_name
    peer_shadow_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = peer_shadow_dir / ".peer_manifest.json"

    # Ensure peers directory exists
    peers_dir.mkdir(parents=True, exist_ok=True)
    peer_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {id_token}"}

    # Get manifest from peer
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

    files = manifest.get("files", [])
    if not files:
        return peer_dir
    server_files = {f["path"]: f for f in files if isinstance(f, dict) and "path" in f}
    cached_files = _load_peer_manifest(manifest_path)
    to_download, removed = _compute_peer_pull_plan(server_files, cached_files)
    to_download_set = set(to_download)

    def download_file(file_info: dict) -> bool:
        path = file_info["path"]
        try:
            response = httpx.get(
                f"{API_BASE_URL}/files/{peer_email}/{path}",
                headers=headers,
                timeout=60,
            )
            if response.status_code == 200:
                encrypted_content = response.content
                shadow_path = peer_shadow_dir / path
                shadow_path.parent.mkdir(parents=True, exist_ok=True)
                shadow_path.write_bytes(encrypted_content)

                local_path = peer_dir / path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                if HAS_ENCRYPTION and is_encrypted_file(encrypted_content):
                    if has_friend_master_key(peer_email, our_email):
                        try:
                            plaintext = decrypt_file_with_friend_master_key(
                                encrypted_content, peer_email, our_email
                            )
                            local_path.write_bytes(plaintext)
                        except Exception as e:
                            print(f"  Warning: Could not decrypt {path}: {e}")
                            local_path.write_bytes(encrypted_content)
                    else:
                        print(f"  Warning: No master key for {peer_email}, cannot decrypt {path}")
                        local_path.write_bytes(encrypted_content)
                else:
                    local_path.write_bytes(encrypted_content)
                return True
            return False
        except Exception:
            return False

    # Download in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(download_file, server_files[p]): p for p in to_download}
        for future in as_completed(futures):
            try:
                path = futures[future]
                success = future.result()
                if success:
                    cached_files[path] = server_files[path]
            except Exception:
                pass

    # Restore missing local files from shadow for unchanged paths
    for path, info in server_files.items():
        if path in to_download_set:
            continue
        local_path = peer_dir / path
        if local_path.exists():
            continue
        shadow_path = peer_shadow_dir / path
        if not shadow_path.exists():
            continue
        encrypted_content = shadow_path.read_bytes()
        if HAS_ENCRYPTION and is_encrypted_file(encrypted_content) and has_friend_master_key(peer_email, our_email):
            try:
                plaintext = decrypt_file_with_friend_master_key(encrypted_content, peer_email, our_email)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(plaintext)
                continue
            except Exception:
                pass
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(encrypted_content)

    for path in removed:
        cached_files.pop(path, None)
        (peer_dir / path).unlink(missing_ok=True)
        (peer_shadow_dir / path).unlink(missing_ok=True)

    _write_peer_manifest(manifest_path, peer_email, cached_files)

    return peer_dir


def upload_file_http(email: str, path: str, content: bytes, id_token: str, encrypt_for: str | None = None, use_friend_key: bool = False, our_email: str | None = None) -> bool:
    """Upload a file to a user's storage via HTTP API.

    Args:
        email: Target user's email (whose storage to write to)
        path: File path relative to user's files directory
        content: File content (plaintext)
        id_token: JWT for authentication
        encrypt_for: If set, encrypt content using this user's master key
        use_friend_key: If True, encrypt using friend's master key (for friend delivery)
        our_email: Our email (required when use_friend_key=True, for account-scoped key lookup)
    """
    encrypted_content = content

    # Encrypt content before upload if requested
    if encrypt_for and HAS_ENCRYPTION:
        try:
            from .encryption import should_encrypt_file, encrypt_file_with_master_key, encrypt_file_for_friend
            if should_encrypt_file(path):
                if use_friend_key:
                    # Delivering to friend's repo - use their master key
                    # our_email is required to know where to find friend's key
                    if our_email is None:
                        raise ValueError("our_email required when use_friend_key=True")
                    encrypted_content = encrypt_file_for_friend(content, encrypt_for, our_email)
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
            # Skip hidden files (any path component starting with '.')
            if any(part.startswith('.') for part in Path(rel_path).parts):
                continue
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

    def upload_one(path: str, local_path: Path) -> bool:
        return upload_file_http(email, path, local_path.read_bytes(), id_token)

    # Upload in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(upload_one, path, local_path) for path, local_path in to_upload]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass

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
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token, our_email)
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
        if upload_file_http(
            peer_email,
            peer_transcript_rel_path,
            transcript.encode(),
            tokens.id_token,
            encrypt_for=peer_email,
            use_friend_key=True,
            our_email=our_email,
        ):
            print(f"  Uploaded to {peer_email}'s repo")
        else:
            print(f"  Warning: Failed to upload to peer's repo")
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


def is_conversation_ending(response: str) -> bool:
    """Check if a response contains natural ending phrases."""
    ending_phrases = [
        "goodbye",
        "talk soon",
        "until next time",
        "take care",
        "that's all for now",
        "let's wrap up",
        "nice chatting",
        "catch you later",
        "signing off",
    ]
    lower_response = response.lower()
    return any(phrase in lower_response for phrase in ending_phrases)


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
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token, our_email)
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

        if is_conversation_ending(our_response):
            print("  (Natural conversation ending detected)")
            break

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

        if is_conversation_ending(peer_response):
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
        if upload_file_http(
            peer_email,
            peer_transcript_rel_path,
            transcript.encode(),
            tokens.id_token,
            encrypt_for=peer_email,
            use_friend_key=True,
            our_email=our_email,
        ):
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

    The conversation is automatically saved by Claude Code to ~/.claude/projects/
    and will be synced to both repos within 60-90 seconds after the session ends.

    Args:
        peer_email: The peer's email address

    Returns:
        Tuple of (success: bool, message: str)
    """
    from .cli import get_valid_token

    # Check platform
    if platform.system() != "Darwin":
        return False, "Interactive sessions are only supported on macOS. Use `claudeconnect session` for autonomous conversations."

    # Check for Claude Code installation
    if not shutil.which("claude"):
        return False, "Claude Code (claude command) not found. Please install from https://claude.ai/download"

    # Get our credentials
    tokens = get_valid_token()
    if not tokens:
        return False, "Not logged in"

    config = get_config()
    if not config.context_dir:
        return False, "No context directory configured"

    our_email = tokens.email

    # Generate session ID and create pending session file
    session_id = str(uuid4())
    pending_dir = get_pending_sessions_dir(our_email)
    pending_dir.mkdir(parents=True, exist_ok=True)
    pending_file = pending_dir / f"{session_id}.json"
    pending_data = {
        "peer_email": peer_email,
        "created_at": datetime.now().isoformat(),
    }
    pending_file.write_text(json.dumps(pending_data))

    # Pull peer's context
    print(f"\nPreparing interactive session with {peer_email}...")
    peer_context_dir = pull_peer_context_http(peer_email, tokens.id_token, our_email)
    if not peer_context_dir:
        return False, f"Failed to pull {peer_email}'s context. Are you connected as friends?"

    # Verify directory exists
    if not peer_context_dir.exists() or not peer_context_dir.is_dir():
        return False, f"Peer context directory not found: {peer_context_dir}"

    # Generate system prompt
    system_prompt = generate_interactive_prompt(peer_email, our_email)

    # Write system prompt to a temp file to avoid complex escaping issues
    # Using tempfile to avoid accumulation in /tmp
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='cc-prompt-') as f:
        f.write(system_prompt)
        prompt_file = f.name

    # Build the command to run in the new terminal
    # Clear venv variables to avoid activation/deactivation messages that corrupt display
    # Use claudeconnect instead of claude to get dashboard + sync during session
    # Pass context-dir to specify peer's context, system-prompt for persona, initial-prompt to greet
    # Pass --peer for simplified interactive session banner
    peer_display_name = peer_email.split("@")[0] if "@" in peer_email else peer_email

    # Use the same claudeconnect executable that launched the parent session
    # sys.argv[0] contains the path to the current script/executable
    claudeconnect_path = sys.argv[0]
    if not Path(claudeconnect_path).is_absolute():
        # Resolve relative path to absolute
        resolved = shutil.which(claudeconnect_path)
        if resolved:
            claudeconnect_path = resolved
        else:
            claudeconnect_path = str(Path(claudeconnect_path).resolve())

    terminal_cmd = f'unset VIRTUAL_ENV CONDA_DEFAULT_ENV; cd {peer_context_dir} && {claudeconnect_path} start --context-dir "{peer_context_dir}" --peer "{peer_display_name}" --session-id "{session_id}" --system-prompt "$(cat {prompt_file})" --initial-prompt "hi"'

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
    print(f"  Peer context: {peer_context_dir}")

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
    print(f"  When you're done, press Ctrl+D to exit.")
    print(f"\n  After the session ends, this command will wait to import the transcript.")
    print(f"  (Press Ctrl+C here to skip waiting and import manually later with `claudeconnect sync`)")

    # Wait for transcript to appear and import it
    from .transcripts import discover_new_interactive_transcripts, import_transcript

    print(f"\n  Waiting for transcript...")

    # Poll for transcript (check every 10 seconds for up to 5 minutes)
    max_attempts = 30
    our_context_dir = Path(config.context_dir)

    for attempt in range(max_attempts):
        time.sleep(10)

        try:
            # Check if transcript exists
            new_transcripts = discover_new_interactive_transcripts(our_email, our_context_dir)

            for jsonl_path, metadata in new_transcripts:
                # Check if this is our session
                if metadata.get("session_id") == session_id:
                    print(f"\n✓ Transcript found! Importing...")
                    transcript_path = import_transcript(
                        jsonl_path,
                        metadata,
                        our_email,
                        our_context_dir,
                        tokens.id_token,
                    )
                    if transcript_path:
                        print(f"  Saved to: {transcript_path}")
                        print(f"\n  The transcript has been uploaded to both your repo and {peer_email}'s repo.")
                        print(f"  Run `claudeconnect sync` to upload any other changes.")
                        return True, f"Transcript saved to {transcript_path}"
                    else:
                        print(f"  Warning: Failed to import transcript")
                        return True, "Session complete but transcript import failed"
        except KeyboardInterrupt:
            print(f"\n\n  Skipping transcript import. Run `claudeconnect sync` later to import it.")
            return True, "Session started (import skipped)"
        except Exception as e:
            pass  # Continue polling

    # Timeout
    print(f"\n  Transcript not found after 5 minutes.")
    print(f"  The session may still be running, or Claude Code may not have saved it yet.")
    print(f"  Run `claudeconnect sync` later to import the transcript.")

    return True, f"Interactive session with {peer_email} started (import timeout)"
