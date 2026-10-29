"""Error handling, security and the less common workflow paths."""

from datetime import timedelta

import httpx
import pytest
import respx
from fastapi import FastAPI

from tests.e2e.conftest import ALICE, BOB, Api, FakeClock

pytestmark = pytest.mark.integration

PROBLEM_JSON = "application/problem+json"


def assert_problem(response: httpx.Response, status: int, slug: str | None = None) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM_JSON
    body = response.json()
    assert body["status"] == status
    assert body["title"]
    if slug is not None:
        assert body["type"] == f"urn:esign:problem:{slug}"


# --- Health, security, plumbing -------------------------------------------------------


async def test_health_endpoints(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    assert (await client.get("/health/ready")).json() == {"status": "ok"}


async def test_readiness_fails_when_database_is_down(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_ping() -> None:
        raise ConnectionError("no route to host")

    monkeypatch.setattr(app.state.container.database, "ping", broken_ping)
    assert_problem(await client.get("/health/ready"), 503)


@pytest.mark.parametrize("key", [None, "wrong-key-0123456789"])
async def test_management_endpoints_require_api_key(
    client: httpx.AsyncClient, key: str | None
) -> None:
    headers = {"X-API-Key": key} if key else {}
    client.headers.pop("X-API-Key")
    response = await client.get("/envelopes/anything", headers=headers)
    assert_problem(response, 401)


async def test_request_id_is_echoed_or_generated(client: httpx.AsyncClient) -> None:
    echoed = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["X-Request-ID"] == "abc-123"
    replaced = await client.get("/health/live", headers={"X-Request-ID": "bad id\nwith newline"})
    assert len(replaced.headers["X-Request-ID"]) == 32


async def test_unexpected_errors_become_500_problems(
    client: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def explode(envelope_id: str) -> None:
        raise RuntimeError("database driver bug")

    monkeypatch.setattr(app.state.container.envelopes, "get", explode)
    response = await client.get("/envelopes/x")
    assert_problem(response, 500)
    assert "database driver bug" not in response.text
    assert response.headers["X-Request-ID"]


async def test_unknown_route_is_a_problem(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/nope"), 404)


async def test_test_ca_certificate_is_published(client: httpx.AsyncClient) -> None:
    response = await client.get("/trust/test-ca.pem", headers={"X-API-Key": ""})
    assert response.status_code == 200
    assert response.text.startswith("-----BEGIN CERTIFICATE-----")


# --- Uploads --------------------------------------------------------------------------


async def test_non_pdf_upload_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/documents", files={"file": ("x.pdf", b"GIF89a", "image/gif")})
    assert_problem(response, 422, "invalid-document")


async def test_corrupt_pdf_upload_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post("/documents", files={"file": ("x.pdf", b"%PDF-1.7\ngarbage")})
    assert_problem(response, 422, "invalid-document")


async def test_upload_over_10_mb_is_rejected(client: httpx.AsyncClient) -> None:
    too_big = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024)
    response = await client.post("/documents", files={"file": ("big.pdf", too_big)})
    assert_problem(response, 413, "document-too-large")


async def test_upload_keeps_only_the_base_filename(api: Api, sample_pdf: bytes) -> None:
    response = await api.http.post(
        "/documents", files={"file": ("../../etc/contract.pdf", sample_pdf)}
    )
    assert response.json()["filename"] == "contract.pdf"


# --- Envelope creation and idempotency ---------------------------------------------------


async def test_invalid_body_lists_errors_without_echoing_input(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/envelopes",
        json={"title": "", "document_id": "d", "signers": [{"name": "A", "email": "not-an-email"}]},
    )
    assert_problem(response, 422, "invalid-request")
    locations = [error["location"] for error in response.json()["errors"]]
    assert ["body", "title"] in locations
    assert ["body", "signers", 0, "email"] in locations
    assert "not-an-email" not in response.text


async def test_unknown_document_is_rejected(api: Api) -> None:
    response = await api.create_envelope("missing", [ALICE])
    assert_problem(response, 422, "validation-failed")


async def test_duplicate_signer_is_rejected(api: Api, sample_pdf: bytes) -> None:
    document = await api.upload(sample_pdf)
    response = await api.create_envelope(document["id"], [ALICE, ALICE])
    assert_problem(response, 422, "validation-failed")


async def test_idempotency_key_replays_the_original_envelope(api: Api, sample_pdf: bytes) -> None:
    document = await api.upload(sample_pdf)
    first = await api.create_envelope(document["id"], [ALICE], **{"Idempotency-Key": "k-1"})
    again = await api.create_envelope(document["id"], [ALICE], **{"Idempotency-Key": "k-1"})
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers


async def test_idempotency_key_with_a_different_body_is_rejected(
    api: Api, sample_pdf: bytes
) -> None:
    document = await api.upload(sample_pdf)
    await api.create_envelope(document["id"], [ALICE], **{"Idempotency-Key": "k-2"})
    response = await api.create_envelope(document["id"], [BOB], **{"Idempotency-Key": "k-2"})
    assert_problem(response, 422, "idempotency-key-reused")


async def test_unknown_envelope_is_404(client: httpx.AsyncClient) -> None:
    for path in ("", "/send", "/document", "/verify", "/audit", "/webhook-deliveries"):
        method = client.post if path == "/send" else client.get
        assert_problem(await method(f"/envelopes/missing{path}"), 404, "not-found")


# --- State machine via the API ----------------------------------------------------------


async def test_sending_twice_is_rejected_with_details(api: Api, sample_pdf: bytes) -> None:
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE])
    response = await api.http.post(f"/envelopes/{envelope_id}/send")
    assert_problem(response, 409, "invalid-state-transition")
    assert response.json()["current_status"] == "SENT"
    assert response.json()["target_status"] == "SENT"


async def test_unknown_signing_link_is_404(client: httpx.AsyncClient) -> None:
    assert_problem(await client.get("/sign/" + "x" * 43), 404, "signing-link-not-found")


async def test_signer_can_download_the_document(api: Api, sample_pdf: bytes) -> None:
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE])
    token = await api.latest_token(envelope_id)
    response = await api.http.get(f"/sign/{token}/document")
    assert response.content == sample_pdf
    assert response.headers["Referrer-Policy"] == "no-referrer"


