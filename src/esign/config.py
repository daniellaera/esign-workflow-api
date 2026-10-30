"""Configuration from environment variables (prefix ``ESIGN_``)."""

from datetime import timedelta
from pathlib import Path
from typing import Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Hard limits applied when ESIGN_DEMO_MODE is on.
DEMO_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
DEMO_MAX_SIGNERS = 5


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ESIGN_", env_file=".env", extra="ignore")

    api_key: SecretStr = Field(min_length=16)
    mongo_uri: str = "mongodb://localhost:27017/?directConnection=true"
    mongo_database: str = "esign"
    data_dir: Path = Path("data")
    # Used to build signing links; the address signers can reach the API on.
    public_base_url: str = "http://localhost:8000"

    max_upload_bytes: int = 10 * 1024 * 1024
    max_signers: int = Field(default=10, ge=1, le=10)
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

    # Addresses of reverse proxies whose X-Forwarded-For header is trusted,
    # e.g. ESIGN_TRUSTED_PROXIES='["203.0.113.10"]'. Empty: trust nobody.
    trusted_proxies: list[str] = []

    # Public demo mode: data retention, tighter limits, rate limiting, a public
    # API key, no outgoing webhooks. Never enable it for real documents.
    demo_mode: bool = False
    demo_api_key: SecretStr | None = Field(default=None, min_length=8)
    demo_retention: timedelta = timedelta(hours=24)
    demo_cleanup_interval: timedelta = timedelta(minutes=10)
    rate_limit_per_minute: int = Field(default=120, ge=1)
    envelope_limit_per_hour: int = Field(default=30, ge=1)

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _webhook_needs_secret(self) -> Self:
        if self.webhook_url is not None and self.webhook_secret is None:
            raise ValueError("ESIGN_WEBHOOK_SECRET is required when ESIGN_WEBHOOK_URL is set")
        return self

    @model_validator(mode="after")
    def _apply_demo_mode(self) -> Self:
        if not self.demo_mode:
            return self
        if self.demo_api_key is None:
            raise ValueError("ESIGN_DEMO_API_KEY is required when ESIGN_DEMO_MODE is on")
        self.max_upload_bytes = min(self.max_upload_bytes, DEMO_MAX_UPLOAD_BYTES)
        self.max_signers = min(self.max_signers, DEMO_MAX_SIGNERS)
        # A public demo must not make outgoing requests on behalf of strangers.
        self.webhook_url = None
        self.webhook_secret = None
        return self
