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
        DEFAULT_KEYS_DIR,
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

        # Check for conflicts in shadow dir
        status = await loop.run_in_executor(None, self.svn.status)
        if status.has_conflicts:
            for path in status.conflicted:
                logger.warning(f"Conflict detected: {path}")
                await self._handle_conflict(path)

        # OUTBOUND: Copy changed files from context to shadow (encrypting), then commit
        # First, detect what changed in context dir
        changed_files = await loop.run_in_executor(
            None, _detect_context_changes, self.context_dir, self.shadow_dir
        )

        if changed_files['modified'] or changed_files['added'] or changed_files['deleted']:
            logger.info(f"Context changes: {len(changed_files['modified'])} modified, "
                       f"{len(changed_files['added'])} added, {len(changed_files['deleted'])} deleted")

            # Copy changed/added files from context to shadow (encrypting)
            await loop.run_in_executor(
                None, _copy_to_shadow, self.context_dir, self.shadow_dir,
                changed_files['modified'] + changed_files['added'],
                self.email, self.encryption_enabled
            )

            # Add new files to SVN
            for path in changed_files['added']:
                await loop.run_in_executor(None, self.svn.add, path, True)

            # Handle deleted files in shadow
            for path in changed_files['deleted']:
                shadow_path = self.shadow_dir / path
                if shadow_path.exists():
                    shadow_path.unlink()

            # Delete missing files from SVN
            deleted = await loop.run_in_executor(None, self.svn.delete_missing)
            if deleted:
                logger.info(f"Marked {len(deleted)} files for deletion in SVN")

            # Commit changes
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"Auto-sync {timestamp}"
                rev = await loop.run_in_executor(None, self.svn.commit, message)
                if rev:
                    logger.info(f"Committed revision {rev}")
            except SvnError as e:
                logger.error(f"Commit failed: {e}")

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

        # OUTBOUND: Detect changes in context dir
        changed_files = _detect_context_changes(context_dir, shadow_dir)

        added_count = 0
        if changed_files['modified'] or changed_files['added'] or changed_files['deleted']:
            # Copy changed/added files from context to shadow (encrypting)
            _copy_to_shadow(
                context_dir, shadow_dir,
                changed_files['modified'] + changed_files['added'],
                email, encryption_enabled
            )

            # Add new files to SVN
            for path in changed_files['added']:
                if svn.add(path, parents=True):
                    added_count += 1

            if added_count:
                print(f"  Added {added_count} files")

            # Handle deleted files
            for path in changed_files['deleted']:
                shadow_path = shadow_dir / path
                if shadow_path.exists():
                    shadow_path.unlink()

            # Delete missing from SVN
            deleted = svn.delete_missing()
            if deleted:
                print(f"  Deleted {len(deleted)} files")

            # Commit
            status = svn.status()
            if status.has_changes or added_count or deleted:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rev = svn.commit(f"Auto-sync {timestamp}")
                if rev:
                    print(f"  Committed revision {rev}")

        return True

    except SvnError as e:
        print(f"  Sync error: {e}")
        return False


def _detect_context_changes(context_dir: Path, shadow_dir: Path) -> dict:
    """
    Detect what files changed in context_dir compared to shadow_dir.

    Returns:
        Dict with 'modified', 'added', 'deleted' lists of relative paths
    """
    changes = {'modified': [], 'added': [], 'deleted': []}

    # Find all markdown files in context dir
    context_files = set()
    for path in context_dir.rglob("*.md"):
        if ".svn" not in path.parts:
            rel_path = path.relative_to(context_dir)
            context_files.add(rel_path)

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

    # Find deleted files (in shadow but not in context)
    for path in shadow_dir.rglob("*.md"):
        if ".svn" not in path.parts:
            rel_path = path.relative_to(shadow_dir)
            if rel_path not in context_files:
                changes['deleted'].append(rel_path)

    return changes


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
                    content = encrypt_file_with_master_key(content)
                    logger.debug(f"Encrypted {rel_path} for shadow dir")
                except Exception as e:
                    logger.error(f"Failed to encrypt {rel_path}: {e}")

        # Write to shadow dir
        shadow_path.write_bytes(content)


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

        # Ensure parent directory exists in context
        context_path.parent.mkdir(parents=True, exist_ok=True)

        # Read from shadow (may be encrypted)
        content = shadow_path.read_bytes()

        # Decrypt if needed
        if encryption_enabled and HAS_ENCRYPTION:
            if should_encrypt_file(rel_path) and is_encrypted_file(content):
                try:
                    content = decrypt_file_with_master_key(content)
                    logger.debug(f"Decrypted {rel_path} for context dir")
                except Exception as e:
                    logger.warning(f"Could not decrypt {rel_path}: {e}")
                    continue

        # Write plaintext to context dir
        context_path.write_bytes(content)


