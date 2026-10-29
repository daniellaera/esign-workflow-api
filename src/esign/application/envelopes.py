"""Use cases for the API-key-protected management side."""

import json
from dataclasses import dataclass

from esign.application.audit import record_event
from esign.application.documents import load_pdf
from esign.application.invitations import Invitations
from esign.application.ports import (
    BlobStorage,
    Clock,
    Database,
    IdempotencyRecord,
    OutboxMessage,
    SignatureCheck,
    SignatureService,
    SignerIdentity,
)
from esign.domain.audit import AuditEvent, AuditEventType, ChainVerification, verify_chain
from esign.domain.common import sha256_hex
from esign.domain.envelopes import Envelope, SignerStatus
from esign.domain.errors import IdempotencyKeyReusedError, NotFoundError, ValidationError
from esign.domain.webhooks import WebhookDelivery

API_ACTOR = "api"


@dataclass(frozen=True)
class NewEnvelope:
    title: str
    document_id: str
    signers: tuple[SignerIdentity, ...]

    def fingerprint(self) -> str:
        """Stable hash of the request, to detect an Idempotency-Key reused for a different body."""
        canonical = json.dumps(
            {
                "title": self.title,
                "document_id": self.document_id,
                "signers": [[s.name, s.email] for s in self.signers],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256_hex(canonical.encode())


@dataclass(frozen=True)
class CreatedEnvelope:
    envelope: Envelope
    replayed: bool  # True when an earlier request with the same Idempotency-Key created it


@dataclass(frozen=True)
class VerificationReport:
    envelope: Envelope
    document_sha256: str  # hash of the bytes we just verified
    signatures: list[SignatureCheck]

    @property
    def hash_matches_record(self) -> bool:
        return self.document_sha256 == self.envelope.current_sha256


@dataclass(frozen=True)
class CurrentSigningLink:
    envelope: Envelope
    signer_index: int
    message: OutboxMessage


@dataclass(frozen=True)
class AuditTrail:
    events: list[AuditEvent]
    verification: ChainVerification


class EnvelopeService:
    def __init__(
        self,
        db: Database,
        storage: BlobStorage,
        signatures: SignatureService,
        invitations: Invitations,
        *,
        max_signers: int,
        clock: Clock,
    ) -> None:
        self._db = db
        self._storage = storage
        self._signatures = signatures
        self._invitations = invitations
        self._max_signers = max_signers
        self._clock = clock

    async def create(self, request: NewEnvelope, idempotency_key: str | None) -> CreatedEnvelope:
        if len(request.signers) > self._max_signers:
            raise ValidationError(f"an envelope can have at most {self._max_signers} signers")
        fingerprint = request.fingerprint()
        if idempotency_key is not None:
            replay = await self._replay(idempotency_key, fingerprint)
            if replay is not None:
                return CreatedEnvelope(replay, replayed=True)

        try:
            document = await self._db.documents.get(request.document_id)
        except NotFoundError as exc:
            raise ValidationError(f"document '{request.document_id}' does not exist") from exc

        now = self._clock()
        envelope = Envelope.create(
            title=request.title,
            document_id=document.id,
            document_sha256=document.sha256,
            signers=[(s.name, s.email) for s in request.signers],
            now=now,
        )
        async with self._db.transaction() as tx:
            if idempotency_key is not None:
                # Inserted first: a concurrent request with the same key fails here.
                await tx.idempotency.add(
                    IdempotencyRecord(idempotency_key, fingerprint, envelope.id, now)
                )
            await tx.envelopes.add(envelope)
            await record_event(
                tx.audit,
                envelope_id=envelope.id,
                event_type=AuditEventType.ENVELOPE_CREATED,
                actor=API_ACTOR,
                now=now,
                data={"document_sha256": document.sha256, "signer_count": len(envelope.signers)},
            )
        return CreatedEnvelope(envelope, replayed=False)

    async def _replay(self, key: str, fingerprint: str) -> Envelope | None:
        record = await self._db.idempotency.get(key)
        if record is None:
            return None
        if record.request_fingerprint != fingerprint:
            raise IdempotencyKeyReusedError()
        return await self._db.envelopes.get(record.envelope_id)

    async def get(self, envelope_id: str) -> Envelope:
        return await self._db.envelopes.get(envelope_id)

    async def send(self, envelope_id: str) -> Envelope:
        envelope = await self._db.envelopes.get(envelope_id)
        now = self._clock()
        envelope.send(now)
        async with self._db.transaction() as tx:
            await record_event(
                tx.audit,
                envelope_id=envelope.id,
                event_type=AuditEventType.ENVELOPE_SENT,
                actor=API_ACTOR,
                now=now,
            )
            link = await self._invitations.invite_current_signer(tx, envelope, now)
            await tx.envelopes.update(envelope)
        self._invitations.announce(link)
        return envelope

    async def current_document(self, envelope_id: str) -> tuple[Envelope, bytes]:
        envelope = await self._db.envelopes.get(envelope_id)
        return envelope, await load_pdf(self._storage, envelope.current_sha256)

    async def verify(self, envelope_id: str) -> VerificationReport:
        envelope, pdf = await self.current_document(envelope_id)
        return VerificationReport(
            envelope=envelope,
            document_sha256=sha256_hex(pdf),
            signatures=await self._signatures.verify(pdf),
        )

    async def audit_trail(self, envelope_id: str) -> AuditTrail:
        await self._db.envelopes.get(envelope_id)  # 404 for unknown envelopes
        events = await self._db.audit.list_for_envelope(envelope_id)
        return AuditTrail(events=events, verification=verify_chain(events))

    async def webhook_deliveries(self, envelope_id: str) -> list[WebhookDelivery]:
        await self._db.envelopes.get(envelope_id)
        return await self._db.webhooks.list_for_envelope(envelope_id)

    async def current_signing_link(self, envelope_id: str) -> CurrentSigningLink:
        """The link of the signer whose turn it is, read back from the outbox.

        Demo mode only: in production the link exists only in the signer's email.
        """
        envelope = await self._db.envelopes.get(envelope_id)
        index = envelope.current_signer_index
        if index is None or envelope.signers[index].status is not SignerStatus.INVITED:
            raise NotFoundError("open signing link for envelope", envelope_id)
        signer = envelope.signers[index]
        messages = await self._db.outbox.list_for_envelope(envelope_id)
        for message in reversed(messages):
            if message.recipient_email == signer.email:
                return CurrentSigningLink(envelope, index, message)
        raise NotFoundError("open signing link for envelope", envelope_id)

    async def outbox(self, envelope_id: str) -> list[OutboxMessage]:
        await self._db.envelopes.get(envelope_id)
        return await self._db.outbox.list_for_envelope(envelope_id)
