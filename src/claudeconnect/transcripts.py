"""Interactive session transcript management.

Handles discovery and import of Claude Code interactive session transcripts.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from .config import email_to_repo_name, get_pending_sessions_dir
from .session import upload_file_http


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
    from the pending session file.
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


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_matching_jsonl(claude_projects: Path, session_id: str) -> Path | None:
    for jsonl_file in claude_projects.rglob(f"{session_id}.jsonl"):
        # Skip subagent transcripts (they're in subdirectories named after session ID)
        if jsonl_file.parent.name == session_id:
            continue
        return jsonl_file
    return None


def cleanup_orphaned_pending_sessions(
    our_email: str,
    context_dir: Path,
    max_age_hours: int = 24,
) -> int:
    """Remove pending session files after inactivity across session + conversation files."""
    pending_dir = get_pending_sessions_dir(our_email)
    if not pending_dir.exists():
        return 0

    claude_projects = Path.home() / ".claude" / "projects"
    max_age_seconds = max_age_hours * 3600
    now = time.time()
    cleaned = 0

    for pending_file in pending_dir.glob("*.json"):
        try:
            pending_data = {}
            try:
                pending_data = json.loads(pending_file.read_text())
            except (json.JSONDecodeError, OSError):
                pending_data = {}

            last_activity = pending_file.stat().st_mtime

            session_id = pending_file.stem
            jsonl_path = _find_matching_jsonl(claude_projects, session_id) if claude_projects.exists() else None

            if jsonl_path and jsonl_path.exists():
                last_activity = max(last_activity, jsonl_path.stat().st_mtime)

            peer_email = pending_data.get("peer_email")
            transcript_mtime = None
            if peer_email:
                conv_dir = context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
                if conv_dir.exists():
                    for md_file in conv_dir.glob(f"*_{session_id}.md"):
                        mtime = md_file.stat().st_mtime
                        transcript_mtime = mtime if transcript_mtime is None else max(transcript_mtime, mtime)
            else:
                conv_root = context_dir / "claudeconnect"
                if conv_root.exists():
                    for md_file in conv_root.rglob(f"*_{session_id}.md"):
                        mtime = md_file.stat().st_mtime
                        transcript_mtime = mtime if transcript_mtime is None else max(transcript_mtime, mtime)

            if transcript_mtime is not None:
                last_activity = max(last_activity, transcript_mtime)

            if (now - last_activity) >= max_age_seconds:
                pending_file.unlink()
                cleaned += 1
        except OSError:
            pass  # Ignore errors

    return cleaned


def discover_new_interactive_transcripts(our_email: str, context_dir: Path) -> list[tuple[Path, dict]]:
    """Find new Claude Code transcripts from ClaudeConnect interactive sessions.

    Uses pending session files to match sessions we started with their transcripts.
    This is multi-account safe - only imports sessions from pending-sessions/ dir
    for this specific account.

    Args:
        our_email: Our email address
        context_dir: Path to our context directory

    Returns:
        List of (jsonl_file_path, metadata_dict) tuples for unprocessed transcripts
    """
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.exists():
        return []

    # Get pending sessions directory for this account
    pending_dir = get_pending_sessions_dir(our_email)
    if not pending_dir.exists():
        return []

    discovered = []

    # Check each pending session file
    for pending_file in pending_dir.glob("*.json"):
        session_id = pending_file.stem  # UUID is the filename without extension

        # Read pending session data
        try:
            pending_data = json.loads(pending_file.read_text())
            peer_email = pending_data.get("peer_email")
            if not peer_email:
                continue
        except (json.JSONDecodeError, OSError):
            continue

        # Search for matching JSONL file by UUID in ~/.claude/projects/
        # The JSONL is named {uuid}.jsonl somewhere in the projects tree
        matching_jsonl = _find_matching_jsonl(claude_projects, session_id)

        if not matching_jsonl:
            continue  # Session hasn't created transcript yet

        mtime = matching_jsonl.stat().st_mtime

        # Extract metadata from JSONL
        metadata = _extract_jsonl_metadata(matching_jsonl)
        if not metadata:
            continue

        # Add peer email from pending file (authoritative source)
        metadata["peer_email"] = peer_email
        # Also store pending file path for cleanup after import
        metadata["pending_file"] = str(pending_file)

        # Check if already imported
        peer_repo_name = email_to_repo_name(peer_email)
        conv_dir = context_dir / "claudeconnect" / f"with-{peer_repo_name}"

        existing_transcript = None
        if conv_dir.exists():
            for md_file in conv_dir.glob(f"*_{session_id}.md"):
                existing_transcript = md_file
                break

        if existing_transcript and mtime <= existing_transcript.stat().st_mtime:
            continue  # Already imported and JSONL hasn't changed

        discovered.append((matching_jsonl, metadata))

    # Clean up orphaned pending sessions (inactive for 24 hours)
    cleanup_orphaned_pending_sessions(our_email, context_dir)

    return discovered


def import_transcript(
    jsonl_path: Path,
    metadata: dict,
    our_email: str,
    context_dir: Path,
    id_token: str | None = None,
) -> Path | None:
    """Import a single transcript to local context.

    Args:
        jsonl_path: Path to the JSONL file
        metadata: Metadata extracted from JSONL (may include 'pending_file' path)
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

        # Upload transcript to peer's repo if token available
        if id_token:
            peer_transcript_rel_path = (
                f"claudeconnect/with-{email_to_repo_name(our_email)}/{filename}"
            )
            try:
                upload_file_http(
                    peer_email,
                    peer_transcript_rel_path,
                    markdown_content.encode(),
                    id_token,
                    encrypt_for=peer_email,
                    use_friend_key=True,
                    our_email=our_email,
                )
            except Exception:
                pass

        # Update pending session metadata (don't delete yet; cleanup handles inactivity)
        pending_file_path = metadata.get("pending_file")
        if pending_file_path:
            try:
                pending_path = Path(pending_file_path)
                pending_data = {}
                try:
                    pending_data = json.loads(pending_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pending_data = {}
                if metadata.get("peer_email"):
                    pending_data.setdefault("peer_email", metadata.get("peer_email"))
                pending_data["last_imported_at"] = datetime.now().isoformat()
                pending_data["last_jsonl_mtime"] = jsonl_path.stat().st_mtime
                pending_path.write_text(json.dumps(pending_data))
            except OSError:
                pass  # Ignore cleanup errors

        return transcript_path
    except Exception as e:
        # Silently fail
        return None
