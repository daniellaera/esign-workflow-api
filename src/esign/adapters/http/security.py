"""API-key authentication for management endpoints."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

# auto_error=False so we raise our own 401 (rendered as a problem document).
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request, api_key: Annotated[str | None, Depends(_api_key_header)]
) -> None:
    expected: str = request.app.state.container.settings.api_key.get_secret_value()
    # compare_digest takes the same time whether the first or last byte differs.
    if api_key is None or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid X-API-Key")
