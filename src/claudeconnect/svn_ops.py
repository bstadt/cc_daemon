"""SVN operations wrapper for Claude Connect.

Wraps the SVN CLI with clean Python functions for checkout, update, commit, etc.
All operations use Bearer token authentication for claudeconnect.io.
"""

from __future__ import annotations

import os
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List

from .config import SVN_BASE_URL


@dataclass
class SvnStatus:
    """Status of an SVN working copy."""
    modified: list[Path] = field(default_factory=list)
    added: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)
    unversioned: list[Path] = field(default_factory=list)
    conflicted: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)  # Files deleted from filesystem but not svn

    @property
    def has_changes(self) -> bool:
        return bool(self.modified or self.added or self.deleted)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicted)


class SvnError(Exception):
    """SVN operation failed."""
    pass


class SvnLockError(SvnError):
    """SVN working copy is locked."""
    pass


def is_lock_error(stderr: str) -> bool:
    """Check if an SVN error is a working copy lock error."""
    return "E155004" in stderr or "is already locked" in stderr


class SvnClient:
    """SVN client for Claude Connect operations."""

    def __init__(self, working_dir: Path, repo_url: str, password: str, username: str = "oauth"):
        """
        Initialize SVN client.

        Args:
            working_dir: Local working directory (e.g., ~/claude)
            repo_url: SVN repository URL (e.g., https://claudeconnect.io/svn/brandon-gmail-com)
            password: SVN password
            username: SVN username (default: oauth for token auth)
        """
        self.working_dir = Path(working_dir)
        self.repo_url = repo_url
        self.username = username
        self.password = password

    def _run(self, args: list[str], cwd: Optional[Path] = None, timeout: int = 120) -> subprocess.CompletedProcess:
        """Run an SVN command with authentication.

        Args:
            args: SVN command arguments
            cwd: Working directory override
            timeout: Command timeout in seconds (default 120)
        """
        cmd = [
            "svn",
            "--non-interactive",
            "--no-auth-cache",
            "--username", self.username,
            "--password", self.password,
            *args
        ]

        # Use system CA certs (fixes Homebrew OpenSSL not having Let's Encrypt roots)
        # DYLD_LIBRARY_PATH: On macOS, Homebrew's SVN may be compiled against a newer
        # SQLite than the system provides, causing "SQLite compiled for X but running
        # with Y" errors. Pointing to Homebrew's SQLite fixes this. Harmless on other
        # systems (path ignored if it doesn't exist).
        env = {
            **os.environ,
            "SSL_CERT_FILE": "/etc/ssl/cert.pem",
            "DYLD_LIBRARY_PATH": "/opt/homebrew/opt/sqlite/lib",
        }

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return result
        except subprocess.TimeoutExpired as e:
            raise SvnError(f"SVN command timed out after {timeout}s: {' '.join(args)}") from e

    def checkout(self, timeout: int = 600) -> bool:
        """
        Checkout the repository to the working directory.

        Args:
            timeout: Command timeout in seconds (default 600 for large repos).

        Returns:
            True if successful.

        Raises:
            SvnError: If checkout fails.
        """
        # Checkout to parent, with working_dir name as target
        parent = self.working_dir.parent
        name = self.working_dir.name

        result = self._run(["checkout", self.repo_url, name], cwd=parent, timeout=timeout)

        if result.returncode != 0:
            raise SvnError(f"Checkout failed: {result.stderr}")

        return True

    def list_remote(self, path: str = "") -> list[str]:
        """
        List contents of a remote path in the repository.

        Args:
            path: Path relative to repo root (empty for root).

        Returns:
            List of item names in the directory.
        """
        url = f"{self.repo_url}/{path}" if path else self.repo_url
        result = self._run(["list", url], timeout=60)
        if result.returncode != 0:
            return []
        return [line.rstrip("/") for line in result.stdout.splitlines() if line.strip()]

    def checkout_incremental(self, base_timeout: int = 120, timeout_per_100_files: int = 30) -> int:
        """
        Checkout the repository incrementally, directory by directory.

        This is slower overall but provides progress feedback and avoids
        timeout issues with large repositories. Timeout scales with directory size.

        Args:
            base_timeout: Base timeout per directory in seconds (default 120s).
            timeout_per_100_files: Additional seconds per 100 files (default 30s).

        Returns:
            Total number of files checked out.

        Raises:
            SvnError: If checkout fails.
        """
        parent = self.working_dir.parent
        name = self.working_dir.name

        # Step 1: Shallow checkout (just top-level files and directory stubs)
        print(f"    Initializing checkout...")
        result = self._run(
            ["checkout", "--depth", "immediates", self.repo_url, name],
            cwd=parent,
            timeout=120
        )
        if result.returncode != 0:
            raise SvnError(f"Shallow checkout failed: {result.stderr}")

        # Step 2: Get list of directories to fully populate
        dirs_to_update = []
        for item in self.working_dir.iterdir():
            if item.is_dir() and item.name != ".svn":
                dirs_to_update.append(item.name)

        total_files = 0

        # Step 3: Update each directory to full depth with progress
        if dirs_to_update:
            print(f"    Found {len(dirs_to_update)} directories to sync...")
            for i, dir_name in enumerate(dirs_to_update, 1):
                # Check remote size to scale timeout
                remote_items = self.list_remote(dir_name)
                item_count = len(remote_items)
                # Scale timeout: base + 30s per 100 files
                timeout = base_timeout + (item_count // 100) * timeout_per_100_files
                timeout = min(timeout, 1200)  # Cap at 20 minutes

                if item_count > 100:
                    print(f"    [{i}/{len(dirs_to_update)}] {dir_name}/ (~{item_count} items, {timeout}s timeout)...")

                result = self._run(
                    ["update", "--set-depth", "infinity", dir_name],
                    timeout=timeout
                )
                if result.returncode != 0:
                    print(f"    Warning: Failed to update {dir_name}: {result.stderr}")
                    continue

                # Count files in this directory
                dir_path = self.working_dir / dir_name
                file_count = len(list(dir_path.rglob("*"))) if dir_path.is_dir() else 0
                total_files += file_count
                print(f"    [{i}/{len(dirs_to_update)}] {dir_name}/ ({file_count} files)")

        # Also count top-level files
        top_level_files = len([f for f in self.working_dir.iterdir() if f.is_file()])
        total_files += top_level_files

        return total_files

    def update(self, timeout: int = 600) -> list[str]:
        """
        Update working copy from repository.

        Args:
            timeout: Command timeout in seconds (default 600 for large repos).

        Returns:
            List of updated file paths.

        Raises:
            SvnError: If update fails.
        """
        result = self._with_cleanup_retry("update", ["update", "--accept", "postpone"], timeout=timeout)

        if result.returncode != 0:
            raise SvnError(f"Update failed: {result.stderr}")

        # Parse updated files from output
        updated = []
        for line in result.stdout.splitlines():
            if line.startswith(("U ", "A ", "G ", "C ")):
                # U=Updated, A=Added, G=Merged, C=Conflict
                updated.append(line[2:].strip())

        return updated

    def status(self) -> SvnStatus:
        """
        Get status of working copy.

        Returns:
            SvnStatus object with categorized file lists.
        """
        result = self._with_cleanup_retry("status", ["status"])

        if result.returncode != 0:
            raise SvnError(f"Status failed: {result.stderr}")

        status = SvnStatus()

        for line in result.stdout.splitlines():
            if not line.strip():
                continue

            code = line[0]
            path = Path(line[8:].strip()) if len(line) > 8 else None

            if path is None:
                continue

            if code == "M":
                status.modified.append(path)
            elif code == "A":
                status.added.append(path)
            elif code == "D":
                status.deleted.append(path)
            elif code == "?":
                status.unversioned.append(path)
            elif code == "C":
                status.conflicted.append(path)
            elif code == "!":
                status.missing.append(path)

        return status

    def add(self, path: Path, parents: bool = False) -> bool:
        """
        Add a file to version control.

        Args:
            path: File path relative to working directory.
            parents: If True, also add parent directories.

        Returns:
            True if successful.
        """
        args = ["add"]
        if parents:
            args.append("--parents")
        args.append(str(path))
        result = self._run(args)
        return result.returncode == 0

    def add_all_markdown(self) -> list[Path]:
        """
        Add all unversioned markdown files recursively.

        Returns:
            List of added file paths.
        """
        added = []

        # Find all markdown files via filesystem
        for md_file in self.working_dir.glob("**/*.md"):
            # Get relative path
            rel_path = md_file.relative_to(self.working_dir)

            # Skip if already versioned (check via svn info)
            check = self._run(["info", str(rel_path)])
            if check.returncode == 0:
                continue  # Already versioned

            # Add with --parents to create directory structure
            if self.add(rel_path, parents=True):
                added.append(rel_path)

        return added

    def add_batch(self, paths: list[Path], batch_size: int = 50) -> tuple[list[Path], list[Path]]:
        """
        Add multiple files to version control in batches.

        More efficient than adding files one at a time for large uploads.
        Processes files in batches to avoid command line length limits.

        Args:
            paths: List of file paths relative to working directory.
            batch_size: Number of files to add per SVN command (default 50).

        Returns:
            Tuple of (successfully_added, failed) path lists.
        """
        added = []
        failed = []

        # Process in batches to avoid command line length limits
        for i in range(0, len(paths), batch_size):
            batch = paths[i:i + batch_size]

            # Build command with all paths in this batch
            args = ["add", "--parents"]
            args.extend(str(p) for p in batch)

            result = self._run(args)

            if result.returncode == 0:
                added.extend(batch)
            else:
                # Batch failed - fall back to one-by-one to identify failures
                for path in batch:
                    if self.add(path, parents=True):
                        added.append(path)
                    else:
                        failed.append(path)

        return added, failed

    def get_unversioned_files(self, pattern: str = "**/*.md") -> list[Path]:
        """
        Get list of unversioned files matching pattern.

        Uses svn status which is faster than checking each file individually.

        Args:
            pattern: Glob pattern to match files (default: all markdown files)

        Returns:
            List of unversioned file paths relative to working directory.
        """
        status = self.status()

        # Filter unversioned files by pattern
        import fnmatch
        unversioned = []
        for path in status.unversioned:
            if fnmatch.fnmatch(str(path), pattern) or path.suffix == ".md":
                unversioned.append(path)

        return unversioned

    def delete(self, path: Path) -> bool:
        """
        Mark a file for deletion from version control.

        Args:
            path: File path relative to working directory.

        Returns:
            True if successful.
        """
        result = self._run(["delete", "--force", str(path)])
        return result.returncode == 0

    def delete_missing(self) -> list[Path]:
        """
        Delete all missing files (removed from filesystem but not SVN).

        Returns:
            List of deleted file paths.
        """
        status = self.status()
        deleted = []

        for path in status.missing:
            if self.delete(path):
                deleted.append(path)

        return deleted

    def commit(self, message: str, timeout: int = 600) -> Optional[int]:
        """
        Commit changes to the repository.

        Args:
            message: Commit message.
            timeout: Command timeout in seconds (default 600 for large commits).

        Returns:
            Revision number if successful, None if nothing to commit.

        Raises:
            SvnError: If commit fails.
        """
        result = self._with_cleanup_retry("commit", ["commit", "-m", message], timeout=timeout)

        if result.returncode != 0:
            if "nothing to commit" in result.stderr.lower():
                return None
            raise SvnError(f"Commit failed: {result.stderr}")

        # Parse revision from output: "Committed revision 42."
        for line in result.stdout.splitlines():
            if "Committed revision" in line:
                try:
                    rev = int(line.split()[-1].rstrip("."))
                    return rev
                except (ValueError, IndexError):
                    pass

        return None

    def resolve_conflict(self, path: Path, accept: str = "mine-full") -> bool:
        """
        Resolve a conflict.

        Args:
            path: Conflicted file path.
            accept: Resolution strategy (mine-full, theirs-full, etc.)

        Returns:
            True if successful.
        """
        result = self._run(["resolve", "--accept", accept, str(path)])
        return result.returncode == 0

    def set_ignore(self, patterns: list[str]) -> bool:
        """
        Set svn:ignore property on working directory root.

        Args:
            patterns: List of patterns to ignore.

        Returns:
            True if successful.
        """
        ignore_value = "\n".join(patterns)
        result = self._run(["propset", "svn:ignore", ignore_value, "."])
        return result.returncode == 0

    def info(self) -> Optional[dict]:
        """
        Get info about working copy.

        Returns:
            Dict with 'url', 'revision', 'root' keys, or None if not a working copy.
        """
        result = self._run(["info", "--show-item", "url"])
        if result.returncode != 0:
            return None

        url = result.stdout.strip()

        result = self._run(["info", "--show-item", "revision"])
        revision = result.stdout.strip() if result.returncode == 0 else "unknown"

        return {
            "url": url,
            "revision": revision,
        }

    def is_working_copy(self) -> bool:
        """Check if the working directory is an SVN working copy."""
        return self.info() is not None

    def cleanup(self) -> bool:
        """
        Clean up the working copy, removing stale locks.

        Returns:
            True if successful.
        """
        result = self._run(["cleanup"])
        return result.returncode == 0

    def revert(self, path: Optional[Path] = None, recursive: bool = True) -> bool:
        """
        Revert changes in the working copy.

        Args:
            path: Specific path to revert, or None for entire working copy
            recursive: If True, revert recursively

        Returns:
            True if successful.
        """
        args = ["revert"]
        if recursive:
            args.append("-R")
        if path:
            args.append(str(path))
        else:
            args.append(".")
        result = self._run(args)
        return result.returncode == 0

    def _with_cleanup_retry(self, operation: str, args: list[str], cwd: Optional[Path] = None, timeout: int = 120) -> subprocess.CompletedProcess:
        """
        Run an SVN command with automatic cleanup retry on lock errors.

        Args:
            operation: Human-readable operation name for error messages.
            args: SVN command arguments.
            cwd: Working directory override.
            timeout: Command timeout in seconds (default 120).

        Returns:
            CompletedProcess result.

        Raises:
            SvnError: If operation fails even after cleanup retry.
        """
        result = self._run(args, cwd, timeout=timeout)

        if result.returncode != 0 and is_lock_error(result.stderr):
            # Working copy is locked - try cleanup and retry once
            self.cleanup()
            result = self._run(args, cwd, timeout=timeout)

        return result


def email_to_repo_name(email: str) -> str:
    """
    Convert email to SVN repo name.

    Args:
        email: User email (e.g., brandon@gmail.com)

    Returns:
        Sanitized repo name (e.g., brandon-gmail-com)
    """
    return email.replace("@", "-").replace(".", "-").lower()


def repo_url_for_email(email: str, base_url: str | None = None) -> str:
    """
    Get SVN repo URL for an email.

    Args:
        email: User email
        base_url: Base SVN URL (defaults to SVN_BASE_URL from config)

    Returns:
        Full repo URL
    """
    if base_url is None:
        base_url = SVN_BASE_URL
    repo_name = email_to_repo_name(email)
    return f"{base_url}/{repo_name}"
