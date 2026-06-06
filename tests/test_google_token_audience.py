"""Regression tests for Google ID token audience verification.

Guards against the audience-verification bypass: a token minted for a *different*
Google OAuth app must be rejected, and validation must fail closed when no client
id is configured. See server/auth.py and the GOOGLE_CLIENT_ID / CC_GOOGLE_CLIENT_ID
unification in server/config.py.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import server.auth as auth_module
from server.config import settings

OUR_CLIENT_ID = "our-app.apps.googleusercontent.com"
OTHER_CLIENT_ID = "some-other-app.apps.googleusercontent.com"


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture
def rsa_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture
def patched_auth(monkeypatch, rsa_keypair):
    """Point auth validation at a key we control and a known client id."""
    _priv, pub = rsa_keypair

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, _token):
            return _FakeSigningKey(pub)

    monkeypatch.setattr(auth_module, "_get_jwk_client", lambda: _FakeJWKClient())
    monkeypatch.setattr(settings, "google_client_id", OUR_CLIENT_ID)
    return rsa_keypair


def _make_token(private_key, aud, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": aud,
        "email": "user@example.com",
        "email_verified": True,
        "sub": "subject-123",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test"})


def test_token_for_our_app_is_accepted(patched_auth):
    priv, _pub = patched_auth
    token = _make_token(priv, aud=OUR_CLIENT_ID)
    payload = auth_module.validate_google_id_token(token)
    assert payload is not None
    assert payload.email == "user@example.com"


def test_token_for_other_app_is_rejected(patched_auth):
    """The core fix: a validly Google-signed token minted for a different app
    (different aud) must NOT authenticate against this server."""
    priv, _pub = patched_auth
    token = _make_token(priv, aud=OTHER_CLIENT_ID)
    assert auth_module.validate_google_id_token(token) is None


def test_fails_closed_when_no_client_id(monkeypatch, rsa_keypair):
    """With no configured client id, validation must reject everything rather
    than silently skip the audience check."""
    priv, pub = rsa_keypair

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, _token):
            return _FakeSigningKey(pub)

    monkeypatch.setattr(auth_module, "_get_jwk_client", lambda: _FakeJWKClient())
    monkeypatch.setattr(settings, "google_client_id", "")

    token = _make_token(priv, aud=OUR_CLIENT_ID)
    assert auth_module.validate_google_id_token(token) is None


def test_unverified_email_rejected(patched_auth):
    priv, _pub = patched_auth
    token = _make_token(priv, aud=OUR_CLIENT_ID, email_verified=False)
    assert auth_module.validate_google_id_token(token) is None
