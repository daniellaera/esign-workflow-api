"""MongoDB repositories.

Mapping between dataclasses and BSON documents is written out by hand. It is a
little repetitive, but it keeps the stored schema explicit and the domain free
of persistence concerns.

Every repository takes an optional session. With a session, its operations
join that session's transaction (see ``database.py``).
"""

from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError

from esign.application.ports import IdempotencyRecord, OutboxMessage
from esign.domain.audit import AuditEvent, AuditEventType
from esign.domain.documents import Document
from esign.domain.envelopes import Envelope, EnvelopeStatus, Signer, SignerStatus
from esign.domain.errors import ConcurrencyError, NotFoundError
from esign.domain.webhooks import DeliveryAttempt, DeliveryStatus, WebhookDelivery

type MongoDocument = dict[str, Any]
type Collection = AsyncCollection[MongoDocument]


class _Repository:
    def __init__(self, collection: Collection, session: AsyncClientSession | None) -> None:
        self._collection = collection
        self._session = session


# --- Documents ---------------------------------------------------------------


class MongoDocumentRepository(_Repository):
    async def add(self, document: Document) -> None:
        await self._collection.insert_one(
            {
                "_id": document.id,
                "filename": document.filename,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
                "uploaded_at": document.uploaded_at,
            },
            session=self._session,
        )

    async def get(self, document_id: str) -> Document:
        raw = await self._collection.find_one({"_id": document_id}, session=self._session)
        if raw is None:
            raise NotFoundError("document", document_id)
        return Document(
            id=raw["_id"],
            filename=raw["filename"],
            size_bytes=raw["size_bytes"],
            sha256=raw["sha256"],
            uploaded_at=raw["uploaded_at"],
        )


# --- Envelopes ---------------------------------------------------------------


def _envelope_to_document(envelope: Envelope) -> MongoDocument:
    return {
        "_id": envelope.id,
        "title": envelope.title,
        "document_id": envelope.document_id,
        "original_sha256": envelope.original_sha256,
        "current_sha256": envelope.current_sha256,
        "status": envelope.status.value,
        "signers": [
            {
                "name": s.name,
                "email": s.email,
                "status": s.status.value,
                "token_hash": s.token_hash,
                "link_expires_at": s.link_expires_at,
                "signed_at": s.signed_at,
                "declined_at": s.declined_at,
                "decline_reason": s.decline_reason,
            }
            for s in envelope.signers
        ],
        "created_at": envelope.created_at,
        "updated_at": envelope.updated_at,
        "version": envelope.version,
    }


def _envelope_from_document(raw: MongoDocument) -> Envelope:
    return Envelope(
        id=raw["_id"],
        title=raw["title"],
        document_id=raw["document_id"],
        original_sha256=raw["original_sha256"],
        current_sha256=raw["current_sha256"],
        status=EnvelopeStatus(raw["status"]),
        signers=[
            Signer(
                name=s["name"],
                email=s["email"],
                status=SignerStatus(s["status"]),
                token_hash=s["token_hash"],
                link_expires_at=s["link_expires_at"],
                signed_at=s["signed_at"],
                declined_at=s["declined_at"],
                decline_reason=s["decline_reason"],
            )
            for s in raw["signers"]
        ],
        created_at=raw["created_at"],
        updated_at=raw["updated_at"],
        version=raw["version"],
    )


class MongoEnvelopeRepository(_Repository):
    async def add(self, envelope: Envelope) -> None:
        await self._collection.insert_one(_envelope_to_document(envelope), session=self._session)

    async def get(self, envelope_id: str) -> Envelope:
        raw = await self._collection.find_one({"_id": envelope_id}, session=self._session)
        if raw is None:
            raise NotFoundError("envelope", envelope_id)
        return _envelope_from_document(raw)

    async def find_by_token_hash(self, token_hash: str) -> Envelope | None:
        raw = await self._collection.find_one(
            {"signers.token_hash": token_hash}, session=self._session
        )
        return None if raw is None else _envelope_from_document(raw)

    async def update(self, envelope: Envelope) -> None:
        """Optimistic locking: only overwrite the version we originally read."""
        replacement = _envelope_to_document(envelope)
        replacement["version"] = envelope.version + 1
        result = await self._collection.replace_one(
            {"_id": envelope.id, "version": envelope.version},
            replacement,
            session=self._session,
        )
        if result.matched_count == 0:
            raise ConcurrencyError(f"envelope '{envelope.id}' was modified concurrently")
        envelope.version += 1


# --- Audit trail ---------------------------------------------------------------


def _event_from_document(raw: MongoDocument) -> AuditEvent:
    return AuditEvent(
        envelope_id=raw["envelope_id"],
        sequence=raw["sequence"],
        type=AuditEventType(raw["type"]),
        occurred_at=raw["occurred_at"],
        actor=raw["actor"],
        data=raw["data"],
        previous_hash=raw["previous_hash"],
        hash=raw["hash"],
    )


