"""Repositories against a real MongoDB replica set."""

import dataclasses
from datetime import timedelta

import pytest

from esign.adapters.mongo.database import AUDIT_EVENTS, MongoDatabase
from esign.application.ports import IdempotencyRecord, OutboxMessage
from esign.domain.audit import AuditEvent, AuditEventType, next_event, verify_chain
from esign.domain.common import new_id, utc_now
from esign.domain.documents import Document
from esign.domain.envelopes import Envelope
from esign.domain.errors import ConcurrencyError, NotFoundError
from esign.domain.webhooks import DeliveryAttempt, DeliveryStatus, WebhookDelivery

pytestmark = pytest.mark.integration


def make_envelope() -> Envelope:
    now = utc_now()
    envelope = Envelope.create(
        title="Contract",
        document_id="doc-1",
        document_sha256="a" * 64,
        signers=[("Alice", "alice@example.com"), ("Bob", "bob@example.com")],
        now=now,
    )
    envelope.send(now)
    envelope.invite_current_signer("token-hash-0", now + timedelta(hours=72))
    return envelope


def make_event(previous: AuditEvent | None, envelope_id: str) -> AuditEvent:
    return next_event(
        previous,
        envelope_id=envelope_id,
        event_type=AuditEventType.SIGNER_VIEWED,
        actor="signer:alice@example.com",
        data={"signer_index": 0, "ip": "203.0.113.7", "user_agent": "pytest"},
        occurred_at=utc_now(),
    )


async def test_document_round_trip(database: MongoDatabase) -> None:
    document = Document(new_id(), "c.pdf", 123, "b" * 64, utc_now())
    await database.documents.add(document)
    assert await database.documents.get(document.id) == document


async def test_unknown_document_raises_not_found(database: MongoDatabase) -> None:
    with pytest.raises(NotFoundError):
        await database.documents.get("missing")


async def test_envelope_round_trip_is_lossless(database: MongoDatabase) -> None:
    envelope = make_envelope()
    await database.envelopes.add(envelope)
    assert await database.envelopes.get(envelope.id) == envelope


async def test_find_by_token_hash(database: MongoDatabase) -> None:
    envelope = make_envelope()
    await database.envelopes.add(envelope)
    found = await database.envelopes.find_by_token_hash("token-hash-0")
    assert found is not None
    assert found.id == envelope.id
    assert await database.envelopes.find_by_token_hash("other") is None


async def test_update_uses_optimistic_locking(database: MongoDatabase) -> None:
    envelope = make_envelope()
    await database.envelopes.add(envelope)
    first = await database.envelopes.get(envelope.id)
    second = await database.envelopes.get(envelope.id)

    first.title = "first writer wins"
    await database.envelopes.update(first)
    assert first.version == 1

    second.title = "stale"
    with pytest.raises(ConcurrencyError):
        await database.envelopes.update(second)
    assert (await database.envelopes.get(envelope.id)).title == "first writer wins"


async def test_transaction_commits_all_writes(database: MongoDatabase) -> None:
    envelope = make_envelope()
    async with database.transaction() as tx:
        await tx.envelopes.add(envelope)
        await tx.audit.append(make_event(None, envelope.id))
    assert await database.envelopes.get(envelope.id) == envelope
    assert len(await database.audit.list_for_envelope(envelope.id)) == 1


async def test_transaction_rolls_back_on_error(database: MongoDatabase) -> None:
    envelope = make_envelope()

    async def fail_half_way() -> None:
        async with database.transaction() as tx:
            await tx.envelopes.add(envelope)
            await tx.audit.append(make_event(None, envelope.id))
            raise RuntimeError("something failed mid-way")

    with pytest.raises(RuntimeError):
        await fail_half_way()
    with pytest.raises(NotFoundError):
        await database.envelopes.get(envelope.id)
    assert await database.audit.list_for_envelope(envelope.id) == []


async def test_audit_chain_survives_a_round_trip(database: MongoDatabase) -> None:
    """Datetimes come back from Mongo with millisecond precision; hashes must still match."""
    previous = None
    for _ in range(3):
        previous = make_event(previous, "env-1")
        await database.audit.append(previous)
    events = await database.audit.list_for_envelope("env-1")
    assert [e.sequence for e in events] == [1, 2, 3]
    assert verify_chain(events).valid
    assert (await database.audit.last("env-1")) == events[-1]


async def test_audit_rejects_a_forked_chain(database: MongoDatabase) -> None:
    first = make_event(None, "env-1")
    await database.audit.append(first)
    with pytest.raises(ConcurrencyError):
        await database.audit.append(dataclasses.replace(first, actor="someone else"))


async def test_concurrent_transactions_conflict(database: MongoDatabase) -> None:
    event = make_event(None, "env-1")
    async with database.transaction() as tx1:
        await tx1.audit.append(event)
        with pytest.raises(ConcurrencyError):
            async with database.transaction() as tx2:
                await tx2.audit.append(event)


async def test_tampering_in_the_database_is_detected(database: MongoDatabase) -> None:
    previous = None
    for _ in range(3):
        previous = make_event(previous, "env-1")
        await database.audit.append(previous)

    raw_collection = database._db[AUDIT_EVENTS]
    await raw_collection.update_one(
        {"envelope_id": "env-1", "sequence": 2}, {"$set": {"data.ip": "198.51.100.1"}}
    )
    result = verify_chain(await database.audit.list_for_envelope("env-1"))
    assert not result.valid
    assert result.broken_at == 2


async def test_idempotency_key_is_unique(database: MongoDatabase) -> None:
    record = IdempotencyRecord("key-1", "fingerprint", "env-1", utc_now())
    await database.idempotency.add(record)
    assert await database.idempotency.get("key-1") == record
    assert await database.idempotency.get("key-2") is None
    with pytest.raises(ConcurrencyError):
        await database.idempotency.add(record)


async def test_outbox_round_trip(database: MongoDatabase) -> None:
    message = OutboxMessage(
        new_id(), "env-1", "alice@example.com", "Please sign", "http://x/sign/t", utc_now()
    )
    await database.outbox.add(message)
    assert await database.outbox.list_for_envelope("env-1") == [message]


async def test_webhook_attempts_are_appended(database: MongoDatabase) -> None:
    delivery = WebhookDelivery(
        id=new_id(),
        envelope_id="env-1",
        event="envelope.completed",
        url="https://hooks.test",
        payload={"event": "envelope.completed"},
        created_at=utc_now(),
    )
    await database.webhooks.add(delivery)
    failed = DeliveryAttempt(1, utc_now(), 500, "receiver answered HTTP 500")
    ok = DeliveryAttempt(2, utc_now(), 200, None)
    await database.webhooks.record_attempt(delivery.id, failed, DeliveryStatus.PENDING)
    await database.webhooks.record_attempt(delivery.id, ok, DeliveryStatus.DELIVERED)

    (stored,) = await database.webhooks.list_for_envelope("env-1")
    assert stored.status is DeliveryStatus.DELIVERED
    assert stored.attempts == [failed, ok]


async def test_ping(database: MongoDatabase) -> None:
    await database.ping()
