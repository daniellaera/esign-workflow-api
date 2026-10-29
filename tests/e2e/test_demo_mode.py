"""The public demo configuration, end to end."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import httpx
import pytest
import respx
from asgi_lifespan import LifespanManager

from esign.config import Settings
from esign.main import create_app
from tests.e2e.conftest import ALICE, BOB, Api, FakeClock

pytestmark = pytest.mark.integration

DEMO_KEY = "public-demo-key-123"

type DemoClientFactory = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def make_demo_client(
    settings: Settings, clock: FakeClock, webhook_receiver: respx.Route
) -> AsyncIterator[DemoClientFactory]:
    """Start the app in demo mode with optional setting overrides; returns a client."""
    async with AsyncExitStack() as stack:

        async def make(**overrides: Any) -> httpx.AsyncClient:
            data = settings.model_dump() | {"demo_mode": True, "demo_api_key": DEMO_KEY}
            demo_settings = Settings(_env_file=None, **(data | overrides))
            app = create_app(demo_settings, clock=clock)
            await stack.enter_async_context(LifespanManager(app))
            stack.push_async_callback(
                app.state.container.mongo_client.drop_database, demo_settings.mongo_database
            )
            return await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://testserver",
                    headers={"X-API-Key": DEMO_KEY},
                )
            )

        yield make


async def test_swagger_starts_with_the_demo_banner(make_demo_client: DemoClientFactory) -> None:
    http = await make_demo_client()
    spec = (await http.get("/openapi.json")).json()
    assert spec["info"]["description"].lstrip().startswith("> ⚠️ **Public demo.**")
    assert "/envelopes/{envelope_id}/signing-link" in spec["paths"]


async def test_demo_key_and_operator_key_both_work(make_demo_client: DemoClientFactory) -> None:
    http = await make_demo_client()
    assert (await http.get("/envelopes/missing")).status_code == 404  # authenticated
    operator = {"X-API-Key": "e2e-api-key-0123456789"}
    assert (await http.get("/envelopes/missing", headers=operator)).status_code == 404
    assert (await http.get("/envelopes/missing", headers={"X-API-Key": "nope"})).status_code == 401


async def test_whole_flow_from_the_api_alone_and_no_webhook(
    make_demo_client: DemoClientFactory, webhook_receiver: respx.Route, sample_pdf: bytes
) -> None:
    api = Api(await make_demo_client())
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE, BOB])

    for expected_order, expected_status in ((1, "PARTIALLY_SIGNED"), (2, "COMPLETED")):
        link = (await api.http.get(f"/envelopes/{envelope_id}/signing-link")).json()
        assert link["signer_order"] == expected_order
        assert link["signing_link"].endswith(link["token"])
        signed = await api.http.post(f"/sign/{link['token']}")
        assert signed.json()["envelope_status"] == expected_status

    verification = (await api.http.get(f"/envelopes/{envelope_id}/verify")).json()
    assert verification["all_signatures_valid"] is True
    # No open link once completed.
    assert (await api.http.get(f"/envelopes/{envelope_id}/signing-link")).status_code == 404
    # Webhooks are disabled in demo mode even though the base settings configure one.
    assert webhook_receiver.call_count == 0
    assert (await api.http.get(f"/envelopes/{envelope_id}/webhook-deliveries")).json() == []


async def test_signing_link_for_a_draft_is_404(
    make_demo_client: DemoClientFactory, sample_pdf: bytes
) -> None:
    api = Api(await make_demo_client())
    document = await api.upload(sample_pdf)
    draft = (await api.create_envelope(document["id"], [ALICE])).json()
    response = await api.http.get(f"/envelopes/{draft['id']}/signing-link")
    assert response.status_code == 404


async def test_upload_over_2_mb_is_rejected(make_demo_client: DemoClientFactory) -> None:
    http = await make_demo_client()
    too_big = b"%PDF-1.7\n" + b"0" * (2 * 1024 * 1024)
    response = await http.post("/documents", files={"file": ("big.pdf", too_big)})
    assert response.status_code == 413


async def test_more_than_5_signers_is_rejected(
    make_demo_client: DemoClientFactory, sample_pdf: bytes
) -> None:
    api = Api(await make_demo_client())
    document = await api.upload(sample_pdf)
    signers = [(f"Signer {i}", f"s{i}@example.com") for i in range(6)]
    response = await api.create_envelope(document["id"], signers)
    assert response.status_code == 422
    assert "at most 5 signers" in response.json()["detail"]


async def test_requests_per_minute_limit_returns_429(make_demo_client: DemoClientFactory) -> None:
    http = await make_demo_client(rate_limit_per_minute=3)
    for _ in range(3):
        assert (await http.get("/envelopes/missing")).status_code == 404
    limited = await http.get("/envelopes/missing")
    assert limited.status_code == 429
    assert limited.headers["content-type"] == "application/problem+json"
    assert limited.json()["type"] == "urn:esign:problem:rate-limited"
    assert int(limited.headers["Retry-After"]) > 0
    assert limited.headers["X-Request-ID"]
    # Health probes are never throttled.
    assert (await http.get("/health/live")).status_code == 200


async def test_envelopes_per_hour_limit_returns_429(
    make_demo_client: DemoClientFactory, sample_pdf: bytes
) -> None:
    api = Api(await make_demo_client(envelope_limit_per_hour=2, rate_limit_per_minute=1000))
    document = await api.upload(sample_pdf)
    statuses = [(await api.create_envelope(document["id"], [ALICE])).status_code for _ in range(3)]
    assert statuses == [201, 201, 429]


async def test_forwarded_for_is_used_only_from_a_trusted_proxy(
    make_demo_client: DemoClientFactory,
) -> None:
    # The ASGI test client connects from 127.0.0.1.
    trusted = await make_demo_client(rate_limit_per_minute=1, trusted_proxies=["127.0.0.1"])
    first = await trusted.get("/envelopes/x", headers={"X-Forwarded-For": "203.0.113.1"})
    other_ip = await trusted.get("/envelopes/x", headers={"X-Forwarded-For": "203.0.113.2"})
    assert (first.status_code, other_ip.status_code) == (404, 404)  # separate buckets
    again = await trusted.get("/envelopes/x", headers={"X-Forwarded-For": "203.0.113.1"})
    assert again.status_code == 429


async def test_forwarded_for_is_ignored_from_untrusted_peers(
    make_demo_client: DemoClientFactory,
) -> None:
    untrusted = await make_demo_client(rate_limit_per_minute=1, trusted_proxies=[])
    first = await untrusted.get("/envelopes/x", headers={"X-Forwarded-For": "203.0.113.1"})
    spoofed = await untrusted.get("/envelopes/x", headers={"X-Forwarded-For": "203.0.113.99"})
    assert (first.status_code, spoofed.status_code) == (404, 429)  # same client, spoofing fails


async def test_signing_link_endpoint_does_not_exist_outside_demo_mode(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get("/envelopes/x/signing-link")).status_code == 404
    spec = (await client.get("/openapi.json")).json()
    assert "/envelopes/{envelope_id}/signing-link" not in spec["paths"]
    assert "Public demo" not in spec["info"]["description"]
