"""Fixtures that run the real application (lifespan included) in-process.

Requests go through httpx's ASGI transport, so no server or port is needed.
The webhook receiver is mocked with respx, which intercepts outgoing httpx
traffic but not the ASGI transport.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from esign.config import Settings
from esign.domain.common import utc_now
from esign.main import create_app

API_KEY = "e2e-api-key-0123456789"
WEBHOOK_URL = "https://hooks.test/esign"
WEBHOOK_SECRET = "whsec-e2e-0123456789"


class FakeClock:
    """Starts at the real time (certificates must be valid now) and can jump forward."""

    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(mongo_uri: str, mongo_database_name: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        api_key=API_KEY,
        mongo_uri=mongo_uri,
        mongo_database=mongo_database_name,
        data_dir=tmp_path,
        public_base_url="http://testserver",
        webhook_url=WEBHOOK_URL,
        webhook_secret=WEBHOOK_SECRET,
        webhook_backoff_seconds=0.0,
        expose_outbox=True,
    )


@pytest.fixture
async def app(settings: Settings, clock: FakeClock) -> AsyncIterator[FastAPI]:
    application = create_app(settings, clock=clock)
    async with LifespanManager(application):
        yield application
        await application.state.container.mongo_client.drop_database(settings.mongo_database)


@pytest.fixture
def webhook_receiver() -> Iterator[respx.Route]:
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(WEBHOOK_URL).mock(return_value=httpx.Response(204))


@pytest.fixture
async def client(app: FastAPI, webhook_receiver: respx.Route) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers={"X-API-Key": API_KEY}
    ) as http:
        yield http


class Api:
    """Thin helpers so tests read as a sequence of business steps."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http

    async def upload(self, pdf: bytes) -> dict[str, Any]:
        response = await self.http.post(
            "/documents", files={"file": ("contract.pdf", pdf, "application/pdf")}
        )
        assert response.status_code == 201, response.text
        return response.json()  # type: ignore[no-any-return]

    async def create_envelope(
        self, document_id: str, signers: list[tuple[str, str]], **headers: str
    ) -> httpx.Response:
        return await self.http.post(
            "/envelopes",
            json={
                "title": "Service agreement",
                "document_id": document_id,
                "signers": [{"name": n, "email": e} for n, e in signers],
            },
            headers=headers,
        )

    async def sent_envelope(self, pdf: bytes, signers: list[tuple[str, str]]) -> str:
        document = await self.upload(pdf)
        created = await self.create_envelope(document["id"], signers)
        assert created.status_code == 201, created.text
        envelope_id: str = created.json()["id"]
        sent = await self.http.post(f"/envelopes/{envelope_id}/send")
        assert sent.status_code == 200, sent.text
        return envelope_id

    async def latest_token(self, envelope_id: str) -> str:
        """Read the newest signing link from the simulated mailbox."""
        response = await self.http.get(f"/dev/outbox/{envelope_id}")
        assert response.status_code == 200
        link: str = response.json()[-1]["signing_link"]
        return link.rsplit("/", 1)[-1]


@pytest.fixture
def api(client: httpx.AsyncClient) -> Api:
    return Api(client)


ALICE = ("Alice Example", "alice@example.com")
BOB = ("Bob Example", "bob@example.com")
