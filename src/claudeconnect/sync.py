"""Sync loop for Claude Connect.

Handles bidirectional syncing between local context directory and SVN repo
using shadow directory architecture.

Shadow directory architecture:
- context_dir: User's plaintext files (no .svn/)
- shadow_dir: SVN working copy with encrypted files (~/.claude-connect/svn-staging/<email>/)

Sync flow:
- OUTBOUND: context_dir → (encrypt) → shadow_dir → SVN commit
- INBOUND: SVN update → shadow_dir → (decrypt) → context_dir

When encryption is enabled, files are encrypted when copying to shadow_dir
and decrypted when copying from shadow_dir. This provides zero-trust context
sharing where the SVN server only ever sees encrypted blobs and private keys
never leave the user's machine.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .svn_ops import SvnClient, SvnError
from .config import get_tokens, get_config, Config, get_shadow_dir

# Encryption is optional
try:
    from .encryption import (
        is_encryption_available,
        should_encrypt_file,
        is_encrypted_file,
        encrypt_file_with_master_key,
        decrypt_file_with_master_key,
        load_master_key,
        get_keys_dir,
        DEFAULT_FRIENDS_DIR,
    )
    HAS_ENCRYPTION = is_encryption_available()
except ImportError:
    HAS_ENCRYPTION = False

logger = logging.getLogger(__name__)


class SyncLoop:
    """Background sync loop for keeping local context in sync with SVN.

    Uses shadow directory architecture where SVN operations happen in
    ~/.claude-connect/svn-staging/<email>/ and files are copied to/from
    the user's context directory with encryption/decryption.
    """

    def __init__(
        self,
        context_dir: Path,
        repo_url: str,
        token: str,
        email: str,
        interval: int = 30,
        kms_key_id: Optional[str] = None,  # Kept for backwards compat, ignored
    ):
        """
        Initialize sync loop.

        Args:
            context_dir: User's plaintext context directory
            repo_url: SVN repository URL
            token: Fernet SVN token
            email: User email (SVN username)
            interval: Sync interval in seconds
            kms_key_id: Deprecated, ignored (use config.encryption_enabled)
        """
        self.context_dir = Path(context_dir)
        self.repo_url = repo_url
        self.token = token
        self.email = email
        self.interval = interval
        self.running = False
        self._task: Optional[asyncio.Task] = None

        # Shadow directory is where SVN operations happen
        self.shadow_dir = get_shadow_dir(email)
        self.svn = SvnClient(self.shadow_dir, repo_url, token, email)

        # Check if encryption is enabled via config
        config = get_config()
        self.encryption_enabled = config.encryption_enabled and HAS_ENCRYPTION

        if config.encryption_enabled and not HAS_ENCRYPTION:
            logger.warning("Encryption enabled but cryptography not available")

    async def start(self):
        """Start the sync loop."""
        self.running = True
        self._task = asyncio.create_task(self._run())
        logger.info(f"Sync loop started (interval: {self.interval}s)")

    async def stop(self):
        """Stop the sync loop."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Sync loop stopped")

    async def _run(self):
        """Main sync loop."""
        while self.running:
            try:
                await self._sync_once()
            except Exception as e:
                logger.error(f"Sync error: {e}")

            await asyncio.sleep(self.interval)

    async def _sync_once(self):
        """Perform a single sync cycle using shadow directory architecture."""
        loop = asyncio.get_event_loop()

        # Clean up any stale locks before operations
        await loop.run_in_executor(None, self.svn.cleanup)

        # Check for new interactive transcripts and commit to peer repos
        await loop.run_in_executor(None, self._commit_interactive_transcripts_to_peers)

        # INBOUND: Pull remote changes to shadow dir, then copy to context dir
        try:
            updated = await loop.run_in_executor(None, self.svn.update)
            if updated:
                logger.info(f"Pulled {len(updated)} updates: {updated}")
                # Copy updated files from shadow to context (decrypting)
                await loop.run_in_executor(
                    None, _copy_from_shadow, self.shadow_dir, self.context_dir,
                    updated, self.email, self.encryption_enabled
                )
        except SvnError as e:
            logger.error(f"Update failed: {e}")
            return

        # Also ensure any files in shadow are in context (handles crashed syncs)
        missing_in_context = await loop.run_in_executor(
            None, _find_missing_in_context, self.shadow_dir, self.context_dir
        )
        if missing_in_context:
            logger.info(f"Syncing {len(missing_in_context)} missing files to context")
            await loop.run_in_executor(
                None, _copy_from_shadow, self.shadow_dir, self.context_dir,
                missing_in_context, self.email, self.encryption_enabled
            )

        # Check for conflicts in shadow dir
        status = await loop.run_in_executor(None, self.svn.status)
        if status.has_conflicts:
            for path in status.conflicted:
                logger.warning(f"Conflict detected: {path}")
                await self._handle_conflict(path)

        # Import new Claude Code transcripts from interactive sessions
        await loop.run_in_executor(None, self._import_claude_code_transcripts)

        # OUTBOUND: Copy changed files from context to shadow (encrypting), then commit
        # First, detect what changed in context dir
        changed_files = await loop.run_in_executor(
            None, _detect_context_changes, self.context_dir, self.shadow_dir
        )

        if changed_files['modified'] or changed_files['added']:
            logger.info(f"Context changes: {len(changed_files['modified'])} modified, "
                       f"{len(changed_files['added'])} added")

            # Copy changed/added files from context to shadow (encrypting)
            await loop.run_in_executor(
                None, _copy_to_shadow, self.context_dir, self.shadow_dir,
                changed_files['modified'] + changed_files['added'],
                self.email, self.encryption_enabled
            )

            # Add new files to SVN
            for path in changed_files['added']:
                await loop.run_in_executor(None, self.svn.add, path, True)

            # NOTE: No auto-deletion - users must explicitly delete via skill

            # Commit changes
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"Auto-sync {timestamp}"
                rev = await loop.run_in_executor(None, self.svn.commit, message)
                if rev:
                    logger.info(f"Committed revision {rev}")
            except SvnError as e:
                logger.error(f"Commit failed: {e}")

    def _commit_interactive_transcripts_to_peers(self):
        """
        Check for interactive session transcripts and commit them to peer repos.

        Interactive transcripts are identified by **Type**: interactive in the header.
        SVN will automatically skip commits if there are no changes.
        """
        # TODO: Temporarily disabled while SVN implementation is being reworked
        # Transcripts are saved locally and will be committed when new SVN is ready
        return

        from .session import commit_interactive_transcript

        # Look for interactive transcripts in conversation folders
        claudeconnect_dir = self.context_dir / "claudeconnect"
        if not claudeconnect_dir.exists():
            return

        for conv_dir in claudeconnect_dir.glob("with-*"):
            for transcript_path in conv_dir.glob("*.md"):
                # Check if it's an interactive transcript
                try:
                    content = transcript_path.read_text()
                    if "**Type**: interactive" not in content:
                        continue

                    # Extract session_id from filename
                    session_id = transcript_path.stem

                    # Commit to peer's repo (SVN will skip if no changes)
                    logger.info(f"Committing interactive transcript to peer: {session_id}")
                    commit_interactive_transcript(session_id, verbose=False)

                except Exception as e:
                    logger.error(f"Error committing transcript {transcript_path.name}: {e}")

    def _discover_claude_code_transcripts(self) -> list[tuple[Path, dict]]:
        """Find new Claude Code transcripts from ClaudeConnect interactive sessions.

        ONLY imports transcripts where cwd points to a peer directory.
        Does NOT import user's normal Claude Code usage.

        Returns:
            List of (jsonl_file_path, metadata_dict) tuples for unprocessed transcripts
        """
        from .session import context_dir_to_claude_projects_dir, _extract_jsonl_metadata

        claude_projects = Path.home() / ".claude" / "projects"
        if not claude_projects.exists():
            return []

        peers_dir = Path.home() / ".claude-connect" / "peers"
        if not peers_dir.exists():
            return []

        discovered = []

        # Check each peer's context directory for corresponding project transcripts
        for peer_dir in peers_dir.iterdir():
            if not peer_dir.is_dir():
                continue

            # Map to expected ~/.claude/projects/ subdirectory
            # e.g., ~/.claude-connect/peers/brandon-calcifercomputing-com
            #    → ~/.claude/projects/-Users-frsc--claude-connect-peers-brandon-calcifercomputing-com
            projects_subdir_name = context_dir_to_claude_projects_dir(peer_dir)
            projects_subdir = claude_projects / projects_subdir_name

            if not projects_subdir.exists():
                continue

            # Find .jsonl conversation files
            for jsonl_file in projects_subdir.glob("*.jsonl"):
                # Skip subagent transcripts (in subdirectories)
                if jsonl_file.parent != projects_subdir:
                    continue

                # CRITICAL: Verify this is an interactive session by checking cwd
                metadata = _extract_jsonl_metadata(jsonl_file)
                if not metadata:
                    continue

                # Only import if cwd points to a peer directory
                cwd = metadata.get("cwd", "")
                if "/.claude-connect/peers/" not in cwd:
                    continue  # Not an interactive session, skip

                # Check file modification time (only import if stable for 60+ seconds)
                mtime = jsonl_file.stat().st_mtime
                age = time.time() - mtime
                if age < 60:
                    continue  # Still being written

                # Check if already imported by comparing with markdown file mtime
                peer_email = metadata["peer_email"]
                session_id = metadata["session_id"]
                from .svn_ops import email_to_repo_name
                conv_dir = self.context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
                md_file = conv_dir / f"{session_id}.md"

                if md_file.exists() and mtime <= md_file.stat().st_mtime:
                    continue  # Already imported and JSONL hasn't changed

                discovered.append((jsonl_file, metadata))

        return discovered

    def _import_claude_code_transcripts(self):
        """Import new transcripts from ~/.claude/projects/ to ClaudeConnect."""
        from .session import convert_jsonl_to_markdown, commit_interactive_transcript
        from .svn_ops import email_to_repo_name

        discovered = self._discover_claude_code_transcripts()

        for jsonl_path, metadata in discovered:
            try:
                # Convert JSONL to markdown
                markdown_content = convert_jsonl_to_markdown(jsonl_path, metadata, self.email)

                # Determine save path in our context
                peer_email = metadata["peer_email"]
                session_id = metadata["session_id"]

                conv_dir = self.context_dir / "claudeconnect" / f"with-{email_to_repo_name(peer_email)}"
                conv_dir.mkdir(parents=True, exist_ok=True)

                transcript_path = conv_dir / f"{session_id}.md"

                # Save transcript
                transcript_path.write_text(markdown_content)

                logger.info(f"Imported transcript: {session_id} from {peer_email}")

                # Commit to peer's repo (reuse existing logic, silent)
                commit_interactive_transcript(session_id, verbose=False)

            except Exception as e:
                logger.error(f"Failed to import {jsonl_path}: {e}")

    async def _handle_conflict(self, path: Path):
        """
        Handle a conflicted file.

        Strategy: Keep local version, save remote version as .theirs.md
        """
        loop = asyncio.get_event_loop()

        # SVN creates files like: file.md.r123, file.md.r456 in shadow dir
        working_path = self.shadow_dir / path

        for conflict_file in working_path.parent.glob(f"{working_path.stem}*.r*"):
            # Rename to .theirs.md
            theirs_path = working_path.with_suffix(".theirs.md")
            conflict_file.rename(theirs_path)
            logger.info(f"Saved remote version as {theirs_path}")
            break

        # Resolve with our version
        await loop.run_in_executor(
            None, self.svn.resolve_conflict, path, "mine-full"
        )
        logger.info(f"Resolved conflict in {path} (kept local version)")


