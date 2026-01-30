"""Bot authentication endpoints for Moltbook verification."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx

from ..auth import create_bot_token, generate_claim_nonce
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bot", tags=["bot"])

# Claim expiry: 10 minutes
CLAIM_EXPIRY_SECONDS = 10 * 60

# Storage paths
BOT_DATA_DIR = settings.data_dir / "_bot"
CLAIMS_FILE = BOT_DATA_DIR / "claims.json"
BOTS_FILE = BOT_DATA_DIR / "bots.json"


def _ensure_bot_data_dir() -> None:
    """Ensure bot data directory exists."""
    BOT_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_claims() -> dict:
    """Load pending claims from file."""
    if not CLAIMS_FILE.exists():
        return {}
    try:
        return json.loads(CLAIMS_FILE.read_text())
    except Exception as e:
        logger.error(f"Error loading claims: {e}")
        return {}


def _save_claims(claims: dict) -> None:
    """Save pending claims to file."""
    _ensure_bot_data_dir()
    CLAIMS_FILE.write_text(json.dumps(claims, indent=2))


def _load_bots() -> dict:
    """Load registered bots from file."""
    if not BOTS_FILE.exists():
        return {}
    try:
        return json.loads(BOTS_FILE.read_text())
    except Exception as e:
        logger.error(f"Error loading bots: {e}")
        return {}


def _save_bots(bots: dict) -> None:
    """Save registered bots to file."""
    _ensure_bot_data_dir()
    BOTS_FILE.write_text(json.dumps(bots, indent=2))


def _clean_expired_claims(claims: dict) -> dict:
    """Remove expired claims."""
    now = int(time.time())
    return {k: v for k, v in claims.items() if v.get("exp", 0) > now}


# --- Request/Response Models ---


class ClaimRequest(BaseModel):
    """Request to claim a Moltbook handle."""
    handle: str


class ClaimResponse(BaseModel):
    """Response with challenge for verification."""
    nonce: str
    exp: int
    challenge_text: str


class VerifyRequest(BaseModel):
    """Request to verify a Moltbook handle."""
    handle: str
    post_url: str = ""  # Optional, kept for backwards compat but not used


class VerifyResponse(BaseModel):
    """Response with bot token after successful verification."""
    token: str
    email: str
    handle: str


# --- Endpoints ---


@router.post("/claim", response_model=ClaimResponse)
async def claim_handle(request: ClaimRequest):
    """
    Start bot authentication by claiming a Moltbook handle.

    Returns a challenge text that must be posted to Moltbook.
    """
    handle = request.handle.strip().lower()

    if not handle:
        raise HTTPException(status_code=400, detail="Handle is required")

    # Validate handle format (alphanumeric, underscore, hyphen)
    if not re.match(r"^[a-z0-9_-]+$", handle):
        raise HTTPException(
            status_code=400,
            detail="Invalid handle format. Use alphanumeric, underscore, or hyphen only."
        )

    # Generate claim
    nonce = generate_claim_nonce()
    exp = int(time.time()) + CLAIM_EXPIRY_SECONDS
    challenge_text = f"cc-claim:{handle}:{nonce}"

    # Store claim
    claims = _load_claims()
    claims = _clean_expired_claims(claims)
    claims[handle] = {
        "nonce": nonce,
        "exp": exp,
        "challenge_text": challenge_text,
    }
    _save_claims(claims)

    logger.info(f"Bot claim started for handle: {handle}")

    return ClaimResponse(
        nonce=nonce,
        exp=exp,
        challenge_text=challenge_text,
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify_post(request: VerifyRequest):
    """
    Verify a Moltbook user owns a handle by checking their profile via Moltbook API.

    The challenge can be in:
    - Profile description
    - A recent post title or content

    On success, returns a long-lived bot token.
    """
    handle = request.handle.strip().lower()

    if not handle:
        raise HTTPException(status_code=400, detail="Handle is required")

    # Load and check claim
    claims = _load_claims()
    claims = _clean_expired_claims(claims)
    _save_claims(claims)

    claim = claims.get(handle)
    if not claim:
        raise HTTPException(
            status_code=400,
            detail="No pending claim for this handle. Call /bot/claim first."
        )

    challenge_text = claim["challenge_text"]

    # Fetch user profile via Moltbook API
    profile_url = f"https://www.moltbook.com/api/v1/agents/profile?name={handle}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(profile_url)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch Moltbook profile: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to fetch Moltbook profile for {handle}: {e}"
        )

    if not data.get("success"):
        raise HTTPException(
            status_code=400,
            detail=f"Moltbook user '{handle}' not found"
        )

    agent = data.get("agent", {})
    recent_posts = data.get("recentPosts", [])

    # Check profile description for challenge
    description = agent.get("description", "") or ""
    found_in = None

    if challenge_text in description:
        found_in = "profile description"
    else:
        # Check recent posts
        for post in recent_posts:
            title = post.get("title", "") or ""
            content = post.get("content", "") or ""
            if challenge_text in title or challenge_text in content:
                found_in = f"post '{title[:50]}...'" if len(title) > 50 else f"post '{title}'"
                break

    if not found_in:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Challenge text not found. Put '{challenge_text}' in your "
                "Moltbook profile description OR in a new post, then try again."
            )
        )

    # Success! Remove claim (one-time use)
    del claims[handle]
    _save_claims(claims)

    # Create bot token
    try:
        token = create_bot_token(handle)
    except ValueError as e:
        logger.error(f"Failed to create bot token: {e}")
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: bot tokens not configured"
        )

    # Register bot
    bots = _load_bots()
    email = f"{handle}@moltbook.cc.bot"
    bots[handle] = {
        "email": email,
        "verified_at": int(time.time()),
        "verified_via": found_in,
    }
    _save_bots(bots)

    logger.info(f"Bot verified successfully: {handle} -> {email}")

    return VerifyResponse(
        token=token,
        email=email,
        handle=handle,
    )


@router.get("/status/{handle}")
async def get_bot_status(handle: str):
    """
    Check if a handle is registered as a bot.
    """
    handle = handle.strip().lower()
    bots = _load_bots()

    if handle not in bots:
        raise HTTPException(status_code=404, detail="Bot not found")

    bot = bots[handle]
    return {
        "handle": handle,
        "email": bot["email"],
        "verified_at": bot.get("verified_at"),
    }