async def test_decline_closes_envelope_and_notifies(
    api: Api, app: FastAPI, webhook_receiver: respx.Route, sample_pdf: bytes
) -> None:
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE, BOB])
    token = await api.latest_token(envelope_id)

    response = await api.http.post(f"/sign/{token}/decline", json={"reason": "Wrong amount"})
    assert response.json()["envelope_status"] == "DECLINED"
    assert response.json()["you"]["decline_reason"] == "Wrong amount"
    assert_problem(await api.http.post(f"/sign/{token}"), 410, "signing-link-inactive")

    await app.state.container.webhooks.drain()
    assert webhook_receiver.call_count == 1
    assert b'"event":"envelope.declined"' in webhook_receiver.calls.last.request.content
    # Nobody else was invited after the decline.
    outbox = (await api.http.get(f"/dev/outbox/{envelope_id}")).json()
    assert len(outbox) == 1


async def test_decline_without_body(api: Api, sample_pdf: bytes) -> None:
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE])
    token = await api.latest_token(envelope_id)
    response = await api.http.post(f"/sign/{token}/decline")
    assert response.json()["you"]["decline_reason"] is None


async def test_expired_link_expires_the_envelope(
    api: Api, clock: FakeClock, sample_pdf: bytes
) -> None:
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE, BOB])
    token = await api.latest_token(envelope_id)

    clock.advance(timedelta(hours=72))
    assert_problem(await api.http.post(f"/sign/{token}"), 410, "signing-link-expired")

    envelope = (await api.http.get(f"/envelopes/{envelope_id}")).json()
    assert envelope["status"] == "EXPIRED"
    audit = (await api.http.get(f"/envelopes/{envelope_id}/audit")).json()
    assert audit["events"][-1]["type"] == "envelope.expired"
    assert audit["chain_valid"] is True
    # Once expired, the envelope is closed: the link is reported as inactive.
    assert_problem(await api.http.get(f"/sign/{token}"), 410, "signing-link-inactive")


async def test_failing_webhook_is_marked_failed_after_retries(
    api: Api, app: FastAPI, webhook_receiver: respx.Route, sample_pdf: bytes
) -> None:
    webhook_receiver.side_effect = httpx.ConnectError("connection refused")
    envelope_id = await api.sent_envelope(sample_pdf, [ALICE])
    await api.http.post(f"/sign/{await api.latest_token(envelope_id)}")
    await app.state.container.webhooks.drain()

    (delivery,) = (await api.http.get(f"/envelopes/{envelope_id}/webhook-deliveries")).json()
    assert delivery["status"] == "FAILED"
    assert len(delivery["attempts"]) == 4
    assert "ConnectError" in delivery["attempts"][0]["error"]


async def test_root_redirects_to_docs_and_is_not_documented(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"X-API-Key": ""})
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"
    assert "/" not in (await client.get("/openapi.json")).json()["paths"]
