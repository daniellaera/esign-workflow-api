"""Configuration from environment variables (prefix ``ESIGN_``)."""

from datetime import timedelta
from pathlib import Path
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ESIGN_", env_file=".env", extra="ignore")

    api_key: SecretStr = Field(min_length=16)
    mongo_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongo_database: str = "esign"
    data_dir: Path = Path("data")
    # Used to build signing links; the address signers can reach the API on.
    public_base_url: str = "http://localhost:8000"

    max_upload_bytes: int = 10 * 1024 * 1024
    # Durations accept seconds or ISO 8601, e.g. ESIGN_SIGNING_LINK_TTL=PT72H.
    signing_link_ttl: timedelta = timedelta(hours=72)
    signer_certificate_validity: timedelta = timedelta(hours=1)

    webhook_url: AnyHttpUrl | None = None
    webhook_secret: SecretStr | None = None
    webhook_max_retries: int = Field(default=3, ge=0)
    webhook_backoff_seconds: float = Field(default=1.0, ge=0)
    webhook_timeout_seconds: float = Field(default=5.0, gt=0)

    # Development conveniences. Both expose signing links, which are bearer
    # credentials, so they must stay off in production.
    log_signing_links: bool = False
    expose_outbox: bool = False

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _webhook_needs_secret(self) -> Self:
        if self.webhook_url is not None and self.webhook_secret is None:
            raise ValueError("ESIGN_WEBHOOK_SECRET is required when ESIGN_WEBHOOK_URL is set")
        return self
