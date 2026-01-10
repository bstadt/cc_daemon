"""Claude Connect CLI.

Main entry point for the claudeconnect command.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx

from .auth import login as do_login, ensure_valid_token, decode_jwt_payload, refresh_token
from .config import get_config, get_tokens, Config, Tokens, is_logged_in, get_email
from .svn_ops import SvnClient, SvnError, email_to_repo_name, repo_url_for_email
from .sync import SyncLoop, sync_once


SERVER_URL = "https://claudeconnect.io"


def get_svn_token(id_token: str) -> str | None:
    """
    Exchange Google JWT for a short Fernet token for SVN auth.

    Args:
        id_token: Google OAuth id_token

    Returns:
        Fernet token string, or None on failure.
    """
    try:
        response = httpx.post(
            f"{SERVER_URL}/api/svn-token",
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=30,
        )

        if response.status_code != 200:
            data = response.json()
            print(f"Failed to get SVN token: {data.get('error', 'Unknown error')}")
            return None

        data = response.json()
        return data.get("svn_token")

    except Exception as e:
        print(f"Error getting SVN token: {e}")
        return None


def get_valid_token() -> Tokens | None:
    """
    Get a valid (non-expired) token, refreshing if needed.

    Returns:
        Valid Tokens, or None if not logged in or refresh fails.
    """
    tokens = get_tokens()
    if not tokens:
        return None

    # Check if token is expired
    payload = decode_jwt_payload(tokens.id_token)
    exp = payload.get("exp", 0)

    if exp < int(time.time()):
        # Token expired - try to refresh
        if tokens.refresh_token:
            print("Token expired, refreshing...")
            new_tokens = refresh_token(tokens.refresh_token)
            if new_tokens:
                return new_tokens
            else:
                print("Failed to refresh token.")
                return None
        else:
            # No refresh token - need to re-login
            return None

    return tokens


def ensure_repo(token: str) -> dict:
    """
    Ensure user's repo exists on server.

    Args:
        token: OAuth id_token

    Returns:
        Dict with 'repo', 'url', 'email' keys.

    Raises:
        Exception on failure.
    """
    response = httpx.post(
        f"{SERVER_URL}/api/ensure-repo",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )

    if response.status_code != 200:
        data = response.json()
        raise Exception(data.get("error", "Failed to ensure repo"))

    return response.json()


def init_context_dir(context_dir: Path, repo_url: str, svn_token: str, email: str) -> bool:
    """
    Initialize a context directory with SVN checkout.

    Args:
        context_dir: The directory to initialize
        repo_url: SVN repository URL
        svn_token: Fernet token for SVN auth
        email: User email (SVN username)

    Returns:
        True if successful.
    """
    svn = SvnClient(context_dir, repo_url, svn_token, email)

    # Check if already a working copy
    if svn.is_working_copy():
        info = svn.info()
        if info and info.get("url") == repo_url:
            print(f"  Already initialized (revision {info['revision']})")
            return True
        else:
            print(f"  Error: Directory is an SVN working copy for different repo")
            print(f"  Expected: {repo_url}")
            print(f"  Got: {info.get('url')}")
            return False

    # Check if directory has markdown files to import
    md_files = list(context_dir.glob("**/*.md"))

    if md_files:
        print(f"  Found {len(md_files)} markdown files to sync")

    # Need to handle this carefully:
    # 1. If directory is empty, just checkout
    # 2. If directory has files, we need to:
    #    a. Checkout to a temp location
    #    b. Move .svn to the context dir
    #    c. Add existing files

    if not any(context_dir.iterdir()):
        # Empty directory - simple checkout
        try:
            svn.checkout()
            print("  Checked out empty repository")
            return True
        except SvnError as e:
            print(f"  Checkout failed: {e}")
            return False
    else:
        # Directory has files - need to overlay SVN
        print("  Initializing with existing files...")

        # Checkout to temp location (use /tmp to avoid permission issues)
        import tempfile
        temp_dir = Path(tempfile.mkdtemp(prefix="claudeconnect_init_"))
        temp_svn = SvnClient(temp_dir, repo_url, svn_token, email)

        try:
            temp_svn.checkout()
        except SvnError as e:
            print(f"  Checkout failed: {e}")
            return False

        # Move .svn folder to context dir
        svn_folder = temp_dir / ".svn"
        target_svn = context_dir / ".svn"

        if target_svn.exists():
            print("  Error: .svn folder already exists")
            return False

        svn_folder.rename(target_svn)

        # Clean up temp dir
        shutil.rmtree(temp_dir)

        # Now add all markdown files
        added = svn.add_all_markdown()
        print(f"  Added {len(added)} markdown files")

        # Set ignore patterns for non-markdown
        svn.set_ignore([
            "*.py",
            "*.json",
            "*.yaml",
            "*.yml",
            "*.txt",
            "*.log",
            "*.sqlite",
            "*.db",
            "__pycache__",
            ".git",
            ".DS_Store",
            "node_modules",
            "venv",
            ".venv",
        ])

        # Initial commit
        try:
            rev = svn.commit("Initial sync from claudeconnect")
            if rev:
                print(f"  Committed initial sync (revision {rev})")
            return True
        except SvnError as e:
            print(f"  Initial commit failed: {e}")
            return False


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """
    Claude Connect - Contextualized Claude instances communicating.

    Run without arguments to start Claude with sync enabled.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand - run main flow
        ctx.invoke(start)


