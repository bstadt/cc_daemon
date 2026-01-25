"""Interactive session transcript management.

Handles discovery, import, and upload of Claude Code interactive session transcripts.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from .config import API_BASE_URL, email_to_repo_name, repo_name_to_email, get_peers_dir


def context_dir_to_claude_projects_dir(context_dir: Path) -> str:
    """Convert context directory path to ~/.claude/projects/ directory name.

    Example:
        /Users/frsc/.claude-connect/peers/brandon-calcifercomputing-com
        → -Users-frsc--claude-connect-peers-brandon-calcifercomputing-com
    """
    # Replace slashes and dots with hyphens
    path_str = str(context_dir.resolve())
    # Replace / and . with -
    result = path_str.replace("/", "-").replace(".", "-")
    return result


def _format_timestamp_for_filename(iso_timestamp: str) -> str:
    """Format ISO 8601 timestamp as YYYY-MM-DD-HHMMSS for filename.

    Example:
        "2026-01-23T14:30:22.123Z" → "2026-01-23-143022"
    """
    try:
        # Parse ISO timestamp (remove 'Z' if present, handle timezone)
        timestamp_str = iso_timestamp.replace('Z', '+00:00')
        # Try parsing with timezone
        try:
            dt = datetime.fromisoformat(timestamp_str)
        except ValueError:
            # Fallback: parse without timezone
            dt = datetime.fromisoformat(iso_timestamp.split('+')[0].split('Z')[0])

        # Format as YYYY-MM-DD-HHMMSS
        return dt.strftime("%Y-%m-%d-%H%M%S")
    except Exception:
        # Fallback: return current timestamp
        return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def _extract_jsonl_metadata(jsonl_path: Path) -> dict | None:
    """Extract session metadata from JSONL transcript.

    Returns:
        {
            'session_id': str,      # UUID from sessionId field
            'cwd': str,             # Working directory
            'start_time': str,      # ISO 8601 timestamp of first message
            'end_time': str,        # ISO 8601 timestamp of last message
        }

    Note: peer_email is added separately in discover_new_interactive_transcripts()
    from the directory structure.
    """
    try:
        with open(jsonl_path) as f:
            lines = [json.loads(line) for line in f if line.strip()]

        if not lines:
            return None

        # Find first user/assistant message
        first_msg = next((l for l in lines if l.get("type") in ["user", "assistant"]), None)
        if not first_msg:
            return None

        # Extract metadata
        session_id = first_msg.get("sessionId")
        cwd = first_msg.get("cwd")
        start_time = first_msg.get("timestamp")

        # Find last message
        last_msg = next((l for l in reversed(lines) if l.get("type") in ["user", "assistant"]), None)
        end_time = last_msg.get("timestamp") if last_msg else start_time

        return {
            "session_id": session_id,
            "cwd": cwd,
            "start_time": start_time,
            "end_time": end_time,
        }
    except Exception:
        # Silently fail and return None
        return None


def convert_jsonl_to_markdown(jsonl_path: Path, metadata: dict, our_email: str) -> str:
    """Convert Claude Code JSONL transcript to ClaudeConnect markdown format.

    Args:
        jsonl_path: Path to .jsonl file
        metadata: Metadata dict from _extract_jsonl_metadata()
        our_email: Our email address

    Returns:
        Markdown-formatted transcript string
    """
    # Read JSONL lines
    with open(jsonl_path) as f:
        lines = [json.loads(line) for line in f if line.strip()]

    # Filter to user/assistant messages only
    messages = [l for l in lines if l.get("type") in ["user", "assistant"]]

    # Build markdown header
    peer_email = metadata.get("peer_email", "unknown@example.com")
    session_id = metadata["session_id"]
    start_time = metadata.get("start_time", "")

    header = f"""# Interactive Session: {our_email.split('@')[0]} with {peer_email.split('@')[0]}'s Claude

**Session ID**: {session_id}
**Date**: {start_time}
**User**: {our_email}
**Representing**: {peer_email}
**Type**: interactive
**Source**: claude-code-transcript

---

