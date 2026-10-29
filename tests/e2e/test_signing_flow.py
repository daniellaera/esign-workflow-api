"""The whole happy path, end to end, against real MongoDB and real pyHanko."""

import hashlib
import json

import httpx
import pytest
import respx
from fastapi import FastAPI

from esign.domain.webhooks import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify_signature
from tests.e2e.conftest import ALICE, BOB, WEBHOOK_SECRET, Api

pytestmark = pytest.mark.integration


async def test_upload_two_signers_verify_audit_and_webhook(
    api: Api, app: FastAPI, webhook_receiver: respx.Route, sample_pdf: bytes
) -> None:
    http = api.http
    # The receiver fails once, so the retry path is part of the happy path test.
    webhook_receiver.side_effect = [httpx.Response(500), httpx.Response(200)]

    # 1. Upload
    document = await api.upload(sample_pdf)
    assert document["sha256"] == hashlib.sha256(sample_pdf).hexdigest()

    # 2. Create (DRAFT) and send (SENT): only Alice gets a link
    created = await api.create_envelope(document["id"], [ALICE, BOB])
    assert created.status_code == 201
    assert created.headers["Location"] == f"/envelopes/{created.json()['id']}"
    envelope_id = created.json()["id"]
    assert created.json()["status"] == "DRAFT"

    sent = (await http.post(f"/envelopes/{envelope_id}/send")).json()
    assert sent["status"] == "SENT"
    assert [s["status"] for s in sent["signers"]] == ["INVITED", "WAITING"]

    # 3. Alice opens her link, sees her position, but not Bob's email
    alice_token = await api.latest_token(envelope_id)
    view = await http.get(f"/sign/{alice_token}", headers={"X-API-Key": ""})
    assert view.status_code == 200
    assert view.headers["Cache-Control"] == "no-store"
    assert view.json()["you"]["order"] == 1
    assert "bob@example.com" not in view.text

    # 4. Alice signs; her link is now spent
    signed = await http.post(f"/sign/{alice_token}")
    assert signed.status_code == 200
    assert signed.json()["envelope_status"] == "PARTIALLY_SIGNED"
    reused = await http.post(f"/sign/{alice_token}")
    assert reused.status_code == 410
    assert reused.json()["type"] == "urn:esign:problem:signing-link-inactive"

    # 5. Bob gets the next link and signs
    bob_token = await api.latest_token(envelope_id)
    assert bob_token != alice_token
    assert (await http.post(f"/sign/{bob_token}")).json()["envelope_status"] == "COMPLETED"

    # 6. Both signatures verify
    verification = (await http.get(f"/envelopes/{envelope_id}/verify")).json()
    assert verification["all_signatures_valid"] is True
    assert verification["document_sha256_matches_record"] is True
    signatures = verification["signatures"]
    assert [s["signer_name"] for s in signatures] == ["Alice Example", "Bob Example"]
    assert all(s["intact"] and s["valid"] and s["chain_valid"] for s in signatures)

    # 7. The downloadable PDF is the original plus incremental updates
    pdf = await http.get(f"/envelopes/{envelope_id}/document")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(sample_pdf)
    assert hashlib.sha256(pdf.content).hexdigest() == verification["document_sha256"]

    # 8. The audit chain verifies and tells the story in order
    audit = (await http.get(f"/envelopes/{envelope_id}/audit")).json()
    assert audit["chain_valid"] is True
    assert [e["type"] for e in audit["events"]] == [
        "envelope.created",
        "envelope.sent",
        "signer.invited",
        "signer.viewed",
        "signer.signed",
        "signer.invited",
        "signer.signed",
        "envelope.completed",
    ]
    assert audit["events"][4]["data"]["document_sha256"] != document["sha256"]

    # 9. The webhook arrived (on the retry) with a valid HMAC signature
    await app.state.container.webhooks.drain()
    assert webhook_receiver.call_count == 2
    request = webhook_receiver.calls.last.request
    timestamp = int(request.headers[TIMESTAMP_HEADER])
    assert verify_signature(
        WEBHOOK_SECRET.encode(),
        timestamp,
        request.content,
        request.headers[SIGNATURE_HEADER],
        now=timestamp,
    )
    payload = json.loads(request.content)
    assert payload["event"] == "envelope.completed"
    assert payload["envelope_id"] == envelope_id

    deliveries = (await http.get(f"/envelopes/{envelope_id}/webhook-deliveries")).json()
    assert deliveries[0]["status"] == "DELIVERED"
    assert [a["status_code"] for a in deliveries[0]["attempts"]] == [500, 200]
