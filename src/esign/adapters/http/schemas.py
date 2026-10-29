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

# Values shown in the OpenAPI docs (/docs).
EXAMPLE_ID = "6f1c2a7e9b0d4c3fa5e8b2d1c0f9e8a7"
EXAMPLE_SHA256 = "8ffa7858c44c25c3482b727bf2338c5ccdd521442c705310bf26730c3cd18c7d"
EXAMPLE_TIME = "2026-10-29T13:22:46.123Z"
SHA256_HEX = "SHA-256, lowercase hex"


class _Response(BaseModel):
    model_config = ConfigDict(frozen=True)


# --- Documents ------------------------------------------------------------------


class DocumentOut(_Response):
    id: str = Field(examples=[EXAMPLE_ID])
    filename: str = Field(examples=["contract.pdf"])
    size_bytes: int = Field(examples=[1024])
    sha256: str = Field(description=SHA256_HEX, examples=[EXAMPLE_SHA256])
    uploaded_at: datetime = Field(examples=[EXAMPLE_TIME])

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
    name: str = Field(min_length=1, max_length=64, examples=["Alice Example"])
    email: SignerEmail = Field(examples=["alice@example.com"])


class EnvelopeIn(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Service agreement",
                    "document_id": EXAMPLE_ID,
                    "signers": [
                        {"name": "Alice Example", "email": "alice@example.com"},
                        {"name": "Bob Example", "email": "bob@example.com"},
                    ],
                }
            ]
        }
    )

    title: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=64, description="Id from POST /documents")
    signers: list[SignerIn] = Field(
        min_length=1, max_length=10, description="In signing order: the first signs first"
    )


class SignerOut(_Response):
    order: int = Field(description="1-based position in the signing order", examples=[1])
    name: str = Field(examples=["Alice Example"])
    email: str = Field(examples=["alice@example.com"])
    status: SignerStatus
    link_expires_at: datetime | None = Field(
        description="Set once this signer has been invited", examples=[EXAMPLE_TIME]
    )
    signed_at: datetime | None
    declined_at: datetime | None
    decline_reason: str | None


class EnvelopeDocumentOut(_Response):
    id: str = Field(examples=[EXAMPLE_ID])
    original_sha256: str = Field(description="Hash of the uploaded PDF", examples=[EXAMPLE_SHA256])
    current_sha256: str = Field(
        description="Hash of the latest revision (changes with every signature)",
        examples=[EXAMPLE_SHA256],
    )


class EnvelopeOut(_Response):
    id: str = Field(examples=[EXAMPLE_ID])
    title: str = Field(examples=["Service agreement"])
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
    field_name: str = Field(examples=["Signature1"])
    signer_name: str | None = Field(
        description="From the certificate subject", examples=["Alice Example"]
    )
    signer_email: str | None = Field(examples=["alice@example.com"])
    signing_time: datetime | None = Field(
        description="As claimed by the signer", examples=[EXAMPLE_TIME]
    )
    intact: bool = Field(description="The signed bytes are unchanged")
    valid: bool = Field(description="The cryptographic signature verifies")
    chain_valid: bool = Field(description="The certificate chains up to the test CA")
    modification_level: str = Field(
        description="What changed after this signature, per pyHanko", examples=["FORM_FILLING"]
    )

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
    envelope_id: str = Field(examples=[EXAMPLE_ID])
    status: EnvelopeStatus
    document_sha256: str = Field(
        description="Hash of the PDF that was just verified", examples=[EXAMPLE_SHA256]
    )
    document_sha256_matches_record: bool = Field(
        description="That hash equals the one stored for the envelope"
    )
    all_signatures_valid: bool = Field(description="At least one signature, and all pass")
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
    sequence: int = Field(examples=[1])
    type: str = Field(examples=["signer.signed"])
    occurred_at: datetime = Field(examples=[EXAMPLE_TIME])
    actor: str = Field(examples=["signer:alice@example.com"])
    data: dict[str, AuditValue] = Field(examples=[{"signer_index": 0, "ip": "203.0.113.7"}])
    previous_hash: str = Field(description="Hash of the previous event (64 zeros for the first)")
    hash: str = Field(description="SHA-256 over this event's canonical JSON")


class AuditTrailOut(_Response):
    envelope_id: str = Field(examples=[EXAMPLE_ID])
    chain_valid: bool = Field(description="Every hash recomputes and links to its predecessor")
    broken_at_sequence: int | None = Field(description="First event that fails, if any")
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
    reason: str | None = Field(
        default=None, max_length=500, examples=["The amount in section 2 is wrong."]
    )


class SigningSignerOut(_Response):
    order: int
    name: str
    status: SignerStatus


class SigningViewOut(_Response):
    """What a signer sees. Other signers' email addresses are not disclosed."""

    envelope_id: str = Field(examples=[EXAMPLE_ID])
    title: str = Field(examples=["Service agreement"])
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
