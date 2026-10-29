"""The envelope aggregate: a document plus an ordered list of signers.

Signers sign sequentially. Only the signer whose turn it is holds a signing
link; the next link is issued when the previous signer has signed.

State machine::

    DRAFT -> SENT -> PARTIALLY_SIGNED -> COMPLETED
               |            |
               +------------+--> DECLINED | EXPIRED

Every status change goes through ``_transition_to``, which consults the
``ALLOWED_TRANSITIONS`` table. That table is the single source of truth.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from esign.domain.common import new_id
from esign.domain.errors import (
    InvalidTransitionError,
    SigningLinkExpiredError,
    SigningLinkInactiveError,
    SigningLinkNotFoundError,
    ValidationError,
)


class EnvelopeStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    PARTIALLY_SIGNED = "PARTIALLY_SIGNED"
    COMPLETED = "COMPLETED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"


# PARTIALLY_SIGNED -> PARTIALLY_SIGNED happens with three or more signers.
ALLOWED_TRANSITIONS: Final[dict[EnvelopeStatus, frozenset[EnvelopeStatus]]] = {
    EnvelopeStatus.DRAFT: frozenset({EnvelopeStatus.SENT}),
    EnvelopeStatus.SENT: frozenset(
        {
            EnvelopeStatus.PARTIALLY_SIGNED,
            EnvelopeStatus.COMPLETED,
            EnvelopeStatus.DECLINED,
            EnvelopeStatus.EXPIRED,
        }
    ),
    EnvelopeStatus.PARTIALLY_SIGNED: frozenset(
        {
            EnvelopeStatus.PARTIALLY_SIGNED,
            EnvelopeStatus.COMPLETED,
            EnvelopeStatus.DECLINED,
            EnvelopeStatus.EXPIRED,
        }
    ),
    EnvelopeStatus.COMPLETED: frozenset(),
    EnvelopeStatus.DECLINED: frozenset(),
    EnvelopeStatus.EXPIRED: frozenset(),
}

OPEN_FOR_SIGNING: Final = frozenset({EnvelopeStatus.SENT, EnvelopeStatus.PARTIALLY_SIGNED})


class SignerStatus(StrEnum):
    WAITING = "WAITING"  # not their turn yet
    INVITED = "INVITED"  # holds a valid signing link
    SIGNED = "SIGNED"
    DECLINED = "DECLINED"


@dataclass
class Signer:
    name: str
    email: str
    status: SignerStatus = SignerStatus.WAITING
    token_hash: str | None = None
    link_expires_at: datetime | None = None
    signed_at: datetime | None = None
    declined_at: datetime | None = None
    decline_reason: str | None = None


@dataclass
class Envelope:
    id: str
    title: str
    document_id: str
    original_sha256: str
    current_sha256: str
    signers: list[Signer]
    created_at: datetime
    updated_at: datetime
    status: EnvelopeStatus = EnvelopeStatus.DRAFT
    # Optimistic-locking counter, incremented by the repository on every save.
    version: int = field(default=0)

    @classmethod
    def create(
        cls,
        *,
        title: str,
        document_id: str,
        document_sha256: str,
        signers: list[tuple[str, str]],
        now: datetime,
    ) -> "Envelope":
        if not signers:
            raise ValidationError("an envelope needs at least one signer")
        emails = [email.casefold() for _, email in signers]
        if len(set(emails)) != len(emails):
            raise ValidationError("each signer must have a distinct email address")
        return cls(
            id=new_id(),
            title=title,
            document_id=document_id,
            original_sha256=document_sha256,
            current_sha256=document_sha256,
            signers=[Signer(name=name, email=email) for name, email in signers],
            created_at=now,
            updated_at=now,
        )

    @property
    def current_signer_index(self) -> int | None:
        """Index of the signer whose turn it is, or None if nobody can sign."""
        if self.status not in OPEN_FOR_SIGNING:
            return None
        for index, signer in enumerate(self.signers):
            if signer.status in (SignerStatus.WAITING, SignerStatus.INVITED):
                return index
        return None

    def send(self, now: datetime) -> None:
        self._transition_to(EnvelopeStatus.SENT, now)

    def invite_current_signer(self, token_hash: str, expires_at: datetime) -> Signer:
        index = self.current_signer_index
        if index is None or self.signers[index].status is not SignerStatus.WAITING:
            raise ValidationError("there is no signer waiting for an invitation")
        signer = self.signers[index]
        signer.status = SignerStatus.INVITED
        signer.token_hash = token_hash
        signer.link_expires_at = expires_at
        return signer

    def signer_index_for_token(self, token_hash: str) -> int:
        for index, signer in enumerate(self.signers):
            if signer.token_hash == token_hash:
                return index
        raise SigningLinkNotFoundError()

    def authorize_link(self, token_hash: str, now: datetime) -> int:
        """Return the signer index if the link may be used right now.

        The order of checks matters: a link for a closed envelope is reported
        as inactive even if it has also expired.
        """
        index = self.signer_index_for_token(token_hash)
        signer = self.signers[index]
        if self.status not in OPEN_FOR_SIGNING or signer.status is not SignerStatus.INVITED:
            raise SigningLinkInactiveError(
                f"signing link is no longer active (envelope {self.status}, signer {signer.status})"
            )
        if signer.link_expires_at is None or now >= signer.link_expires_at:
            raise SigningLinkExpiredError()
        return index

    def record_signature(self, token_hash: str, signed_sha256: str, now: datetime) -> Signer:
        index = self.authorize_link(token_hash, now)
        signer = self.signers[index]
        signer.status = SignerStatus.SIGNED
        signer.signed_at = now
        self.current_sha256 = signed_sha256
        if all(s.status is SignerStatus.SIGNED for s in self.signers):
            self._transition_to(EnvelopeStatus.COMPLETED, now)
        else:
            self._transition_to(EnvelopeStatus.PARTIALLY_SIGNED, now)
        return signer

    def decline(self, token_hash: str, reason: str | None, now: datetime) -> Signer:
        index = self.authorize_link(token_hash, now)
        signer = self.signers[index]
        signer.status = SignerStatus.DECLINED
        signer.declined_at = now
        signer.decline_reason = reason
        self._transition_to(EnvelopeStatus.DECLINED, now)
        return signer

    def expire(self, now: datetime) -> None:
        self._transition_to(EnvelopeStatus.EXPIRED, now)

    def _transition_to(self, target: EnvelopeStatus, now: datetime) -> None:
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransitionError(self.status, target)
        self.status = target
        self.updated_at = now