def sync_once(
    context_dir: Path,
    repo_url: str,
    svn_token: str,
    email: str,
    kms_key_id: Optional[str] = None,  # Kept for backwards compat, ignored
) -> bool:
    """
    Perform a single synchronous sync using shadow directory architecture.

    Args:
        context_dir: User's plaintext context directory
        repo_url: SVN repository URL
        svn_token: Fernet SVN token
        email: User email (SVN username)
        kms_key_id: Deprecated, ignored (use config.encryption_enabled)

    Returns:
        True if sync completed successfully.
    """
    # Shadow directory is where SVN operations happen
    shadow_dir = get_shadow_dir(email)
    svn = SvnClient(shadow_dir, repo_url, svn_token, email)

    # Check if encryption is enabled via config
    config = get_config()
    encryption_enabled = config.encryption_enabled and HAS_ENCRYPTION

    if config.encryption_enabled and not HAS_ENCRYPTION:
        print("  Warning: Encryption enabled but cryptography not available")

    try:
        # Clean up any stale locks from previous sessions
        svn.cleanup()

        # INBOUND: Pull updates to shadow dir
        updated = svn.update()
        if updated:
            print(f"  Pulled {len(updated)} files")
            # Copy from shadow to context (decrypting)
            _copy_from_shadow(shadow_dir, context_dir, updated, email, encryption_enabled)

        # Also ensure any files in shadow (especially claudeconnect/) are in context
        # This handles cases where a previous sync crashed mid-copy
        missing_in_context = _find_missing_in_context(shadow_dir, context_dir)
        if missing_in_context:
            print(f"  Syncing {len(missing_in_context)} missing files to context")
            _copy_from_shadow(shadow_dir, context_dir, missing_in_context, email, encryption_enabled)

        # OUTBOUND: Detect changes in context dir
        changed_files = _detect_context_changes(context_dir, shadow_dir)

        all_changes = changed_files['modified'] + changed_files['added']
        total_changes = len(all_changes)

        added_count = 0
        total_committed = 0

        if total_changes > 0:
            print(f"  Processing {total_changes} files ({len(changed_files['added'])} new, {len(changed_files['modified'])} modified)")

            # For large batches, commit in chunks for better progress and reliability
            BATCH_SIZE = 100
            if total_changes >= BATCH_SIZE:
                print(f"  Using batched commits ({BATCH_SIZE} files per commit)...")

                # Process in batches
                for batch_start in range(0, total_changes, BATCH_SIZE):
                    batch_end = min(batch_start + BATCH_SIZE, total_changes)
                    batch = all_changes[batch_start:batch_end]
                    batch_num = (batch_start // BATCH_SIZE) + 1
                    total_batches = (total_changes + BATCH_SIZE - 1) // BATCH_SIZE

                    # Copy batch to shadow (encrypting)
                    _copy_to_shadow(context_dir, shadow_dir, batch, email, encryption_enabled)

                    # Add new files in this batch
                    new_in_batch = [f for f in batch if f in changed_files['added']]
                    if new_in_batch:
                        added, failed = svn.add_batch(new_in_batch)
                        added_count += len(added)

                    # Commit this batch
                    status = svn.status()
                    if status.has_changes:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        rev = svn.commit(f"Batch {batch_num}/{total_batches}: {len(batch)} files ({timestamp})")
                        if rev:
                            total_committed += len(batch)
                            print(f"  [{total_committed}/{total_changes}] Committed batch {batch_num}/{total_batches} (rev {rev})")

            else:
                # Small batch - single commit as before
                _copy_to_shadow_with_progress(
                    context_dir, shadow_dir,
                    all_changes,
                    email, encryption_enabled
                )

                # Add new files to SVN using batch operation
                if changed_files['added']:
                    added, failed = svn.add_batch(changed_files['added'])
                    added_count = len(added)
                    if failed:
                        print(f"  Warning: {len(failed)} files failed to add")

                # NOTE: No auto-deletion - users must explicitly delete via skill

                # Commit
                status = svn.status()
                if status.has_changes or added_count:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rev = svn.commit(f"Auto-sync {timestamp}")
                    if rev:
                        total_committed = total_changes
                        print(f"  Committed revision {rev}")

            if added_count:
                print(f"  Added {added_count} new files total")

        return True

    except SvnError as e:
        print(f"  Sync error: {e}")
        return False


def _detect_context_changes(context_dir: Path, shadow_dir: Path) -> dict:
    """
    Detect what files changed in context_dir compared to shadow_dir.

    NOTE: Sync never auto-deletes files. Deletions require explicit user action
    via a delete skill that runs `svn delete`. This prevents accidental data loss.

    Returns:
        Dict with 'modified', 'added' lists of relative paths (no 'deleted')
    """
    changes = {'modified': [], 'added': [], 'deleted': []}

    # Find all markdown files in context dir
    for path in context_dir.rglob("*.md"):
        if ".svn" not in path.parts:
            rel_path = path.relative_to(context_dir)

            shadow_path = shadow_dir / rel_path
            if shadow_path.exists():
                # Check if modified (compare mtimes or content)
                if path.stat().st_mtime > shadow_path.stat().st_mtime:
                    changes['modified'].append(rel_path)
            else:
                changes['added'].append(rel_path)

    # Also check authz file
    context_authz = context_dir / "authz"
    shadow_authz = shadow_dir / "authz"
    if context_authz.exists():
        if shadow_authz.exists():
            if context_authz.stat().st_mtime > shadow_authz.stat().st_mtime:
                changes['modified'].append(Path("authz"))
        else:
            changes['added'].append(Path("authz"))

    # NOTE: No delete detection - sync only adds/updates, never deletes
    # Users must explicitly delete files via a delete skill

    return changes


def _find_missing_in_context(shadow_dir: Path, context_dir: Path) -> list[Path]:
    """
    Find files in shadow_dir that are missing from context_dir.

    This handles cases where a previous sync crashed mid-copy,
    leaving files in shadow but not in context.

    Returns:
        List of relative paths that need to be copied to context
    """
    missing = []

    for path in shadow_dir.rglob("*.md"):
        if ".svn" not in path.parts:
            rel_path = path.relative_to(shadow_dir)
            context_path = context_dir / rel_path
            if not context_path.exists():
                missing.append(rel_path)

    return missing


def _copy_to_shadow(
    context_dir: Path,
    shadow_dir: Path,
    files: list[Path],
    email: str,
    encryption_enabled: bool
) -> None:
    """
    Copy files from context_dir to shadow_dir, encrypting if enabled.

    Uses master key encryption (v2) - simpler than multi-recipient model.
    All files are encrypted with user's master AES key.

    Args:
        context_dir: User's plaintext directory
        shadow_dir: SVN working copy directory
        files: List of relative paths to copy
        email: User's email (unused in v2, kept for API compat)
        encryption_enabled: Whether to encrypt files
    """
    for rel_path in files:
        context_path = context_dir / rel_path
        shadow_path = shadow_dir / rel_path

        if not context_path.exists():
            continue

        # Ensure parent directory exists in shadow
        shadow_path.parent.mkdir(parents=True, exist_ok=True)

        # Read plaintext from context
        content = context_path.read_bytes()

        # Encrypt if enabled and file should be encrypted
        if encryption_enabled and HAS_ENCRYPTION:
            if should_encrypt_file(rel_path) and not is_encrypted_file(content):
                try:
                    content = encrypt_file_with_master_key(content, email)
                    logger.debug(f"Encrypted {rel_path} for shadow dir")
                except Exception as e:
                    logger.error(f"Failed to encrypt {rel_path}: {e}")

        # Write to shadow dir
        shadow_path.write_bytes(content)


def _copy_to_shadow_with_progress(
    context_dir: Path,
    shadow_dir: Path,
    files: list[Path],
    email: str,
    encryption_enabled: bool
) -> None:
    """
    Copy files from context_dir to shadow_dir with progress output.

    Same as _copy_to_shadow but prints progress for large batches.

    Args:
        context_dir: User's plaintext directory
        shadow_dir: SVN working copy directory
        files: List of relative paths to copy
        email: User's email (unused in v2, kept for API compat)
        encryption_enabled: Whether to encrypt files
    """
    total = len(files)
    # Only show progress for large batches
    show_progress = total >= 20
    progress_interval = max(1, total // 10)  # Show ~10 progress updates

    for i, rel_path in enumerate(files, 1):
        context_path = context_dir / rel_path
        shadow_path = shadow_dir / rel_path

        if not context_path.exists():
            continue

        # Ensure parent directory exists in shadow
        shadow_path.parent.mkdir(parents=True, exist_ok=True)

        # Read plaintext from context
        content = context_path.read_bytes()

        # Encrypt if enabled and file should be encrypted
        if encryption_enabled and HAS_ENCRYPTION:
            if should_encrypt_file(rel_path) and not is_encrypted_file(content):
                try:
                    content = encrypt_file_with_master_key(content, email)
                except Exception as e:
                    logger.error(f"Failed to encrypt {rel_path}: {e}")

        # Write to shadow dir
        shadow_path.write_bytes(content)

        # Progress output
        if show_progress and (i % progress_interval == 0 or i == total):
            print(f"  [{i}/{total}] Encrypting and staging files...")


def _copy_from_shadow(
    shadow_dir: Path,
    context_dir: Path,
    files: list[Path],
    email: str,
    encryption_enabled: bool
) -> None:
    """
    Copy files from shadow_dir to context_dir, decrypting if needed.

    Uses master key decryption (v2) - files are encrypted with user's master key.

    Args:
        shadow_dir: SVN working copy directory
        context_dir: User's plaintext directory
        files: List of relative paths to copy
        email: User's email (unused in v2, kept for API compat)
        encryption_enabled: Whether encryption is enabled
    """
    for rel_path in files:
        shadow_path = shadow_dir / rel_path
        context_path = context_dir / rel_path

        if not shadow_path.exists():
            # File was deleted on remote - delete locally too
            if context_path.exists():
                context_path.unlink()
                logger.debug(f"Deleted {rel_path} from context dir")
            continue

        # Skip directories (SVN update returns both files and dirs)
        if shadow_path.is_dir():
            context_path.mkdir(parents=True, exist_ok=True)
            continue

        # Ensure parent directory exists in context
        context_path.parent.mkdir(parents=True, exist_ok=True)

        # Read from shadow (may be encrypted)
        content = shadow_path.read_bytes()

        # Decrypt if needed
        if encryption_enabled and HAS_ENCRYPTION:
            if should_encrypt_file(rel_path) and is_encrypted_file(content):
                try:
                    content = decrypt_file_with_master_key(content, email)
                    logger.debug(f"Decrypted {rel_path} for context dir")
                except Exception as e:
                    logger.warning(f"Could not decrypt {rel_path}: {e}")
                    continue

        # Write plaintext to context dir
        context_path.write_bytes(content)