"""

    # Build conversation
    conversation = []
    for msg in messages:
        role = msg.get("type")
        timestamp = msg.get("timestamp", "")

        if role == "user":
            content = msg.get("message", {})
            if isinstance(content, dict):
                content = content.get("content", "")
            elif isinstance(content, str):
                pass  # Already a string
            else:
                content = str(content)

            conversation.append(f"**User** ({timestamp}):\n{content}\n")

        elif role == "assistant":
            # Extract text from content array
            message_obj = msg.get("message", {})
            if isinstance(message_obj, dict):
                content_blocks = message_obj.get("content", [])
                if isinstance(content_blocks, list):
                    text_parts = [block.get("text", "") for block in content_blocks if isinstance(block, dict) and block.get("type") == "text"]
                    content = "\n".join(text_parts)
                else:
                    content = str(content_blocks)
            else:
                content = str(message_obj)

            conversation.append(f"**Assistant** ({timestamp}):\n{content}\n")

    return header + "\n\n".join(conversation)


def check_file_exists_on_server(email: str, path: str, id_token: str) -> bool:
    """Check if a file exists on the server using HEAD request.

    Args:
        email: Email of the repo owner
        path: Relative path of the file in the repo
        id_token: JWT token for authentication

    Returns:
        True if file exists, False otherwise
    """
    try:
        headers = {"Authorization": f"Bearer {id_token}"}
        response = httpx.head(
            f"{API_BASE_URL}/file/{email}/{path}",
            headers=headers,
            timeout=10,
        )
        return response.status_code == 200
    except Exception:
        return False


def discover_new_interactive_transcripts(our_email: str, context_dir: Path) -> list[tuple[Path, dict]]:
    """Find new Claude Code transcripts from ClaudeConnect interactive sessions.

    ONLY imports transcripts where cwd points to a peer directory.
    Does NOT import user's normal Claude Code usage.

    Args:
        our_email: Our email address
        context_dir: Path to our context directory

    Returns:
        List of (jsonl_file_path, metadata_dict) tuples for unprocessed transcripts
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return []

    # Use email-namespaced peers directory
    peers_dir = get_peers_dir(our_email)
    if not peers_dir.exists():
        return []

    discovered = []

    # Check each peer's context directory for corresponding project transcripts
    for peer_dir in peers_dir.iterdir():
        if not peer_dir.is_dir():
            continue

        # Extract peer email from directory name (e.g., brandon-gmail-com → brandon@gmail.com)
        peer_repo_name = peer_dir.name
        peer_email = repo_name_to_email(peer_repo_name)

        # Skip if peer email matches our email (can't have session with self)
        if peer_email.lower() == our_email.lower():
            continue

        # Map to expected ~/.claude/projects/ subdirectory
        projects_subdir_name = context_dir_to_claude_projects_dir(peer_dir)
        projects_subdir = claude_projects / projects_subdir_name

        if not projects_subdir.exists():
            continue

        # Find .jsonl conversation files
        for jsonl_file in projects_subdir.glob("*.jsonl"):
            # Skip subagent transcripts (in subdirectories)
            if jsonl_file.parent != projects_subdir:
                continue

            # Extract metadata
            metadata = _extract_jsonl_metadata(jsonl_file)
            if not metadata:
                continue

            # Add peer email to metadata (we know it from directory structure)
            metadata["peer_email"] = peer_email

            # CRITICAL: Verify this is an interactive session by checking cwd
            # Must be in OUR account's peers directory
            cwd = metadata.get("cwd", "")
            expected_peers_path = f"/.claude-connect/accounts/{our_email}/peers/"
            if expected_peers_path not in cwd:
                # Also check legacy path for migration
                if "/.claude-connect/peers/" not in cwd:
                    continue  # Not an interactive session, skip

            # Check file modification time (only import if stable for 60+ seconds)
            mtime = jsonl_file.stat().st_mtime
            age = time.time() - mtime
            if age < 60:
                continue  # Still being written

            # Check if already imported by comparing with markdown file mtime
            # Filename format: YYYY-MM-DD-HHMMSS_{uuid}.md
            session_id = metadata["session_id"]
            conv_dir = context_dir / "claudeconnect" / f"with-{peer_repo_name}"

            # Search for existing transcript with this UUID (may have different timestamp prefix)
            existing_transcript = None
            if conv_dir.exists():
                for md_file in conv_dir.glob(f"*_{session_id}.md"):
                    existing_transcript = md_file
                    break

            if existing_transcript and mtime <= existing_transcript.stat().st_mtime:
                continue  # Already imported and JSONL hasn't changed

            discovered.append((jsonl_file, metadata))

    return discovered


def import_transcript(
    jsonl_path: Path,
    metadata: dict,
    our_email: str,
    context_dir: Path,
) -> Path | None:
    """Import a single transcript to local context.

    Args:
        jsonl_path: Path to the JSONL file
        metadata: Metadata extracted from JSONL
        our_email: Our email address
        context_dir: Path to our context directory

    Returns:
        Path to saved markdown file, or None on failure
    """
    try:
        # Convert JSONL to markdown
        markdown_content = convert_jsonl_to_markdown(jsonl_path, metadata, our_email)

        # Determine save path
        peer_email = metadata["peer_email"]
        session_id = metadata["session_id"]
        start_time = metadata["start_time"]

        # Generate filename with timestamp: YYYY-MM-DD-HHMMSS_{uuid}.md
        timestamp_str = _format_timestamp_for_filename(start_time)
        filename = f"{timestamp_str}_{session_id}.md"

        conv_dir = context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
        conv_dir.mkdir(parents=True, exist_ok=True)

        transcript_path = conv_dir / filename

        # Save transcript
        transcript_path.write_text(markdown_content)

        return transcript_path
    except Exception as e:
        # Silently fail
        return None


def commit_transcript_to_peer(
    transcript_path: Path,
    peer_email: str,
    our_email: str,
    id_token: str,
) -> bool:
    """Upload a transcript to peer's repo via HTTP API.

    Note: Upload to our own repo is handled automatically by sync_http().
    We only need to explicitly upload to the peer's repo.

    Args:
        transcript_path: Path to the markdown transcript file
        peer_email: Peer's email (owner of destination repo)
        our_email: Our email (for conversation folder structure)
        id_token: JWT token for authentication

    Returns:
        True if successful, False otherwise
    """
    from .session import upload_file_http

    # Read transcript content
    content = transcript_path.read_bytes()
    filename = transcript_path.name

    # Determine path in peer's repo
    our_repo_name = email_to_repo_name(our_email)
    peer_repo_path = f"claudeconnect/with-{our_repo_name}/{filename}"

    # Check if file already exists on peer's server (avoid re-uploading)
    if check_file_exists_on_server(peer_email, peer_repo_path, id_token):
        return True  # Already exists, consider it success

    # Upload to peer's repo
    success = upload_file_http(
        email=peer_email,
        path=peer_repo_path,
        content=content,
        id_token=id_token,
        encrypt_for=peer_email,  # Encrypt with peer's key if enabled
        use_friend_key=True,
        our_email=our_email,
    )

    return success
