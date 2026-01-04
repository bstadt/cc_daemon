#!/usr/bin/env python3
"""
SVN authentication script for Apache mod_authnz_external.

Validates Fernet tokens and returns email for authz.
Exit code 0 = authenticated, 1 = denied.

Apache will set AUTHN_USERNAME and AUTHN_PASSWORD environment variables,
or pass them on stdin depending on config.
"""

import os
import sys
import time

from cryptography.fernet import Fernet, InvalidToken


def main():
    # Get Fernet key from file or environment
    fernet_key = os.environ.get("FERNET_KEY")
    if not fernet_key:
        key_file = "/opt/claudeconnect/fernet.key"
        try:
            with open(key_file) as f:
                fernet_key = f.read().strip()
        except Exception:
            pass

    if not fernet_key:
        print("FERNET_KEY not set", file=sys.stderr)
        sys.exit(1)

    fernet = Fernet(fernet_key.encode())

    # Get credentials - try environment first, then stdin
    username = os.environ.get("AUTHN_USERNAME", "")
    password = os.environ.get("AUTHN_PASSWORD", "")

    if not password:
        # Read from stdin (format: username\npassword\n)
        try:
            lines = sys.stdin.read().strip().split("\n")
            if len(lines) >= 2:
                username = lines[0]
                password = lines[1]
            elif len(lines) == 1:
                password = lines[0]
        except Exception:
            pass

    if not password:
        print("No password provided", file=sys.stderr)
        sys.exit(1)

    # Validate Fernet token
    try:
        payload = fernet.decrypt(password.encode()).decode()
        email, expiry_str = payload.rsplit("|", 1)
        expiry = int(expiry_str)

        # Check expiry
        if expiry < time.time():
            print(f"Token expired for {email}", file=sys.stderr)
            sys.exit(1)

        # Success - print email for logging
        print(email)
        sys.exit(0)

    except InvalidToken:
        print("Invalid token", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
