"""Composition root: the one place that knows every concrete class.

Built once at startup by the FastAPI lifespan and stored on ``app.state``.
"""

from dataclasses import dataclass
from typing import Self

import httpx
from pymongo import AsyncMongoClient

from esign.adapters.mongo.database import MongoDatabase, create_client, ensure_indexes
from esign.adapters.mongo.repositories import MongoDocument
from esign.adapters.signer.pyhanko_signer import PyHankoSignatureService
from esign.adapters.signer.test_ca import LocalTestCA
from esign.adapters.storage.local import LocalBlobStorage
from esign.adapters.webhook.httpx_sender import HttpxWebhookSender
from esign.application.documents import DocumentService
from esign.application.envelopes import EnvelopeService
from esign.application.invitations import Invitations
from esign.application.ports import Clock
from esign.application.signing import SigningService
from esign.application.webhooks import WebhookDispatcher, WebhookTarget
from esign.config import Settings

WEBHOOK_SHUTDOWN_GRACE_SECONDS = 10.0


@dataclass
class Container:
    settings: Settings
    mongo_client: AsyncMongoClient[MongoDocument]
    http_client: httpx.AsyncClient
    database: MongoDatabase
    signatures: PyHankoSignatureService
    documents: DocumentService
    envelopes: EnvelopeService
    signing: SigningService
    webhooks: WebhookDispatcher

    @classmethod
    async def start(cls, settings: Settings, clock: Clock) -> Self:
        mongo_client = create_client(settings.mongo_uri)
        mongo_db = mongo_client[settings.mongo_database]
        await ensure_indexes(mongo_db)
        database = MongoDatabase(mongo_client, mongo_db)

        storage = LocalBlobStorage(settings.data_dir / "documents")
        ca = LocalTestCA.load_or_create(settings.data_dir / "ca", clock())
        signatures = PyHankoSignatureService(
            ca, certificate_validity=settings.signer_certificate_validity, clock=clock
        )
        invitations = Invitations(
            public_base_url=settings.public_base_url,
            link_ttl=settings.signing_link_ttl,
            log_links=settings.log_signing_links,
        )

        http_client = httpx.AsyncClient(timeout=settings.webhook_timeout_seconds)
        target = None
        if settings.webhook_url is not None and settings.webhook_secret is not None:
            target = WebhookTarget(
                url=str(settings.webhook_url),
                secret=settings.webhook_secret.get_secret_value().encode(),
            )
        webhooks = WebhookDispatcher(
            database,
            HttpxWebhookSender(http_client),
            target=target,
            max_retries=settings.webhook_max_retries,
            backoff_seconds=settings.webhook_backoff_seconds,
            clock=clock,
        )

        return cls(
            settings=settings,
            mongo_client=mongo_client,
            http_client=http_client,
            database=database,
            signatures=signatures,
            documents=DocumentService(
                database,
                storage,
                signatures,
                max_upload_bytes=settings.max_upload_bytes,
                clock=clock,
            ),
            envelopes=EnvelopeService(database, storage, signatures, invitations, clock=clock),
            signing=SigningService(
                database, storage, signatures, invitations, webhooks, clock=clock
            ),
            webhooks=webhooks,
        )

    async def close(self) -> None:
        await self.webhooks.shutdown(WEBHOOK_SHUTDOWN_GRACE_SECONDS)
        await self.http_client.aclose()
        await self.mongo_client.close()
