"""Sends webhook requests with httpx."""

from collections.abc import Mapping

import httpx

from esign.application.ports import WebhookTransportError


class HttpxWebhookSender:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        try:
            response = await self._client.post(url, content=body, headers=dict(headers))
        except httpx.HTTPError as exc:
            raise WebhookTransportError(f"{type(exc).__name__}: {exc}") from exc
        return response.status_code