class MongoAuditRepository(_Repository):
    """Append-only: there is deliberately no update or delete method."""

    async def last(self, envelope_id: str) -> AuditEvent | None:
        raw = await self._collection.find_one(
            {"envelope_id": envelope_id},
            sort=[("sequence", DESCENDING)],
            session=self._session,
        )
        return None if raw is None else _event_from_document(raw)

    async def append(self, event: AuditEvent) -> None:
        try:
            await self._collection.insert_one(
                {
                    "envelope_id": event.envelope_id,
                    "sequence": event.sequence,
                    "type": event.type.value,
                    "occurred_at": event.occurred_at,
                    "actor": event.actor,
                    "data": dict(event.data),
                    "previous_hash": event.previous_hash,
                    "hash": event.hash,
                },
                session=self._session,
            )
        except DuplicateKeyError as exc:
            raise ConcurrencyError("audit chain was extended concurrently") from exc

    async def list_for_envelope(self, envelope_id: str) -> list[AuditEvent]:
        cursor = self._collection.find(
            {"envelope_id": envelope_id},
            sort=[("sequence", ASCENDING)],
            session=self._session,
        )
        return [_event_from_document(raw) async for raw in cursor]


# --- Outbox ---------------------------------------------------------------------


class MongoOutboxRepository(_Repository):
    async def add(self, message: OutboxMessage) -> None:
        await self._collection.insert_one(
            {
                "_id": message.id,
                "envelope_id": message.envelope_id,
                "recipient_email": message.recipient_email,
                "subject": message.subject,
                "signing_link": message.signing_link,
                "created_at": message.created_at,
            },
            session=self._session,
        )

    async def list_for_envelope(self, envelope_id: str) -> list[OutboxMessage]:
        cursor = self._collection.find(
            {"envelope_id": envelope_id},
            sort=[("created_at", ASCENDING)],
            session=self._session,
        )
        return [
            OutboxMessage(
                id=raw["_id"],
                envelope_id=raw["envelope_id"],
                recipient_email=raw["recipient_email"],
                subject=raw["subject"],
                signing_link=raw["signing_link"],
                created_at=raw["created_at"],
            )
            async for raw in cursor
        ]


# --- Webhook deliveries ---------------------------------------------------------


def _attempt_to_document(attempt: DeliveryAttempt) -> MongoDocument:
    return {
        "number": attempt.number,
        "attempted_at": attempt.attempted_at,
        "status_code": attempt.status_code,
        "error": attempt.error,
    }


class MongoWebhookDeliveryRepository(_Repository):
    async def add(self, delivery: WebhookDelivery) -> None:
        await self._collection.insert_one(
            {
                "_id": delivery.id,
                "envelope_id": delivery.envelope_id,
                "event": delivery.event,
                "url": delivery.url,
                "payload": delivery.payload,
                "created_at": delivery.created_at,
                "status": delivery.status.value,
                "attempts": [_attempt_to_document(a) for a in delivery.attempts],
            },
            session=self._session,
        )

    async def record_attempt(
        self, delivery_id: str, attempt: DeliveryAttempt, status: DeliveryStatus
    ) -> None:
        await self._collection.update_one(
            {"_id": delivery_id},
            {
                "$push": {"attempts": _attempt_to_document(attempt)},
                "$set": {"status": status.value},
            },
            session=self._session,
        )

    async def list_for_envelope(self, envelope_id: str) -> list[WebhookDelivery]:
        cursor = self._collection.find(
            {"envelope_id": envelope_id},
            sort=[("created_at", ASCENDING)],
            session=self._session,
        )
        return [
            WebhookDelivery(
                id=raw["_id"],
                envelope_id=raw["envelope_id"],
                event=raw["event"],
                url=raw["url"],
                payload=raw["payload"],
                created_at=raw["created_at"],
                status=DeliveryStatus(raw["status"]),
                attempts=[DeliveryAttempt(**a) for a in raw["attempts"]],
            )
            async for raw in cursor
        ]


# --- Idempotency keys -----------------------------------------------------------


class MongoIdempotencyRepository(_Repository):
    async def get(self, key: str) -> IdempotencyRecord | None:
        raw = await self._collection.find_one({"_id": key}, session=self._session)
        if raw is None:
            return None
        return IdempotencyRecord(
            key=raw["_id"],
            request_fingerprint=raw["request_fingerprint"],
            envelope_id=raw["envelope_id"],
            created_at=raw["created_at"],
        )

    async def add(self, record: IdempotencyRecord) -> None:
        try:
            await self._collection.insert_one(
                {
                    "_id": record.key,
                    "request_fingerprint": record.request_fingerprint,
                    "envelope_id": record.envelope_id,
                    "created_at": record.created_at,
                },
                session=self._session,
            )
        except DuplicateKeyError as exc:
            raise ConcurrencyError(
                "a request with this Idempotency-Key is already being processed"
            ) from exc
