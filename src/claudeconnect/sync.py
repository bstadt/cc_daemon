"""Sync loop for Claude Connect.

Handles bidirectional syncing between local context directory and SVN repo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .svn_ops import SvnClient, SvnError
from .config import get_tokens, get_config, Config

logger = logging.getLogger(__name__)


class SyncLoop:
    """Background sync loop for keeping local context in sync with SVN."""

    def __init__(
        self,
        context_dir: Path,
        repo_url: str,
        token: str,
        email: str,
        interval: int = 30,
    ):
        """
        Initialize sync loop.

        Args:
            context_dir: Local context directory
            repo_url: SVN repository URL
            token: Fernet SVN token
            email: User email (SVN username)
            interval: Sync interval in seconds
        """
        self.context_dir = Path(context_dir)
        self.repo_url = repo_url
        self.token = token
        self.email = email
        self.interval = interval
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self.svn = SvnClient(context_dir, repo_url, token, email)

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
        """Perform a single sync cycle."""
        # Run SVN operations in thread pool to avoid blocking
        loop = asyncio.get_event_loop()

        # Clean up any stale locks before operations
        await loop.run_in_executor(None, self.svn.cleanup)

        # Pull remote changes
        try:
            updated = await loop.run_in_executor(None, self.svn.update)
            if updated:
                logger.info(f"Pulled {len(updated)} updates: {updated}")
        except SvnError as e:
            logger.error(f"Update failed: {e}")
            return

        # Check for conflicts
        status = await loop.run_in_executor(None, self.svn.status)
        if status.has_conflicts:
            for path in status.conflicted:
                logger.warning(f"Conflict detected: {path}")
                # Auto-resolve with local version, keep theirs as backup
                await self._handle_conflict(path)

        # Add new markdown files
        added = await loop.run_in_executor(None, self.svn.add_all_markdown)
        if added:
            logger.info(f"Added {len(added)} new files: {added}")

        # Ensure authz file is tracked (it's not a .md file so add_all_markdown won't catch it)
        authz_path = self.context_dir / "authz"
        if authz_path.exists():
            authz_added = await loop.run_in_executor(None, self.svn.add, Path("authz"))
            if authz_added:
                added.append(Path("authz"))
                logger.info("Added authz file")

        # Delete missing files (removed from filesystem)
        deleted = await loop.run_in_executor(None, self.svn.delete_missing)
        if deleted:
            logger.info(f"Deleted {len(deleted)} missing files: {deleted}")

        # Commit local changes
        if status.has_changes or added or deleted:
            try:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"Auto-sync {timestamp}"
                rev = await loop.run_in_executor(
                    None, self.svn.commit, message
                )
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

        # SVN creates files like: file.md.r123, file.md.r456
        # Find the "theirs" version
        working_path = self.context_dir / path

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


def sync_once(context_dir: Path, repo_url: str, svn_token: str, email: str) -> bool:
    """
    Perform a single synchronous sync.

    Returns:
        True if sync completed successfully.
    """
    svn = SvnClient(context_dir, repo_url, svn_token, email)

    try:
        # Clean up any stale locks from previous sessions
        svn.cleanup()

        # Pull updates
        updated = svn.update()
        if updated:
            print(f"  Pulled {len(updated)} files")

        # Add new markdown files
        added = svn.add_all_markdown()
        if added:
            print(f"  Added {len(added)} files")

        # Ensure authz file is tracked (it's not a .md file so add_all_markdown won't catch it)
        authz_path = context_dir / "authz"
        if authz_path.exists() and svn.add(Path("authz")):
            added.append(Path("authz"))
            print(f"  Added authz file")

        # Delete missing files
        deleted = svn.delete_missing()
        if deleted:
            print(f"  Deleted {len(deleted)} files")

        # Commit
        status = svn.status()
        if status.has_changes or added or deleted:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rev = svn.commit(f"Auto-sync {timestamp}")
            if rev:
                print(f"  Committed revision {rev}")

        return True

    except SvnError as e:
        print(f"  Sync error: {e}")
        return False
