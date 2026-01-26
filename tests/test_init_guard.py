from pathlib import Path

from claudeconnect.cli import _read_authz_owner_email, _read_authz_public_key


def test_read_authz_public_key_variants(tmp_path: Path) -> None:
    authz = tmp_path / "authz"
    authz.write_text(
        """
# Public-Key: aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899

[/]
user@example.com = rw
""".strip()
    )
    assert _read_authz_public_key(authz) == (
        "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899"
    )

    authz.write_text(
        """
# Public Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
[/]
user@example.com = rw
""".strip()
    )
    assert _read_authz_public_key(authz) == (
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )


def test_read_authz_owner_email_from_root(tmp_path: Path) -> None:
    authz = tmp_path / "authz"
    authz.write_text(
        """
# Public-Key: aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899

[/]
owner@example.com = rw
other@example.com = r

[/claudeconnect/with-owner-example-com]
friend@example.com = rw
""".strip()
    )
    assert _read_authz_owner_email(authz) == "owner@example.com"


def test_read_authz_owner_email_missing(tmp_path: Path) -> None:
    authz = tmp_path / "authz"
    authz.write_text(
        """
[/]
friend@example.com = r

[/notes]
owner@example.com = rw
""".strip()
    )
    assert _read_authz_owner_email(authz) is None
