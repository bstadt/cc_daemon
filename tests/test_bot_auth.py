#!/usr/bin/env python3
"""
Tests for Moltbook bot authentication flow.

Tests the bot claim/verify endpoints and JWT creation/validation.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import tempfile

import pytest

# Set up test environment before importing server modules
os.environ["CC_BOT_JWT_SECRET"] = "test-secret-for-bot-jwt-signing-1234567890"


class TestBotJWT:
    """Test bot JWT creation and validation."""

    def test_create_bot_token(self):
        """Test creating a bot JWT token."""
        from server.auth import create_bot_token, BOT_JWT_SECRET

        assert BOT_JWT_SECRET, "BOT_JWT_SECRET should be set"

        token = create_bot_token("testbot")
        assert token
        assert isinstance(token, str)
        assert len(token.split(".")) == 3  # JWT has 3 parts

    def test_validate_bot_token(self):
        """Test validating a bot JWT token."""
        from server.auth import create_bot_token, validate_bot_token

        token = create_bot_token("testbot")
        payload = validate_bot_token(token)

        assert payload is not None
        assert payload.email == "testbot@moltbook.cc.bot"
        assert payload.provider == "moltbook"
        assert payload.subject == "moltbook:testbot"

    def test_invalid_bot_token(self):
        """Test that invalid tokens are rejected."""
        from server.auth import validate_bot_token

        # Garbage token
        payload = validate_bot_token("not.a.valid.token")
        assert payload is None

        # Wrong secret
        import jwt
        fake_token = jwt.encode(
            {"email": "fake@moltbook.cc.bot", "iss": "claudeconnect"},
            "wrong-secret",
            algorithm="HS256",
        )
        payload = validate_bot_token(fake_token)
        assert payload is None

    def test_expired_bot_token(self):
        """Test that expired tokens are rejected."""
        from server.auth import validate_bot_token, BOT_JWT_SECRET
        import jwt

        # Create an already-expired token
        expired_token = jwt.encode(
            {
                "iss": "claudeconnect",
                "sub": "moltbook:expired",
                "email": "expired@moltbook.cc.bot",
                "provider": "moltbook",
                "iat": int(time.time()) - 1000,
                "exp": int(time.time()) - 100,  # Expired 100 seconds ago
            },
            BOT_JWT_SECRET,
            algorithm="HS256",
        )

        payload = validate_bot_token(expired_token)
        assert payload is None

    def test_generate_claim_nonce(self):
        """Test nonce generation."""
        from server.auth import generate_claim_nonce

        nonce1 = generate_claim_nonce()
        nonce2 = generate_claim_nonce()

        assert nonce1 != nonce2
        assert len(nonce1) > 20  # Should be sufficiently long


class TestBotRoutes:
    """Test bot claim/verify endpoints."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary data directory for tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_claim_endpoint(self, temp_data_dir):
        """Test the /bot/claim endpoint."""
        from server.routes.bot import (
            ClaimRequest,
            _load_claims,
            _save_claims,
            CLAIM_EXPIRY_SECONDS,
        )
        import server.routes.bot as bot_module

        # Patch the data directory
        with patch.object(bot_module, "BOT_DATA_DIR", temp_data_dir / "_bot"):
            with patch.object(bot_module, "CLAIMS_FILE", temp_data_dir / "_bot" / "claims.json"):
                # Import after patching
                from server.routes.bot import claim_handle

                # Create a claim
                import asyncio
                request = ClaimRequest(handle="TestBot")
                response = asyncio.run(claim_handle(request))

                assert response.nonce
                assert response.challenge_text == f"cc-claim:testbot:{response.nonce}"
                assert response.exp > int(time.time())
                assert response.exp <= int(time.time()) + CLAIM_EXPIRY_SECONDS + 1

    def test_claim_normalizes_handle(self, temp_data_dir):
        """Test that handles are normalized to lowercase."""
        from server.routes.bot import ClaimRequest
        import server.routes.bot as bot_module

        with patch.object(bot_module, "BOT_DATA_DIR", temp_data_dir / "_bot"):
            with patch.object(bot_module, "CLAIMS_FILE", temp_data_dir / "_bot" / "claims.json"):
                from server.routes.bot import claim_handle

                import asyncio
                request = ClaimRequest(handle="MyBOT_Test")
                response = asyncio.run(claim_handle(request))

                # Challenge should have lowercase handle
                assert "mybot_test" in response.challenge_text

    def test_invalid_handle_format(self, temp_data_dir):
        """Test that invalid handle formats are rejected."""
        from server.routes.bot import ClaimRequest
        from fastapi import HTTPException
        import server.routes.bot as bot_module

        with patch.object(bot_module, "BOT_DATA_DIR", temp_data_dir / "_bot"):
            with patch.object(bot_module, "CLAIMS_FILE", temp_data_dir / "_bot" / "claims.json"):
                from server.routes.bot import claim_handle

                import asyncio

                # Handle with spaces
                request = ClaimRequest(handle="my bot")
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(claim_handle(request))
                assert exc.value.status_code == 400

                # Handle with special chars
                request = ClaimRequest(handle="my@bot")
                with pytest.raises(HTTPException) as exc:
                    asyncio.run(claim_handle(request))
                assert exc.value.status_code == 400


class TestTokenPayloadProvider:
    """Test that TokenPayload correctly tracks provider."""

    def test_google_token_has_google_provider(self):
        """Test that Google tokens have provider='google'."""
        from server.auth import TokenPayload

        payload = TokenPayload(
            email="test@gmail.com",
            subject="12345",
            issued_at=int(time.time()),
            expires_at=int(time.time()) + 3600,
        )
        assert payload.provider == "google"

    def test_bot_token_has_moltbook_provider(self):
        """Test that bot tokens have provider='moltbook'."""
        from server.auth import create_bot_token, validate_bot_token

        token = create_bot_token("testbot")
        payload = validate_bot_token(token)

        assert payload.provider == "moltbook"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
