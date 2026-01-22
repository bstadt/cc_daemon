#!/usr/bin/env python3
"""
ClaudeConnect Integration Test

Tests the full flow:
1. Server cleanup (purge all repos)
2. Client cleanup (~/.claude-connect)
3. Two-account login/init
4. File creation and sync
5. Friend request flow
6. Session between accounts
7. Transcript verification
8. Context pull verification

Requires two Google accounts with manual OAuth at each login.
"""

import json
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

# Server config
SERVER = "v2.claudeconnect.io"
SSH_KEY = Path.home() / ".ssh" / "calco_key.pem"
CC_CONFIG_DIR = Path.home() / ".claude-connect"
PEERS_DIR = CC_CONFIG_DIR / "peers"


class Colors:
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    RED = "\033[0;31m"
    RESET = "\033[0m"


def log(msg: str):
    print(f"{Colors.GREEN}[TEST]{Colors.RESET} {msg}")


def warn(msg: str):
    print(f"{Colors.YELLOW}[WARN]{Colors.RESET} {msg}")


def error(msg: str):
    print(f"{Colors.RED}[ERROR]{Colors.RESET} {msg}")


def prompt(msg: str):
    print(f"{Colors.YELLOW}[ACTION REQUIRED]{Colors.RESET} {msg}")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        error(f"Command failed: {' '.join(cmd)}")
        error(f"stdout: {result.stdout}")
        error(f"stderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result


def ssh_server(cmd: str) -> str:
    """Run command on server via SSH."""
    result = run(["ssh", "-i", str(SSH_KEY), f"ubuntu@{SERVER}", cmd])
    return result.stdout.strip()


def claudeconnect(*args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run claudeconnect CLI command."""
    return run(["claudeconnect", *args], cwd=cwd)


def get_current_email() -> str:
    """Get email from current tokens."""
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    with open(tokens_file) as f:
        return json.load(f)["email"]


def email_to_repo_name(email: str) -> str:
    """Convert email to repo name format."""
    return email.replace("@", "-").replace(".", "-").lower()


def wait_for_user(msg: str):
    """Prompt user and wait for Enter."""
    prompt(msg)
    input("Press Enter to continue...")


def step_1_clean_server():
    """Remove all repos on server."""
    log("Step 1: Cleaning server repos...")
    ssh_server("sudo rm -rf /var/svn/repos/*")
    log("Server repos cleaned.")


def step_2_clean_client():
    """Remove local ~/.claude-connect."""
    log("Step 2: Removing local ~/.claude-connect...")
    if CC_CONFIG_DIR.exists():
        shutil.rmtree(CC_CONFIG_DIR)
    log("Local config removed.")


def step_3_create_temp_dirs() -> tuple[Path, Path]:
    """Create temp directories for both accounts."""
    log("Step 3: Creating temp directories...")
    temp1 = Path(tempfile.mkdtemp(prefix="cc_test_account1_"))
    temp2 = Path(tempfile.mkdtemp(prefix="cc_test_account2_"))
    log(f"Created {temp1} (Account 1)")
    log(f"Created {temp2} (Account 2)")
    return temp1, temp2


def step_4_login_account1(temp1: Path):
    """Login as Account 1."""
    log("Step 4: Logging in as Account 1...")
    os.chdir(temp1)
    wait_for_user("Please login with ACCOUNT 1 (first Google account)")
    claudeconnect("login", cwd=temp1)


def step_5_init_account1(temp1: Path) -> str:
    """Initialize Account 1."""
    log("Step 5: Initializing Account 1...")
    wait_for_user("Account 1 logged in. Ready to init.")
    claudeconnect("init", cwd=temp1)
    email = get_current_email()
    log(f"Account 1 initialized: {email}")
    return email


def step_6_switch_to_temp2(temp2: Path):
    """Switch to Account 2 directory."""
    log("Step 6: Switching to Account 2 directory...")
    os.chdir(temp2)
    # Clear tokens for new login
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    if tokens_file.exists():
        tokens_file.unlink()


def step_7_login_account2(temp2: Path):
    """Login as Account 2."""
    log("Step 7: Logging in as Account 2...")
    wait_for_user("Please login with ACCOUNT 2 (second Google account)")
    claudeconnect("login", cwd=temp2)


def step_8_init_account2(temp2: Path) -> str:
    """Initialize Account 2."""
    log("Step 8: Initializing Account 2...")
    wait_for_user("Account 2 logged in. Ready to init.")
    claudeconnect("init", cwd=temp2)
    email = get_current_email()
    log(f"Account 2 initialized: {email}")
    return email


def step_9_create_poetry(temp2: Path):
    """Create poetry.md in Account 2's context."""
    log("Step 9: Creating poetry.md...")
    poetry_file = temp2 / "poetry.md"
    poetry_file.write_text("# Poetry Collection\n\nturning and turning in the widening gyre\n")
    log("Created poetry.md")


def step_10_sync_account2(temp2: Path):
    """Sync Account 2."""
    log("Step 10: Syncing Account 2...")
    claudeconnect("sync", cwd=temp2)
    log("Account 2 synced.")


def step_11_send_friend_request(temp2: Path, account1_email: str):
    """Send friend request from Account 2 to Account 1."""
    log("Step 11: Sending friend request...")
    claudeconnect("friend", account1_email, "-m", "Let's connect!", cwd=temp2)
    log(f"Friend request sent to {account1_email}")


def step_12_switch_to_temp1(temp1: Path):
    """Switch back to Account 1 directory."""
    log("Step 12: Switching to Account 1 directory...")
    os.chdir(temp1)
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    if tokens_file.exists():
        tokens_file.unlink()


def step_13_relogin_account1(temp1: Path):
    """Re-login as Account 1."""
    log("Step 13: Re-logging in as Account 1...")
    wait_for_user("Please login with ACCOUNT 1 again")
    claudeconnect("login", cwd=temp1)


def step_14_reinit_account1(temp1: Path):
    """Re-init Account 1."""
    log("Step 14: Re-initializing Account 1...")
    wait_for_user("Ready to re-init Account 1.")
    claudeconnect("init", cwd=temp1)


def step_15_sync_check_friend_request(temp1: Path, account2_email: str):
    """Sync and verify friend request exists."""
    log("Step 15: Syncing and checking for friend request...")
    claudeconnect("sync", cwd=temp1)

    friend_requests_dir = temp1 / "claudeconnect" / "friend_requests"

    # Check for friend request file
    found = False
    if friend_requests_dir.exists():
        for f in friend_requests_dir.iterdir():
            if f.suffix == ".json":
                log(f"Found friend request: {f.name}")
                print(f.read_text())
                found = True
                break

    if not found:
        warn("Friend request file not found directly, checking directory:")
        if friend_requests_dir.exists():
            for f in friend_requests_dir.iterdir():
                print(f"  {f}")
        else:
            warn("friend_requests directory doesn't exist")


def step_16_accept_friend_request(temp1: Path, account2_email: str):
    """Accept the friend request."""
    log("Step 16: Accepting friend request...")
    claudeconnect("accept-friend", account2_email, cwd=temp1)
    log("Friend request accepted.")


def step_17_start_session(temp1: Path, account2_email: str):
    """Start a session about poetry."""
    log("Step 17: Starting session about poetry...")
    wait_for_user("Ready to start Claude session between accounts.")
    claudeconnect("session", account2_email, "-t", "poetry and the widening gyre", cwd=temp1)


def step_18_verify_transcript_account1(temp1: Path):
    """Verify transcript saved in Account 1."""
    log("Step 18: Verifying transcript in Account 1...")
    conv_dir = temp1 / "claudeconnect" / "conversations"

    if not conv_dir.exists():
        error(f"Conversations directory not found: {conv_dir}")
        return

    transcripts = list(conv_dir.rglob("*.md"))
    if transcripts:
        log(f"Found {len(transcripts)} transcript(s)")
        for t in transcripts:
            log(f"  {t}")
        # Show preview
        log("Preview of first transcript:")
        print(transcripts[0].read_text()[:1000])
    else:
        error("No transcripts found!")


def step_19_pull_and_verify_poetry(temp1: Path, account2_email: str):
    """Pull Account 2's context and verify poetry.md."""
    log("Step 19: Pulling Account 2's context...")
    claudeconnect("pull", account2_email, cwd=temp1)

    repo_name = email_to_repo_name(account2_email)
    peer_poetry = PEERS_DIR / repo_name / "poetry.md"

    if peer_poetry.exists():
        content = peer_poetry.read_text()
        log(f"Pulled poetry.md:")
        print(content)
        if "widening gyre" in content:
            log("Content verified - 'widening gyre' found!")
        else:
            error("Content verification failed")
    else:
        error(f"Could not find: {peer_poetry}")
        if PEERS_DIR.exists():
            warn("Peers directory contents:")
            for p in PEERS_DIR.iterdir():
                print(f"  {p}")


def step_20_switch_to_temp2(temp2: Path):
    """Switch back to Account 2."""
    log("Step 20: Switching to Account 2 directory...")
    os.chdir(temp2)
    tokens_file = CC_CONFIG_DIR / "tokens.json"
    if tokens_file.exists():
        tokens_file.unlink()


def step_21_relogin_account2(temp2: Path):
    """Re-login as Account 2."""
    log("Step 21: Re-logging in as Account 2...")
    wait_for_user("Please login with ACCOUNT 2 again")
    claudeconnect("login", cwd=temp2)


def step_22_reinit_account2(temp2: Path):
    """Re-init Account 2."""
    log("Step 22: Re-initializing Account 2...")
    wait_for_user("Ready to re-init Account 2.")
    claudeconnect("init", cwd=temp2)


def step_23_verify_transcript_account2(temp2: Path):
    """Sync and verify transcript arrived at Account 2."""
    log("Step 23: Syncing and verifying transcript in Account 2...")
    claudeconnect("sync", cwd=temp2)

    conv_dir = temp2 / "claudeconnect" / "conversations"

    if not conv_dir.exists():
        error(f"Conversations directory not found: {conv_dir}")
        return False

    transcripts = list(conv_dir.rglob("*.md"))
    if transcripts:
        log(f"Found {len(transcripts)} transcript(s) in Account 2")
        for t in transcripts:
            log(f"  {t}")
        log("SUCCESS: Transcript synced to Account 2!")
        return True
    else:
        error("No transcripts found in Account 2!")
        return False


def main():
    temp1 = None
    temp2 = None

    try:
        # Setup
        step_1_clean_server()
        step_2_clean_client()
        temp1, temp2 = step_3_create_temp_dirs()

        # Account 1 first login
        step_4_login_account1(temp1)
        account1_email = step_5_init_account1(temp1)

        # Account 2 setup
        step_6_switch_to_temp2(temp2)
        step_7_login_account2(temp2)
        account2_email = step_8_init_account2(temp2)
        step_9_create_poetry(temp2)
        step_10_sync_account2(temp2)
        step_11_send_friend_request(temp2, account1_email)

        # Account 1 receives and accepts
        step_12_switch_to_temp1(temp1)
        step_13_relogin_account1(temp1)
        step_14_reinit_account1(temp1)
        step_15_sync_check_friend_request(temp1, account2_email)
        step_16_accept_friend_request(temp1, account2_email)

        # Session
        step_17_start_session(temp1, account2_email)
        step_18_verify_transcript_account1(temp1)
        step_19_pull_and_verify_poetry(temp1, account2_email)

        # Account 2 verifies
        step_20_switch_to_temp2(temp2)
        step_21_relogin_account2(temp2)
        step_22_reinit_account2(temp2)
        step_23_verify_transcript_account2(temp2)

        # Summary
        print()
        log("==========================================")
        log("Integration test complete!")
        log("==========================================")
        log(f"Account 1: {account1_email}")
        log(f"Account 2: {account2_email}")
        log(f"Temp1: {temp1}")
        log(f"Temp2: {temp2}")

    except Exception as e:
        error(f"Test failed: {e}")
        raise
    finally:
        # Cleanup
        if temp1 and temp1.exists():
            log(f"Cleaning up {temp1}")
            shutil.rmtree(temp1)
        if temp2 and temp2.exists():
            log(f"Cleaning up {temp2}")
            shutil.rmtree(temp2)


if __name__ == "__main__":
    main()
