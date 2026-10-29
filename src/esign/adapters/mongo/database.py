"""MongoDB client, indexes and transactions.

Transactions need a replica set. Locally and in tests we run a single-node
replica set, which costs nothing extra; see docs/adr/0002-mongodb-transactions.md.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pymongo import ASCENDING, AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from esign.adapters.mongo.repositories import (
    MongoAuditRepository,
    MongoDocument,
    MongoDocumentRepository,
    MongoEnvelopeRepository,
    MongoIdempotencyRepository,
    MongoOutboxRepository,
    MongoWebhookDeliveryRepository,
)
from esign.application.ports import Repositories
from esign.domain.errors import ConcurrencyError

DOCUMENTS = "documents"
ENVELOPES = "envelopes"
AUDIT_EVENTS = "audit_events"
OUTBOX = "outbox"
WEBHOOK_DELIVERIES = "webhook_deliveries"
IDEMPOTENCY_KEYS = "idempotency_keys"

IDEMPOTENCY_KEY_TTL_SECONDS = 24 * 3600


def create_client(uri: str) -> AsyncMongoClient[MongoDocument]:
    # tz_aware: return timezone-aware UTC datetimes instead of naive ones.
    return AsyncMongoClient(uri, tz_aware=True, uuidRepresentation="standard")


async def ensure_indexes(db: AsyncDatabase[MongoDocument]) -> None:
    """Idempotent; runs at startup. Creating them also creates the collections,
    which must exist before a transaction writes to them on older servers."""
    await db[ENVELOPES].create_index("signers.token_hash")
    await db[DOCUMENTS].create_index("sha256")
    # Unique: two writers can never append the same sequence number, so the chain never forks.
    await db[AUDIT_EVENTS].create_index(
        [("envelope_id", ASCENDING), ("sequence", ASCENDING)], unique=True
    )
    await db[OUTBOX].create_index([("envelope_id", ASCENDING), ("created_at", ASCENDING)])
    await db[WEBHOOK_DELIVERIES].create_index(
        [("envelope_id", ASCENDING), ("created_at", ASCENDING)]
    )
    # TTL index: MongoDB deletes idempotency keys a day after they were stored.
    await db[IDEMPOTENCY_KEYS].create_index(
        "created_at", expireAfterSeconds=IDEMPOTENCY_KEY_TTL_SECONDS
    )


class MongoRepositories:
    def __init__(
        self, db: AsyncDatabase[MongoDocument], session: AsyncClientSession | None = None
    ) -> None:
        self._documents = MongoDocumentRepository(db[DOCUMENTS], session)
        self._envelopes = MongoEnvelopeRepository(db[ENVELOPES], session)
        self._audit = MongoAuditRepository(db[AUDIT_EVENTS], session)
        self._outbox = MongoOutboxRepository(db[OUTBOX], session)
        self._webhooks = MongoWebhookDeliveryRepository(db[WEBHOOK_DELIVERIES], session)
        self._idempotency = MongoIdempotencyRepository(db[IDEMPOTENCY_KEYS], session)

    @property
    def documents(self) -> MongoDocumentRepository:
        return self._documents

    @property
    def envelopes(self) -> MongoEnvelopeRepository:
        return self._envelopes

    @property
    def audit(self) -> MongoAuditRepository:
        return self._audit

    @property
    def outbox(self) -> MongoOutboxRepository:
        return self._outbox

    @property
    def webhooks(self) -> MongoWebhookDeliveryRepository:
        return self._webhooks

    @property
    def idempotency(self) -> MongoIdempotencyRepository:
        return self._idempotency


class MongoDatabase(MongoRepositories):
    """Repositories without a session, plus a way to open a transaction."""

    def __init__(
        self, client: AsyncMongoClient[MongoDocument], db: AsyncDatabase[MongoDocument]
    ) -> None:
        super().__init__(db)
        self._client = client
        self._db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Repositories]:
        """Commit on normal exit, abort if the block raises."""
        try:
            async with (
                self._client.start_session() as session,
                await session.start_transaction(),
            ):
                yield MongoRepositories(self._db, session)
        except PyMongoError as exc:
            # Write conflicts with a concurrent transaction carry this label.
            if exc.has_error_label("TransientTransactionError"):
                raise ConcurrencyError("concurrent update, please retry") from exc
            raise

    async def ping(self) -> None:
        await self._db.command("ping")
