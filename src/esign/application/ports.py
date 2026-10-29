"""Interfaces the application layer needs from the outside world.

These are ``typing.Protocol`` classes: adapters satisfy them structurally,
without inheriting from them. Tests can pass any object with the same shape.
"""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from esign.domain.audit import AuditEvent
from esign.domain.documents import Document
from esign.domain.envelopes import Envelope
from esign.domain.webhooks import DeliveryAttempt, DeliveryStatus, WebhookDelivery

# --- Records that only exist to cross a port -------------------------------


@dataclass(frozen=True)
class OutboxMessage:
    """Stands in for an email to a signer. A real system would hand this to a mail provider."""

    id: str
    envelope_id: str
    recipient_email: str
    subject: str
    signing_link: str
    created_at: datetime


@dataclass(frozen=True)
class IdempotencyRecord:
    key: str
    request_fingerprint: str
    envelope_id: str
    created_at: datetime


@dataclass(frozen=True)
class SignerIdentity:
    name: str
    email: str


@dataclass(frozen=True)
class SignatureCheck:
    field_name: str
    signer_name: str | None
    signer_email: str | None
    signing_time: datetime | None
    intact: bool  # the signed byte ranges still hash to the signed digest
    valid: bool  # the cryptographic signature over that digest verifies
    chain_valid: bool  # the certificate chains up to our test CA
    modification_level: str  # what changed after this signature, per pyHanko


# --- Persistence -----------------------------------------------------------


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None: ...
    async def get(self, document_id: str) -> Document: ...


class EnvelopeRepository(Protocol):
    async def add(self, envelope: Envelope) -> None: ...
    async def get(self, envelope_id: str) -> Envelope: ...
    async def find_by_token_hash(self, token_hash: str) -> Envelope | None: ...

    async def update(self, envelope: Envelope) -> None:
        """Save with optimistic locking; raises ConcurrencyError on a stale version."""
        ...


class AuditRepository(Protocol):
    async def last(self, envelope_id: str) -> AuditEvent | None: ...
    async def append(self, event: AuditEvent) -> None: ...
    async def list_for_envelope(self, envelope_id: str) -> list[AuditEvent]: ...


class OutboxRepository(Protocol):
    async def add(self, message: OutboxMessage) -> None: ...
    async def list_for_envelope(self, envelope_id: str) -> list[OutboxMessage]: ...


class WebhookDeliveryRepository(Protocol):
    async def add(self, delivery: WebhookDelivery) -> None: ...

    async def record_attempt(
        self, delivery_id: str, attempt: DeliveryAttempt, status: DeliveryStatus
    ) -> None: ...

    async def list_for_envelope(self, envelope_id: str) -> list[WebhookDelivery]: ...


class IdempotencyRepository(Protocol):
    async def get(self, key: str) -> IdempotencyRecord | None: ...

    async def add(self, record: IdempotencyRecord) -> None:
        """Raises ConcurrencyError if the key already exists."""
        ...


class Repositories(Protocol):
    @property
    def documents(self) -> DocumentRepository: ...
    @property
    def envelopes(self) -> EnvelopeRepository: ...
    @property
    def audit(self) -> AuditRepository: ...
    @property
    def outbox(self) -> OutboxRepository: ...
    @property
    def webhooks(self) -> WebhookDeliveryRepository: ...
    @property
    def idempotency(self) -> IdempotencyRepository: ...


class Database(Repositories, Protocol):
    def transaction(self) -> AbstractAsyncContextManager[Repositories]:
        """Repositories whose writes commit together, or not at all."""
        ...

    async def ping(self) -> None: ...


# --- Other infrastructure ---------------------------------------------------


class BlobStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...


class SignatureService(Protocol):
    async def ensure_signable(self, pdf: bytes) -> None:
        """Raise InvalidDocumentError if the PDF cannot be parsed or signed."""
        ...

    async def sign(
        self, pdf: bytes, signer: SignerIdentity, *, field_name: str, reason: str
    ) -> bytes: ...

    async def verify(self, pdf: bytes) -> list[SignatureCheck]: ...

    def trust_anchor_pem(self) -> bytes: ...


class WebhookTransportError(Exception):
    """The webhook request did not produce an HTTP response (DNS, timeout, refused...)."""


class WebhookSender(Protocol):
    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        """Send the request and return the HTTP status code."""
        ...


type Clock = Callable[[], datetime]