@cli.command()
def login():
    """Login to Claude Connect with Google."""
    print("Logging in to Claude Connect...")

    result = do_login()

    if result.success:
        print(f"\n✓ Logged in as {result.tokens.email}")
        print(f"\nRun `claudeconnect` in your context directory to start.")
    else:
        print(f"\n✗ Login failed: {result.error}")
        sys.exit(1)


@cli.command()
def status():
    """Show current status."""
    tokens = get_tokens()
    config = get_config()

    if not tokens:
        print("Not logged in. Run `claudeconnect login` first.")
        return

    print(f"Logged in as: {tokens.email}")
    print(f"Repo: {email_to_repo_name(tokens.email)}")

    if config.context_dir:
        print(f"Context directory: {config.context_dir}")

        # Check if it's a valid working copy
        svn = SvnClient(
            Path(config.context_dir),
            repo_url_for_email(tokens.email),
            tokens.id_token,
        )
        info = svn.info()
        if info:
            print(f"SVN revision: {info['revision']}")
        else:
            print("SVN status: Not initialized")
    else:
        print("Context directory: Not set")


@cli.command()
def start():
    """Start Claude with sync enabled (default command)."""
    # Check login and token validity
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    cwd = Path.cwd()

    # Determine context directory
    if config.context_dir:
        context_dir = Path(config.context_dir)
        if cwd != context_dir and not cwd.is_relative_to(context_dir):
            print(f"Your context directory is: {context_dir}")
            print(f"Current directory: {cwd}")
            print(f"\nRun `claudeconnect` from your context directory.")
            print(f"Or use `claudeconnect init` here to switch.")
            sys.exit(1)
    else:
        # First time - use current directory
        context_dir = cwd

    # Ensure repo exists on server
    print(f"Connecting as {tokens.email}...")
    try:
        repo_info = ensure_repo(tokens.id_token)
        repo_url = repo_info["url"]

        if repo_info.get("created"):
            print(f"  Created new repo: {repo_info['repo']}")
        else:
            print(f"  Using repo: {repo_info['repo']}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Get SVN token (Fernet)
    print("Getting SVN credentials...")
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    # Initialize context directory if needed
    # Username is email, password is the Fernet token
    svn = SvnClient(context_dir, repo_url, svn_token, tokens.email)
    if not svn.is_working_copy():
        print(f"\nInitializing context directory: {context_dir}")
        if not init_context_dir(context_dir, repo_url, svn_token, tokens.email):
            sys.exit(1)

        # Save context dir to config
        config.context_dir = str(context_dir)
        config.save()

    # Initial sync
    print("\nSyncing...")
    sync_once(context_dir, repo_url, svn_token, tokens.email)

    # Start sync loop and Claude
    print("\nStarting Claude Code with sync enabled...")
    print("(Sync runs every 30 seconds in background)\n")

    # Run async main
    asyncio.run(run_with_sync(context_dir, repo_url, svn_token, tokens.email))


async def run_with_sync(context_dir: Path, repo_url: str, token: str, email: str):
    """Run Claude Code with background sync loop."""
    # Start sync loop
    sync_loop = SyncLoop(context_dir, repo_url, token, email, interval=30)
    await sync_loop.start()

    try:
        # Run Claude Code
        # Use subprocess to run claude, streaming output
        process = await asyncio.create_subprocess_exec(
            "claude",
            stdin=None,  # Inherit stdin
            stdout=None,  # Inherit stdout
            stderr=None,  # Inherit stderr
            cwd=context_dir,
        )

        await process.wait()

    except FileNotFoundError:
        print("Error: 'claude' command not found.")
        print("Make sure Claude Code is installed.")
    except KeyboardInterrupt:
        pass
    finally:
        # Stop sync loop
        await sync_loop.stop()
        print("\nSync stopped. Goodbye!")


@cli.command()
def init():
    """Initialize current directory as context directory."""
    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    config = get_config()
    cwd = Path.cwd()

    if config.context_dir and Path(config.context_dir) != cwd:
        print(f"Warning: Switching context directory")
        print(f"  From: {config.context_dir}")
        print(f"  To: {cwd}")
        if not click.confirm("Continue?"):
            return

    # Ensure repo exists
    print(f"Connecting as {tokens.email}...")
    try:
        repo_info = ensure_repo(tokens.id_token)
        repo_url = repo_info["url"]
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Get SVN token
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    # Initialize
    print(f"\nInitializing: {cwd}")
    if init_context_dir(cwd, repo_url, svn_token, tokens.email):
        config.context_dir = str(cwd)
        config.save()
        print("\n✓ Context directory initialized")
        print(f"  Run `claudeconnect` to start Claude with sync.")
    else:
        sys.exit(1)


@cli.command()
def sync():
    """Manually trigger a sync."""
    tokens = get_valid_token()
    config = get_config()

    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    if not config.context_dir:
        print("No context directory configured.")
        sys.exit(1)

    # Get SVN token
    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    context_dir = Path(config.context_dir)
    repo_url = repo_url_for_email(tokens.email)

    print("Syncing...")
    if sync_once(context_dir, repo_url, svn_token, tokens.email):
        print("✓ Sync complete")
    else:
        sys.exit(1)


@cli.command()
@click.argument("peer_email")
@click.option("--topic", "-t", help="Conversation topic")
def session(peer_email: str, topic: str | None):
    """Start a conversation session with a friend's Claude."""
    from .session import run_session

    print(f"Starting session with {peer_email}...")

    success, result = asyncio.run(run_session(peer_email, topic))

    if success:
        print(f"\n✓ Session complete!")
        print(f"  Transcript: {result}")
    else:
        print(f"\n✗ Session failed: {result}")
        sys.exit(1)


@cli.command()
@click.argument("peer_email")
def pull(peer_email: str):
    """Pull a friend's context without starting a session."""
    from .session import pull_peer_context

    tokens = get_valid_token()
    if not tokens:
        print("Not logged in or token expired. Run `claudeconnect login` first.")
        sys.exit(1)

    svn_token = get_svn_token(tokens.id_token)
    if not svn_token:
        print("Failed to get SVN token")
        sys.exit(1)

    print(f"Pulling {peer_email}'s context...")
    peer_dir = pull_peer_context(peer_email, svn_token, tokens.email)

    if peer_dir:
        print(f"\n✓ Context pulled to: {peer_dir}")
    else:
        print(f"\n✗ Failed to pull context")
        sys.exit(1)


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
