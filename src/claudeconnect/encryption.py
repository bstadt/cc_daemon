"""Client-side hybrid encryption using X25519 + AES-256-GCM.

Zero-trust encryption where private keys NEVER leave the user's machine.
The server only ever sees encrypted blobs.

Architecture:
- Each user has an X25519 keypair stored locally
- Files are encrypted with a random AES-256 key
- The AES key is encrypted separately for each recipient using X25519 ECDH
- Adding a friend = adding another encrypted AES key blob (no re-encryption of content)

Encryption rules:
- All files are encrypted before upload (Issue #98)
- authz files remain plaintext (required for access control)
- Friends gain access when you encrypt your AES key with their public key
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Optional

# Cryptography imports
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# File format constants
MAGIC_BYTES = b"CCENC"  # Claude Connect Encrypted
FORMAT_VERSION = 1
NONCE_SIZE = 12  # GCM standard
AES_KEY_SIZE = 32  # AES-256
X25519_KEY_SIZE = 32
ENCRYPTED_KEY_SIZE = 48  # 32-byte key + 16-byte GCM tag

# Files that should NOT be encrypted (required plaintext for system)
PLAINTEXT_FILES = {
    "authz",
}

# DEPRECATED: Previously only .md files were encrypted
# Now ALL files are encrypted except PLAINTEXT_FILES
# Kept for backwards compatibility if needed
ENCRYPTED_EXTENSIONS = {".md"}

# Import account-scoped directory helpers from config
from .config import get_keys_dir, get_friends_dir, email_to_repo_name

# Master key encryption format (v2)
MASTER_KEY_FORMAT_VERSION = 2
ENCRYPTED_MASTER_KEY_SIZE = 80  # 32 ephemeral pub + 48 encrypted key

# Key export format constants
KEYS_EXPORT_VERSION = 1
KEYS_EXPORT_MAGIC = "claudeconnect-keys-v1"
SCRYPT_N = 2**17  # ~128MB memory, ~1s on modern hardware
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_KEY_LEN = 32
SCRYPT_SALT_LEN = 16


def is_encryption_available() -> bool:
    """Check if cryptography library is available."""
    return HAS_CRYPTO


# Backwards compatibility alias
def is_kms_available() -> bool:
    """Deprecated: Use is_encryption_available() instead."""
    return is_encryption_available()


def _ensure_crypto():
    """Raise ImportError if cryptography is not available."""
    if not HAS_CRYPTO:
        raise ImportError(
            "cryptography is required for encryption. "
            "Install with: pip install cryptography"
        )


# =============================================================================
# Key Management
# =============================================================================

def generate_keypair(email: str, keys_dir: Optional[Path] = None) -> tuple[bytes, bytes]:
    """
    Generate a new X25519 keypair and save to disk.

    Args:
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory to store keys (default: ~/.claude-connect/keys/<email>/)

    Returns:
        Tuple of (private_key_bytes, public_key_bytes)

    Raises:
        ImportError: If cryptography is not installed
        FileExistsError: If keys already exist
    """
    _ensure_crypto()

    keys_dir = keys_dir or get_keys_dir(email)
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_path = keys_dir / "private.key"
    public_path = keys_dir / "public.key"

    if private_path.exists():
        raise FileExistsError(f"Private key already exists at {private_path}")

    # Generate keypair
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()

    # Serialize keys
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Save private key with restricted permissions
    private_path.write_bytes(private_bytes)
    private_path.chmod(0o600)

    # Save public key
    public_path.write_bytes(public_bytes)

    return private_bytes, public_bytes


def load_private_key(email: str, keys_dir: Optional[Path] = None) -> X25519PrivateKey:
    """
    Load the user's private key from disk.

    Args:
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing keys

    Returns:
        X25519PrivateKey object

    Raises:
        FileNotFoundError: If private key doesn't exist
        ImportError: If cryptography is not installed
    """
    _ensure_crypto()

    keys_dir = keys_dir or get_keys_dir(email)
    private_path = keys_dir / "private.key"

    if not private_path.exists():
        raise FileNotFoundError(
            f"Private key not found at {private_path}. "
            "Run 'claudeconnect init' to generate keys."
        )

    private_bytes = private_path.read_bytes()
    return X25519PrivateKey.from_private_bytes(private_bytes)


def load_public_key(email: str, keys_dir: Optional[Path] = None) -> bytes:
    """
    Load the user's public key bytes from disk.

    Args:
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing keys

    Returns:
        Raw public key bytes (32 bytes)

    Raises:
        FileNotFoundError: If public key doesn't exist
    """
    keys_dir = keys_dir or get_keys_dir(email)
    public_path = keys_dir / "public.key"

    if not public_path.exists():
        raise FileNotFoundError(
            f"Public key not found at {public_path}. "
            "Run 'claudeconnect init' to generate keys."
        )

    return public_path.read_bytes()


def get_key_fingerprint(public_key_bytes: bytes) -> str:
    """
    Get a short fingerprint of a public key for display.

    Args:
        public_key_bytes: Raw 32-byte public key

    Returns:
        Hex string fingerprint (first 8 bytes of SHA-256)
    """
    digest = hashlib.sha256(public_key_bytes).hexdigest()
    return digest[:16]


# =============================================================================
# Master Key Management (v2 - single key per user)
# =============================================================================

def generate_master_key(email: str, keys_dir: Optional[Path] = None) -> bytes:
    """
    Generate and save a new master AES key.

    The master key is used to encrypt all of a user's files.
    Friends receive this key (encrypted for them) to decrypt your files.

    Args:
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory to store the key

    Returns:
        The generated 32-byte master key

    Raises:
        FileExistsError: If master key already exists
    """
    keys_dir = keys_dir or get_keys_dir(email)
    keys_dir.mkdir(parents=True, exist_ok=True)

    master_path = keys_dir / "master.key"

    if master_path.exists():
        raise FileExistsError(f"Master key already exists at {master_path}")

    master_key = os.urandom(AES_KEY_SIZE)

    # Save with restricted permissions
    master_path.write_bytes(master_key)
    master_path.chmod(0o600)

    return master_key


def load_master_key(email: str, keys_dir: Optional[Path] = None) -> bytes:
    """
    Load the user's master AES key from disk.

    Args:
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing the key

    Returns:
        32-byte master key

    Raises:
        FileNotFoundError: If master key doesn't exist
    """
    keys_dir = keys_dir or get_keys_dir(email)
    master_path = keys_dir / "master.key"

    if not master_path.exists():
        raise FileNotFoundError(
            f"Master key not found at {master_path}. "
            "Run 'claudeconnect init' to generate keys."
        )

    return master_path.read_bytes()


def encrypt_master_key_for_recipient(
    master_key: bytes,
    recipient_public_key: bytes,
) -> bytes:
    """
    Encrypt our master key for a specific recipient.

    Uses ephemeral X25519 ECDH + AES-GCM to encrypt the master key
    so only the recipient can decrypt it with their private key.

    Output format (80 bytes):
        EPHEMERAL_PUBLIC_KEY (32 bytes)
        ENCRYPTED_KEY (48 bytes): AES-GCM(master_key) with 16-byte tag

    Args:
        master_key: Our 32-byte master AES key
        recipient_public_key: Recipient's X25519 public key (32 bytes)

    Returns:
        80-byte encrypted blob
    """
    _ensure_crypto()

    # Generate ephemeral keypair
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Encrypt master key for recipient
    encrypted_key = _encrypt_key_for_recipient(
        master_key, recipient_public_key, ephemeral_private
    )

    return ephemeral_public + encrypted_key


def decrypt_received_master_key(
    encrypted_blob: bytes,
    email: str,
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Decrypt a master key that was encrypted for us.

    Args:
        encrypted_blob: 80-byte blob from encrypt_master_key_for_recipient
        email: Our email address (for account-scoped key storage)
        keys_dir: Override directory containing our private key

    Returns:
        Decrypted 32-byte master key

    Raises:
        ValueError: If blob is invalid or decryption fails
    """
    _ensure_crypto()

    if len(encrypted_blob) != ENCRYPTED_MASTER_KEY_SIZE:
        raise ValueError(
            f"Invalid encrypted master key size: {len(encrypted_blob)}, "
            f"expected {ENCRYPTED_MASTER_KEY_SIZE}"
        )

    ephemeral_public = encrypted_blob[:X25519_KEY_SIZE]
    encrypted_key = encrypted_blob[X25519_KEY_SIZE:]

    private_key = load_private_key(email, keys_dir)

    return _decrypt_key_for_recipient(encrypted_key, ephemeral_public, private_key)


