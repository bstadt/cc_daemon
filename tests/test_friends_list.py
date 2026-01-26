from claudeconnect.cli import parse_friends_from_authz


def test_parse_friends_from_authz_basic():
    authz_content = """# Public-Key: deadbeef

[/]
me@example.com = rw
alice@example.com = r
bob@example.com = rw
charlie@example.com = w

[/claudeconnect/with-claudeconnect-io]
* = rw
"""
    friends = parse_friends_from_authz(authz_content, "me@example.com")
    assert friends == ["alice@example.com", "bob@example.com"]


def test_parse_friends_from_authz_ignores_other_sections():
    authz_content = """[/]
owner@example.com = rw

[/claudeconnect/with-alice-example-com]
alice@example.com = rw
"""
    friends = parse_friends_from_authz(authz_content, "owner@example.com")
    assert friends == []
