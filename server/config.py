"""Server configuration."""

from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Server settings from environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Data storage
    data_dir: Path = Path("/data/users")

    # Google OAuth — the single source of truth for BOTH the login flow
    # (routes/oauth.py) and ID-token audience verification (auth.py). These are
    # read from the un-prefixed env names (the canonical deploy vars); the
    # CC_-prefixed names are also accepted for backwards compatibility.
    #
    # IMPORTANT: keep these on one Settings field. Historically oauth.py read
    # GOOGLE_CLIENT_ID while auth.py read CC_GOOGLE_CLIENT_ID, so a deploy could
    # set one and not the other — login worked but audience verification silently
    # turned off (tokens minted for any other Google app were accepted). One field
    # makes that split impossible.
    google_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLIENT_ID", "CC_GOOGLE_CLIENT_ID"),
    )
    google_client_secret: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_CLIENT_SECRET", "CC_GOOGLE_CLIENT_SECRET"),
    )
    server_base_url: str = Field(
        default="https://claudeconnect.io",
        validation_alias=AliasChoices("SERVER_BASE_URL", "CC_SERVER_BASE_URL"),
    )

    # Optional: restrict to specific emails/domains
    allowed_domains: list[str] = []

    class Config:
        env_prefix = "CC_"
        env_file = ".env"
        extra = "ignore"


settings = Settings()
