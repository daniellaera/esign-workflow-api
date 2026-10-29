"""Request and response bodies.

Kept separate from the domain dataclasses so the public API can evolve
independently of internal models, and so internals (token hashes, versions)
are never serialized by accident.
"""

from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from esign.application.envelopes import AuditTrail, VerificationReport
from esign.application.ports import OutboxMessage, SignatureCheck
from esign.application.signing import SigningSession
from esign.domain.audit import AuditValue
from esign.domain.documents import Document
from esign.domain.envelopes import Envelope, EnvelopeStatus, SignerStatus
from esign.domain.webhooks import DeliveryStatus, WebhookDelivery


def _ascii_only(value: str) -> str:
    # X.509 stores email addresses as IA5String, which is ASCII.
    if not value.isascii():
        raise ValueError("email address must be ASCII")
    return value


SignerEmail = Annotated[EmailStr, AfterValidator(_ascii_only)]


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- Documents ------------------------------------------------------------------


class DocumentOut(_Response):
    id: str
    filename: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime

    @classmethod
    def from_domain(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            filename=document.filename,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
            uploaded_at=document.uploaded_at,
        )


# --- Envelopes ------------------------------------------------------------------


class SignerIn(BaseModel):
    # 64 characters is the X.509 upper bound for a common name.
    name: str = Field(min_length=1, max_length=64)
    email: SignerEmail


class EnvelopeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=64)
    signers: list[SignerIn] = Field(min_length=1, max_length=10, description="Signing order")


class SignerOut(_Response):
    order: int
    name: str
    email: str
    status: SignerStatus
    link_expires_at: datetime | None
    signed_at: datetime | None
    declined_at: datetime | None
    decline_reason: str | None


class EnvelopeDocumentOut(_Response):
    id: str
    original_sha256: str
    current_sha256: str


class EnvelopeOut(_Response):
    id: str
    title: str
    status: EnvelopeStatus
    document: EnvelopeDocumentOut
    signers: list[SignerOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, envelope: Envelope) -> Self:
        return cls(
            id=envelope.id,
            title=envelope.title,
            status=envelope.status,
            document=EnvelopeDocumentOut(
                id=envelope.document_id,
                original_sha256=envelope.original_sha256,
                current_sha256=envelope.current_sha256,
            ),
            signers=[
                SignerOut(
                    order=index + 1,
                    name=s.name,
                    email=s.email,
                    status=s.status,
                    link_expires_at=s.link_expires_at,
                    signed_at=s.signed_at,
                    declined_at=s.declined_at,
                    decline_reason=s.decline_reason,
                )
                for index, s in enumerate(envelope.signers)
            ],
            created_at=envelope.created_at,
            updated_at=envelope.updated_at,
        )


# --- Verification and audit -----------------------------------------------------


class SignatureCheckOut(_Response):
    field_name: str
    signer_name: str | None
    signer_email: str | None
    signing_time: datetime | None
    intact: bool
    valid: bool
    chain_valid: bool
    modification_level: str

    @classmethod
    def from_check(cls, check: SignatureCheck) -> Self:
        return cls(
            field_name=check.field_name,
            signer_name=check.signer_name,
            signer_email=check.signer_email,
            signing_time=check.signing_time,
            intact=check.intact,
            valid=check.valid,
            chain_valid=check.chain_valid,
            modification_level=check.modification_level,
        )


class VerificationOut(_Response):
    envelope_id: str
    status: EnvelopeStatus
    document_sha256: str
    document_sha256_matches_record: bool
    all_signatures_valid: bool
    signatures: list[SignatureCheckOut]

    @classmethod
    def from_report(cls, report: VerificationReport) -> Self:
        checks = [SignatureCheckOut.from_check(c) for c in report.signatures]
        return cls(
            envelope_id=report.envelope.id,
            status=report.envelope.status,
            document_sha256=report.document_sha256,
            document_sha256_matches_record=report.hash_matches_record,
            all_signatures_valid=bool(checks)
            and all(c.intact and c.valid and c.chain_valid for c in checks),
            signatures=checks,
        )


class AuditEventOut(_Response):
    sequence: int
    type: str
    occurred_at: datetime
    actor: str
    data: dict[str, AuditValue]
    previous_hash: str
    hash: str


class AuditTrailOut(_Response):
    envelope_id: str
    chain_valid: bool
    broken_at_sequence: int | None
    events: list[AuditEventOut]

    @classmethod
    def from_trail(cls, envelope_id: str, trail: AuditTrail) -> Self:
        return cls(
            envelope_id=envelope_id,
            chain_valid=trail.verification.valid,
            broken_at_sequence=trail.verification.broken_at,
            events=[
                AuditEventOut(
                    sequence=e.sequence,
                    type=e.type.value,
                    occurred_at=e.occurred_at,
                    actor=e.actor,
                    data=dict(e.data),
                    previous_hash=e.previous_hash,
                    hash=e.hash,
                )
                for e in trail.events
            ],
        )


# --- Webhooks and outbox -----------------------------------------------------------


class DeliveryAttemptOut(_Response):
    number: int
    attempted_at: datetime
    status_code: int | None
    error: str | None


class WebhookDeliveryOut(_Response):
    id: str
    event: str
    url: str
    status: DeliveryStatus
    created_at: datetime
    attempts: list[DeliveryAttemptOut]

    @classmethod
    def from_domain(cls, delivery: WebhookDelivery) -> Self:
        return cls(
            id=delivery.id,
            event=delivery.event,
            url=delivery.url,
            status=delivery.status,
            created_at=delivery.created_at,
            attempts=[
                DeliveryAttemptOut(
                    number=a.number,
                    attempted_at=a.attempted_at,
                    status_code=a.status_code,
                    error=a.error,
                )
                for a in delivery.attempts
            ],
        )


class OutboxMessageOut(_Response):
    recipient_email: str
    subject: str
    signing_link: str
    created_at: datetime

    @classmethod
    def from_message(cls, message: OutboxMessage) -> Self:
        return cls(
            recipient_email=message.recipient_email,
            subject=message.subject,
            signing_link=message.signing_link,
            created_at=message.created_at,
        )


# --- Signer-facing ------------------------------------------------------------------


class DeclineIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class SigningSignerOut(_Response):
    order: int
    name: str
    status: SignerStatus


class SigningViewOut(_Response):
    """What a signer sees. Other signers' email addresses are not disclosed."""

    envelope_id: str
    title: str
    envelope_status: EnvelopeStatus
    document_sha256: str
    you: SignerOut
    signers: list[SigningSignerOut]

    @classmethod
    def from_session(cls, session: SigningSession) -> Self:
        envelope = session.envelope
        full = EnvelopeOut.from_domain(envelope)
        return cls(
            envelope_id=envelope.id,
            title=envelope.title,
            envelope_status=envelope.status,
            document_sha256=envelope.current_sha256,
            you=full.signers[session.signer_index],
            signers=[
                SigningSignerOut(order=s.order, name=s.name, status=s.status) for s in full.signers
            ],
        )