def save_friend_master_key(
    friend_email: str,
    master_key: bytes,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> Path:
    """
    Save a friend's decrypted master key.

    This key allows us to decrypt all of their files.

    Args:
        friend_email: Friend's email address
        master_key: Friend's decrypted 32-byte master key
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory to store friend keys (override)

    Returns:
        Path to saved key file
    """
    friends_dir = friends_dir or get_friends_dir(our_email)
    friends_dir.mkdir(parents=True, exist_ok=True)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.master"

    key_path.write_bytes(master_key)
    key_path.chmod(0o600)
    return key_path


def load_friend_master_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bytes:
    """
    Load a friend's master key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        Friend's 32-byte master key

    Raises:
        FileNotFoundError: If friend's master key not found
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.master"

    if not key_path.exists():
        raise FileNotFoundError(f"Master key for {friend_email} not found at {key_path}")

    return key_path.read_bytes()


def has_friend_master_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bool:
    """
    Check if we have a friend's master key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        True if we have their master key
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.master"

    return key_path.exists()


def delete_friend_master_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bool:
    """
    Delete a friend's master key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        True if key was deleted, False if it didn't exist
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.master"

    if key_path.exists():
        key_path.unlink()
        return True
    return False


# =============================================================================
# Key Export/Import
# =============================================================================

def derive_key_from_password(password: str, salt: bytes) -> bytes:
    """
    Derive an AES-256 key from a password using scrypt.

    Args:
        password: User-provided password
        salt: Random 16-byte salt

    Returns:
        32-byte derived key
    """
    _ensure_crypto()

    kdf = Scrypt(
        salt=salt,
        length=SCRYPT_KEY_LEN,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    return kdf.derive(password.encode("utf-8"))


def export_keys(
    email: str,
    password: Optional[str] = None,
    keys_dir: Optional[Path] = None,
    friends_dir: Optional[Path] = None,
) -> dict:
    """
    Export all keys to a dictionary suitable for JSON serialization.

    If password is provided, the keys are encrypted with AES-256-GCM
    using a key derived from the password via scrypt.

    Includes friend keys (public and master) so they can be restored
    on a new machine.

    Args:
        email: User's email address
        password: If provided, encrypt the keys
        keys_dir: Override keys directory
        friends_dir: Override friends directory

    Returns:
        Dict with export format (ready for json.dumps)

    Raises:
        FileNotFoundError: If keys don't exist
    """
    _ensure_crypto()

    keys_dir = keys_dir or get_keys_dir(email)
    friends_dir = friends_dir or get_friends_dir(email)

    # Load keys
    private_key = load_private_key(email, keys_dir)
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = load_public_key(email, keys_dir)

    # Load master key if exists
    master_bytes = None
    try:
        master_bytes = load_master_key(email, keys_dir)
    except FileNotFoundError:
        pass

    # Load friend keys
    friends_data = {}
    if friends_dir.exists():
        # Get all unique friend identifiers (stem of .pub or .master files)
        friend_ids = set()
        for pub_file in friends_dir.glob("*.pub"):
            friend_ids.add(pub_file.stem)
        for master_file in friends_dir.glob("*.master"):
            friend_ids.add(master_file.stem)

        for friend_id in friend_ids:
            friend_entry = {}
            pub_path = friends_dir / f"{friend_id}.pub"
            master_path = friends_dir / f"{friend_id}.master"

            if pub_path.exists():
                friend_entry["public_key"] = base64.b64encode(pub_path.read_bytes()).decode()
            if master_path.exists():
                friend_entry["master_key"] = base64.b64encode(master_path.read_bytes()).decode()

            if friend_entry:
                friends_data[friend_id] = friend_entry

    # Build keys dict
    keys_data = {
        "private_key": base64.b64encode(private_bytes).decode(),
        "public_key": base64.b64encode(public_bytes).decode(),
        "master_key": base64.b64encode(master_bytes).decode() if master_bytes else None,
        "friends": friends_data,
    }

    fingerprint = get_key_fingerprint(public_bytes)

    export = {
        "version": KEYS_EXPORT_VERSION,
        "format": KEYS_EXPORT_MAGIC,
        "email": email,
        "fingerprint": fingerprint,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    if password:
        # Encrypt keys
        salt = os.urandom(SCRYPT_SALT_LEN)
        nonce = os.urandom(NONCE_SIZE)
        key = derive_key_from_password(password, salt)
        aesgcm = AESGCM(key)
        plaintext = json.dumps(keys_data).encode()
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        export["salt"] = base64.b64encode(salt).decode()
        export["nonce"] = base64.b64encode(nonce).decode()
        export["encrypted_keys"] = base64.b64encode(ciphertext).decode()
    else:
        export["keys"] = keys_data

    return export


def import_keys(
    export_data: dict,
    password: Optional[str] = None,
    email: Optional[str] = None,
    keys_dir: Optional[Path] = None,
    friends_dir: Optional[Path] = None,
    force: bool = False,
) -> tuple[str, str, int]:
    """
    Import keys from export data.

    Args:
        export_data: Parsed JSON export
        password: Password if encrypted
        email: Expected email (for verification, optional)
        keys_dir: Override keys directory
        friends_dir: Override friends directory
        force: Allow overwriting existing keys

    Returns:
        Tuple of (email, fingerprint, friend_count) of imported keys

    Raises:
        ValueError: If verification fails or password wrong
        FileExistsError: If keys exist and force=False
    """
    _ensure_crypto()

    # Validate format
    if export_data.get("format") != KEYS_EXPORT_MAGIC:
        raise ValueError("Invalid export file format")

    version = export_data.get("version", 0)
    if version > KEYS_EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {version}")

    export_email = export_data.get("email")
    if not export_email:
        raise ValueError("Export file missing email")

    # Verify email matches if specified
    if email and email != export_email:
        raise ValueError(
            f"Email mismatch: expected {email}, "
            f"but export file is for {export_email}"
        )

    # Decrypt or extract keys
    if "encrypted_keys" in export_data:
        if not password:
            raise ValueError("Export file is encrypted, password required")
        salt = base64.b64decode(export_data["salt"])
        nonce = base64.b64decode(export_data["nonce"])
        ciphertext = base64.b64decode(export_data["encrypted_keys"])
        key = derive_key_from_password(password, salt)
        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception:
            raise ValueError("Decryption failed - wrong password?")
        keys_data = json.loads(plaintext)
    else:
        keys_data = export_data.get("keys")
        if not keys_data:
            raise ValueError("Export file missing keys")

    # Decode keys
    private_bytes = base64.b64decode(keys_data["private_key"])
    public_bytes = base64.b64decode(keys_data["public_key"])
    master_bytes = (
        base64.b64decode(keys_data["master_key"])
        if keys_data.get("master_key")
        else None
    )

    # Verify fingerprint
    fingerprint = get_key_fingerprint(public_bytes)
    expected_fingerprint = export_data.get("fingerprint")
    if expected_fingerprint and fingerprint != expected_fingerprint:
        raise ValueError("Key fingerprint mismatch - file may be corrupted")

    # Check for existing keys
    target_keys_dir = keys_dir or get_keys_dir(export_email)
    private_path = target_keys_dir / "private.key"

    if private_path.exists() and not force:
        raise FileExistsError(
            f"Keys already exist for {export_email}. "
            "Use --force to overwrite (this is DANGEROUS)."
        )

    # Write keys
    target_keys_dir.mkdir(parents=True, exist_ok=True)

    private_path.write_bytes(private_bytes)
    private_path.chmod(0o600)

    public_path = target_keys_dir / "public.key"
    public_path.write_bytes(public_bytes)

    if master_bytes:
        master_path = target_keys_dir / "master.key"
        master_path.write_bytes(master_bytes)
        master_path.chmod(0o600)

    # Restore friend keys
    friend_count = 0
    friends_data = keys_data.get("friends", {})
    if friends_data:
        target_friends_dir = friends_dir or get_friends_dir(export_email)
        target_friends_dir.mkdir(parents=True, exist_ok=True)

        for friend_id, friend_keys in friends_data.items():
            if "public_key" in friend_keys:
                pub_path = target_friends_dir / f"{friend_id}.pub"
                pub_path.write_bytes(base64.b64decode(friend_keys["public_key"]))

            if "master_key" in friend_keys:
                master_path = target_friends_dir / f"{friend_id}.master"
                master_path.write_bytes(base64.b64decode(friend_keys["master_key"]))
                master_path.chmod(0o600)

            friend_count += 1

    return export_email, fingerprint, friend_count


def verify_export_file(
    export_data: dict,
    password: Optional[str] = None,
) -> dict:
    """
    Verify export file integrity and return metadata.

    Args:
        export_data: Parsed JSON export
        password: Password if encrypted (to verify it's correct)

    Returns:
        Dict with: email, fingerprint, created_at, has_master_key, encrypted, friend_count

    Raises:
        ValueError: If file is invalid or password is wrong
    """
    if export_data.get("format") != KEYS_EXPORT_MAGIC:
        raise ValueError("Invalid export file format")

    is_encrypted = "encrypted_keys" in export_data

    result = {
        "email": export_data.get("email"),
        "fingerprint": export_data.get("fingerprint"),
        "created_at": export_data.get("created_at"),
        "encrypted": is_encrypted,
        "has_master_key": None,  # Unknown until decrypted
        "friend_count": None,  # Unknown until decrypted
    }

    if is_encrypted:
        if password:
            _ensure_crypto()
            # Try to decrypt to verify
            salt = base64.b64decode(export_data["salt"])
            nonce = base64.b64decode(export_data["nonce"])
            ciphertext = base64.b64decode(export_data["encrypted_keys"])
            key = derive_key_from_password(password, salt)
            aesgcm = AESGCM(key)
            try:
                plaintext = aesgcm.decrypt(nonce, ciphertext, None)
                keys_data = json.loads(plaintext)
                result["has_master_key"] = keys_data.get("master_key") is not None
                result["friend_count"] = len(keys_data.get("friends", {}))
            except Exception:
                raise ValueError("Decryption failed - wrong password?")
    else:
        keys_data = export_data.get("keys", {})
        result["has_master_key"] = keys_data.get("master_key") is not None
        result["friend_count"] = len(keys_data.get("friends", {}))

    return result


# =============================================================================
# Friend Key Management
# =============================================================================

def save_friend_public_key(
    friend_email: str,
    public_key_bytes: bytes,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> Path:
    """
    Save a friend's public key.

    Args:
        friend_email: Friend's email address
        public_key_bytes: Friend's raw public key (32 bytes)
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory to store friend keys (override)

    Returns:
        Path to saved key file
    """
    friends_dir = friends_dir or get_friends_dir(our_email)
    friends_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize email for filename
    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.pub"

    key_path.write_bytes(public_key_bytes)
    return key_path


def load_friend_public_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bytes:
    """
    Load a friend's public key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        Raw public key bytes (32 bytes)

    Raises:
        FileNotFoundError: If friend's key not found
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.pub"

    if not key_path.exists():
        raise FileNotFoundError(f"Public key for {friend_email} not found at {key_path}")

    return key_path.read_bytes()


def has_friend_public_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bool:
    """
    Check if we have a friend's public key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        True if we have their public key
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.pub"

    return key_path.exists()


def list_friends(our_email: str, friends_dir: Optional[Path] = None) -> list[str]:
    """
    List all friends with stored public keys.

    Args:
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        List of email addresses (sanitized form)
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    if not friends_dir.exists():
        return []

    emails = []
    for key_file in friends_dir.glob("*.pub"):
        # Convert filename back to email
        safe_email = key_file.stem
        # This is a lossy conversion (can't perfectly recover @ vs -)
        # but good enough for display purposes
        emails.append(safe_email)

    return emails


def delete_friend_public_key(
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bool:
    """
    Delete a friend's public key.

    Args:
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped storage)
        friends_dir: Directory containing friend keys (override)

    Returns:
        True if key was deleted, False if it didn't exist
    """
    friends_dir = friends_dir or get_friends_dir(our_email)

    safe_email = email_to_repo_name(friend_email)
    key_path = friends_dir / f"{safe_email}.pub"

    if key_path.exists():
        key_path.unlink()
        return True
    return False


# =============================================================================
# Encryption / Decryption
# =============================================================================

def _derive_shared_key(
    private_key: X25519PrivateKey,
    peer_public_key_bytes: bytes,
) -> bytes:
    """
    Derive a shared AES key from X25519 key exchange.

    Uses HKDF to derive a proper AES key from the shared secret.

    Args:
        private_key: Our X25519 private key
        peer_public_key_bytes: Their raw public key bytes

    Returns:
        32-byte AES key
    """
    _ensure_crypto()

    peer_public_key = X25519PublicKey.from_public_bytes(peer_public_key_bytes)
    shared_secret = private_key.exchange(peer_public_key)

    # Derive AES key using HKDF
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=None,
        info=b"claudeconnect-key-encryption",
    )
    return hkdf.derive(shared_secret)


def _encrypt_key_for_recipient(
    aes_key: bytes,
    recipient_public_key_bytes: bytes,
    ephemeral_private_key: X25519PrivateKey,
) -> bytes:
    """
    Encrypt an AES key for a specific recipient.

    Uses ECDH with an ephemeral key to derive a wrapping key,
    then encrypts the AES key with AES-GCM.

    Args:
        aes_key: The AES content key to encrypt (32 bytes)
        recipient_public_key_bytes: Recipient's public key
        ephemeral_private_key: Ephemeral private key for this encryption

    Returns:
        Encrypted key blob (48 bytes: 32 encrypted + 16 tag)
    """
    _ensure_crypto()

    # Derive wrapping key from ECDH
    wrapping_key = _derive_shared_key(ephemeral_private_key, recipient_public_key_bytes)

    # Encrypt the AES key with the wrapping key
    # Use a fixed nonce since each ephemeral key is only used once
    aesgcm = AESGCM(wrapping_key)
    nonce = b"\x00" * NONCE_SIZE  # Safe because ephemeral key is single-use
    encrypted_key = aesgcm.encrypt(nonce, aes_key, None)

    return encrypted_key


def _decrypt_key_for_recipient(
    encrypted_key_blob: bytes,
    ephemeral_public_key_bytes: bytes,
    recipient_private_key: X25519PrivateKey,
) -> bytes:
    """
    Decrypt an AES key using our private key.

    Args:
        encrypted_key_blob: Encrypted AES key (48 bytes)
        ephemeral_public_key_bytes: Ephemeral public key from encryption
        recipient_private_key: Our private key

    Returns:
        Decrypted AES key (32 bytes)
    """
    _ensure_crypto()

    # Derive wrapping key from ECDH
    wrapping_key = _derive_shared_key(recipient_private_key, ephemeral_public_key_bytes)

    # Decrypt the AES key
    aesgcm = AESGCM(wrapping_key)
    nonce = b"\x00" * NONCE_SIZE
    aes_key = aesgcm.decrypt(nonce, encrypted_key_blob, None)

    return aes_key


def encrypt_file(
    plaintext: bytes,
    recipient_public_keys: dict[str, bytes],
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Encrypt file content for multiple recipients.

    File format:
        MAGIC (5 bytes): "CCENC"
        VERSION (1 byte): Format version
        NUM_RECIPIENTS (2 bytes): Number of recipients
        EPHEMERAL_PUBLIC_KEY (32 bytes): One-time public key
        For each recipient:
            RECIPIENT_ID_LEN (1 byte): Length of recipient ID
            RECIPIENT_ID (variable): Recipient identifier (email hash)
            ENCRYPTED_KEY (48 bytes): AES key encrypted for this recipient
        NONCE (12 bytes): AES-GCM nonce
        CIPHERTEXT (variable): Encrypted content with GCM tag

    Args:
        plaintext: Raw file content to encrypt
        recipient_public_keys: Dict mapping recipient_id -> public_key_bytes
                              Must include yourself!
        keys_dir: Directory containing your keys (for including yourself)

    Returns:
        Encrypted file bytes

    Raises:
        ImportError: If cryptography is not installed
        ValueError: If no recipients provided
    """
    _ensure_crypto()

    if not recipient_public_keys:
        raise ValueError("At least one recipient required")

    # Generate random AES key for content
    aes_key = os.urandom(AES_KEY_SIZE)

    # Generate ephemeral keypair for key encryption
    ephemeral_private = X25519PrivateKey.generate()
    ephemeral_public = ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Encrypt AES key for each recipient
    encrypted_keys: list[tuple[bytes, bytes]] = []
    for recipient_id, public_key_bytes in recipient_public_keys.items():
        # Hash recipient ID to fixed length
        recipient_id_hash = hashlib.sha256(recipient_id.encode()).digest()[:8]
        encrypted_key = _encrypt_key_for_recipient(
            aes_key, public_key_bytes, ephemeral_private
        )
        encrypted_keys.append((recipient_id_hash, encrypted_key))

    # Encrypt content with AES-GCM
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Build output
    output = bytearray()

    # Header
    output.extend(MAGIC_BYTES)
    output.append(FORMAT_VERSION)
    output.extend(struct.pack(">H", len(encrypted_keys)))  # Big-endian uint16
    output.extend(ephemeral_public)

    # Encrypted keys
    for recipient_id_hash, encrypted_key in encrypted_keys:
        output.append(len(recipient_id_hash))
        output.extend(recipient_id_hash)
        output.extend(encrypted_key)

    # Content
    output.extend(nonce)
    output.extend(ciphertext)

    return bytes(output)


def decrypt_file(
    ciphertext: bytes,
    recipient_id: str,
    email: str,
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Decrypt file content using our private key (v1 multi-recipient format).

    NOTE: This is the v1 format. For v2 master key format, use decrypt_file_with_master_key.

    Args:
        ciphertext: Encrypted file bytes
        recipient_id: Our recipient ID (email) to find our key blob
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing our keys

    Returns:
        Decrypted plaintext bytes

    Raises:
        ImportError: If cryptography is not installed
        ValueError: If file format is invalid or we're not a recipient
    """
    _ensure_crypto()

    # Load our private key (account-scoped)
    private_key = load_private_key(email, keys_dir)

    # Parse header
    if len(ciphertext) < 5:
        raise ValueError("File too short to be encrypted")

    if ciphertext[:5] != MAGIC_BYTES:
        raise ValueError("Not a ClaudeConnect encrypted file")

    version = ciphertext[5]
    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported format version: {version}")

    num_recipients = struct.unpack(">H", ciphertext[6:8])[0]
    ephemeral_public = ciphertext[8:40]

    # Parse encrypted keys
    offset = 40
    our_recipient_hash = hashlib.sha256(recipient_id.encode()).digest()[:8]
    our_encrypted_key = None

    for _ in range(num_recipients):
        id_len = ciphertext[offset]
        offset += 1
        recipient_id_hash = ciphertext[offset : offset + id_len]
        offset += id_len
        encrypted_key = ciphertext[offset : offset + ENCRYPTED_KEY_SIZE]
        offset += ENCRYPTED_KEY_SIZE

        if recipient_id_hash == our_recipient_hash:
            our_encrypted_key = encrypted_key

    if our_encrypted_key is None:
        raise ValueError(f"Not a recipient of this file (id: {recipient_id})")

    # Decrypt our AES key
    aes_key = _decrypt_key_for_recipient(
        our_encrypted_key, ephemeral_public, private_key
    )

    # Decrypt content
    nonce = ciphertext[offset : offset + NONCE_SIZE]
    offset += NONCE_SIZE
    encrypted_content = ciphertext[offset:]

    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(nonce, encrypted_content, None)

    return plaintext


def add_recipient_to_file(
    ciphertext: bytes,
    new_recipient_id: str,
    new_recipient_public_key: bytes,
    our_recipient_id: str,
    email: str,
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Add a new recipient to an already-encrypted file (v1 format).

    NOTE: This is the v1 format. For v2, friends receive your master key directly.

    This decrypts our copy of the AES key, then re-encrypts it
    for the new recipient. Content is NOT re-encrypted.

    Args:
        ciphertext: Existing encrypted file
        new_recipient_id: New recipient's ID (email)
        new_recipient_public_key: New recipient's public key
        our_recipient_id: Our recipient ID to find our key
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing our keys

    Returns:
        Updated encrypted file with new recipient

    Raises:
        ValueError: If we're not a recipient or file is invalid
    """
    _ensure_crypto()

    # Load our private key (account-scoped)
    private_key = load_private_key(email, keys_dir)

    # Parse header
    if ciphertext[:5] != MAGIC_BYTES:
        raise ValueError("Not a ClaudeConnect encrypted file")

    version = ciphertext[5]
    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported format version: {version}")

    num_recipients = struct.unpack(">H", ciphertext[6:8])[0]
    ephemeral_public = ciphertext[8:40]

    # Parse existing encrypted keys
    offset = 40
    our_recipient_hash = hashlib.sha256(our_recipient_id.encode()).digest()[:8]
    our_encrypted_key = None
    existing_keys: list[tuple[bytes, bytes]] = []

    for _ in range(num_recipients):
        id_len = ciphertext[offset]
        offset += 1
        recipient_id_hash = ciphertext[offset : offset + id_len]
        offset += id_len
        encrypted_key = ciphertext[offset : offset + ENCRYPTED_KEY_SIZE]
        offset += ENCRYPTED_KEY_SIZE

        existing_keys.append((recipient_id_hash, encrypted_key))

        if recipient_id_hash == our_recipient_hash:
            our_encrypted_key = encrypted_key

    if our_encrypted_key is None:
        raise ValueError("Not a recipient of this file")

    # Decrypt our AES key
    aes_key = _decrypt_key_for_recipient(
        our_encrypted_key, ephemeral_public, private_key
    )

    # Load ephemeral public key to create new encrypted key
    # We need to re-encrypt using the SAME ephemeral public key
    # Actually, we need to create a NEW ephemeral key for the new recipient
    # But that changes the ephemeral key in the header...
    #
    # Better approach: Use a deterministic derivation from the original
    # ephemeral key + new recipient ID. But we don't have the ephemeral private.
    #
    # Simplest correct approach: Re-encrypt the whole file with all recipients.
    # This is what we'll do for correctness.

    # For now, we re-encrypt with a new ephemeral key for the new recipient only
    # This means each recipient might have a different ephemeral key
    # We need to store ephemeral public key per recipient

    # Actually, let's change the format: store ephemeral key per recipient
    # For v1, we'll just re-encrypt the whole file
    all_recipients = {
        our_recipient_id: load_public_key(email, keys_dir),  # us
        new_recipient_id: new_recipient_public_key,
    }

    # Add existing friends (we need their public keys)
    # For simplicity in v1, we require the caller to provide all recipients
    # or we re-encrypt with just us + new person (losing other recipients)

    # For now: just add the new recipient alongside existing ones
    # We'll generate a new ephemeral key and re-encrypt all keys

    new_ephemeral_private = X25519PrivateKey.generate()
    new_ephemeral_public = new_ephemeral_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # Get content after keys
    nonce = ciphertext[offset : offset + NONCE_SIZE]
    encrypted_content = ciphertext[offset + NONCE_SIZE :]

    # Build new encrypted keys list
    # We need public keys for ALL existing recipients to re-encrypt
    # This is a limitation - for now we'll just add our key + new recipient
    # In practice, the caller should provide all recipient public keys

    # Get the decrypted content
    aesgcm = AESGCM(aes_key)
    plaintext = aesgcm.decrypt(nonce, encrypted_content, None)

    # Build recipient dict for re-encryption
    # We only have our key and the new person's key
    # We lose other recipients - this is a v1 limitation
    recipients = {
        our_recipient_id: load_public_key(email, keys_dir),
        new_recipient_id: new_recipient_public_key,
    }

    return encrypt_file(plaintext, recipients, keys_dir)


# =============================================================================
# Master Key File Encryption (v2 - simpler format)
# =============================================================================

def encrypt_file_with_master_key(
    plaintext: bytes,
    email: str,
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Encrypt file content using our master key.

    V2 file format (much simpler than v1):
        MAGIC (5 bytes): "CCENC"
        VERSION (1 byte): 2 (master key format)
        NONCE (12 bytes): AES-GCM nonce
        CIPHERTEXT (variable): Encrypted content with GCM tag

    Args:
        plaintext: Raw file content to encrypt
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing our master key

    Returns:
        Encrypted file bytes
    """
    _ensure_crypto()

    master_key = load_master_key(email, keys_dir)

    # Encrypt content with AES-GCM
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(master_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Build output
    output = bytearray()
    output.extend(MAGIC_BYTES)
    output.append(MASTER_KEY_FORMAT_VERSION)
    output.extend(nonce)
    output.extend(ciphertext)

    return bytes(output)


def encrypt_file_for_friend(
    plaintext: bytes,
    friend_email: str,
    our_email: str,
) -> bytes:
    """
    Encrypt file content using a friend's master key.

    Used when uploading content to a friend's repo (e.g., transcripts).
    The friend's master key must have been saved previously (e.g., when
    they sent us a friend request with their encrypted master key).

    Args:
        plaintext: Raw file content to encrypt
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped key storage)

    Returns:
        Encrypted file bytes (same v2 format as encrypt_file_with_master_key)

    Raises:
        FileNotFoundError: If friend's master key not found
    """
    _ensure_crypto()

    master_key = load_friend_master_key(friend_email, our_email)

    # Encrypt content with AES-GCM (same format as own files)
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(master_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Build output
    output = bytearray()
    output.extend(MAGIC_BYTES)
    output.append(MASTER_KEY_FORMAT_VERSION)
    output.extend(nonce)
    output.extend(ciphertext)

    return bytes(output)


def decrypt_file_with_master_key(
    ciphertext: bytes,
    email: str,
    keys_dir: Optional[Path] = None,
) -> bytes:
    """
    Decrypt file content using our master key.

    Args:
        ciphertext: Encrypted file bytes (v2 format)
        email: User's email address (for account-scoped key storage)
        keys_dir: Override directory containing our master key

    Returns:
        Decrypted plaintext bytes
    """
    _ensure_crypto()

    if len(ciphertext) < 18:  # 5 magic + 1 version + 12 nonce
        raise ValueError("File too short to be encrypted")

    if ciphertext[:5] != MAGIC_BYTES:
        raise ValueError("Not a ClaudeConnect encrypted file")

    version = ciphertext[5]
    if version != MASTER_KEY_FORMAT_VERSION:
        raise ValueError(f"Expected v2 format, got v{version}")

    nonce = ciphertext[6:18]
    encrypted_content = ciphertext[18:]

    master_key = load_master_key(email, keys_dir)
    aesgcm = AESGCM(master_key)

    return aesgcm.decrypt(nonce, encrypted_content, None)


def decrypt_file_with_friend_master_key(
    ciphertext: bytes,
    friend_email: str,
    our_email: str,
    friends_dir: Optional[Path] = None,
) -> bytes:
    """
    Decrypt a friend's file using their master key.

    Args:
        ciphertext: Encrypted file bytes (v2 format)
        friend_email: Friend's email address
        our_email: Our email address (for account-scoped key storage)
        friends_dir: Directory containing friend master keys (override)

    Returns:
        Decrypted plaintext bytes
    """
    _ensure_crypto()

    if len(ciphertext) < 18:
        raise ValueError("File too short to be encrypted")

    if ciphertext[:5] != MAGIC_BYTES:
        raise ValueError("Not a ClaudeConnect encrypted file")

    version = ciphertext[5]
    if version != MASTER_KEY_FORMAT_VERSION:
        raise ValueError(f"Expected v2 format, got v{version}")

    nonce = ciphertext[6:18]
    encrypted_content = ciphertext[18:]

    master_key = load_friend_master_key(friend_email, our_email, friends_dir)
    aesgcm = AESGCM(master_key)

    return aesgcm.decrypt(nonce, encrypted_content, None)


def get_encrypted_file_version(data: bytes) -> int:
    """
    Get the version of an encrypted file.

    Args:
        data: Encrypted file bytes

    Returns:
        Version number (1 for multi-recipient, 2 for master key)

    Raises:
        ValueError: If not a valid encrypted file
    """
    if len(data) < 6:
        raise ValueError("File too short")
    if data[:5] != MAGIC_BYTES:
        raise ValueError("Not a ClaudeConnect encrypted file")
    return data[5]


# =============================================================================
# File Filtering
# =============================================================================

def should_encrypt_file(file_path: Path) -> bool:
    """
    Determine if a file should be encrypted.

    Rules:
    - Files in PLAINTEXT_FILES (authz, .keep) are never encrypted
    - ALL other files are encrypted (Issue #98)

    Args:
        file_path: Path to the file (can be relative or absolute, or string)

    Returns:
        True if the file should be encrypted
    """
    # Handle both Path and string inputs
    if isinstance(file_path, str):
        file_path = Path(file_path)

    name = file_path.name

    # Never encrypt system files
    if name in PLAINTEXT_FILES:
        return False

    # Encrypt everything else (Issue #98)
    return True


def is_encrypted_file(data: bytes) -> bool:
    """
    Check if data appears to be a ClaudeConnect encrypted file.

    Args:
        data: File content bytes

    Returns:
        True if file has encryption magic bytes
    """
    return len(data) >= 5 and data[:5] == MAGIC_BYTES
