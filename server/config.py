"""Server configuration."""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Server settings from environment variables."""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Data storage
    data_dir: Path = Path("/data/users")

    # Google OAuth (for JWT validation)
    google_client_id: str = ""

    # Optional: restrict to specific emails/domains
    allowed_domains: list[str] = []

    class Config:
        env_prefix = "CC_"
        env_file = ".env"


settings = Settings()
