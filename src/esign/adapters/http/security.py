"""API-key authentication for management endpoints."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from esign.config import Settings

# auto_error=False so we raise our own 401 (rendered as a problem document).
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request, api_key: Annotated[str | None, Depends(_api_key_header)]
) -> None:
    settings: Settings = request.app.state.container.settings
    accepted = [settings.api_key]
    if settings.demo_mode and settings.demo_api_key is not None:
        accepted.append(settings.demo_api_key)  # public by design, see README "Live demo"
    if api_key is None or not any(_same(api_key, key) for key in accepted):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-API-Key")


def _same(given: str, expected: SecretStr) -> bool:
    # compare_digest takes the same time whether the first or last byte differs.
    return secrets.compare_digest(given.encode(), expected.get_secret_value().encode())
