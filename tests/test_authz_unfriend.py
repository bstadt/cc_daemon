from claudeconnect.cli import remove_friend_from_authz


def test_remove_friend_from_authz_removes_sections(tmp_path):
    authz_path = tmp_path / "authz"
    authz_path.write_text(
        "\n".join(
            [
                "[/]",
                "owner@example.com = rw",
                "alice@example.com = r",
                "",
                "[/claudeconnect/with-alice-example-com]",
                "owner@example.com = rw",
                "alice@example.com = rw",
                "",
                "[/claudeconnect/with-bob-example-com]",
                "owner@example.com = rw",
                "bob@example.com = rw",
            ]
        )
    )

    changed = remove_friend_from_authz(authz_path, "alice@example.com")
    assert changed is True

    content = authz_path.read_text()
    assert "alice@example.com" not in content
    assert "bob@example.com = rw" in content


def test_remove_friend_from_authz_handles_legacy_section(tmp_path):
    authz_path = tmp_path / "authz"
    authz_path.write_text(
        "\n".join(
            [
                "[/]",
                "owner@example.com = rw",
                "carol@example.com = r",
                "",
                "[/claudeconnect/conversations]",
                "owner@example.com = rw",
                "carol@example.com = rw",
            ]
        )
    )

    changed = remove_friend_from_authz(authz_path, "carol@example.com")
    assert changed is True

    content = authz_path.read_text()
    assert "carol@example.com" not in content
