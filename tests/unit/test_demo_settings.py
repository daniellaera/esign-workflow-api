from datetime import timedelta

import pytest
from pydantic import ValidationError

from esign.adapters.http.openapi import DESCRIPTION, description
from esign.config import DEMO_MAX_SIGNERS, DEMO_MAX_UPLOAD_BYTES, Settings

BASE = {"_env_file": None, "api_key": "operator-key-0123456789"}


def test_demo_mode_is_off_by_default() -> None:
    settings = Settings(**BASE)  # type: ignore[arg-type]
    assert not settings.demo_mode
    assert settings.max_upload_bytes == 10 * 1024 * 1024
    assert settings.max_signers == 10
    assert description(settings) == DESCRIPTION


def test_demo_mode_tightens_limits_and_disables_webhooks() -> None:
    settings = Settings(
        **BASE,  # type: ignore[arg-type]
        demo_mode=True,
        demo_api_key="public-demo-key",
        webhook_url="https://hooks.test/x",
        webhook_secret="secret-0123456789",
    )
    assert settings.max_upload_bytes == DEMO_MAX_UPLOAD_BYTES == 2 * 1024 * 1024
    assert settings.max_signers == DEMO_MAX_SIGNERS == 5
    assert settings.webhook_url is None
    assert settings.webhook_secret is None


def test_demo_mode_requires_a_demo_key() -> None:
    with pytest.raises(ValidationError, match="ESIGN_DEMO_API_KEY"):
        Settings(**BASE, demo_mode=True)  # type: ignore[arg-type]


def test_swagger_description_starts_with_the_demo_banner() -> None:
    settings = Settings(
        **BASE,  # type: ignore[arg-type]
        demo_mode=True,
        demo_api_key="public-demo-key",
        demo_retention=timedelta(hours=24),
    )
    text = description(settings).lstrip()
    assert text.startswith("> ⚠️ **Public demo.** All data is deleted after 24 h.")
    assert "Do not upload real or personal documents" in text
    assert "`public-demo-key`" in text
    assert "2 MB per PDF" in text
    assert "5 signers per envelope" in text
    assert "operator-key" not in text
    assert "`POST /envelopes/{envelope_id}/send`" in text
    assert "{id}" not in text
