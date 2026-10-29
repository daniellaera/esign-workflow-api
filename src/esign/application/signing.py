"""Use cases for signers, who authenticate only by holding a signing link."""

import logging
from dataclasses import dataclass
from datetime import datetime

from esign.application.audit import record_event
from esign.application.documents import load_pdf
from esign.application.invitations import Invitations, IssuedLink
from esign.application.ports import (
    BlobStorage,
    Clock,
    Database,
    Repositories,
    SignatureService,
    SignerIdentity,
)
from esign.application.webhooks import WebhookDispatcher
from esign.domain.audit import AuditEventType, AuditValue
from esign.domain.common import sha256_hex
from esign.domain.envelopes import Envelope, EnvelopeStatus, Signer
from esign.domain.errors import ConcurrencyError, SigningLinkExpiredError, SigningLinkNotFoundError
from esign.domain.tokens import hash_token
from esign.domain.webhooks import WebhookDelivery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientInfo:
    """Recorded in the audit trail as evidence of who acted, from where."""

    ip: str | None
    user_agent: str | None


@dataclass(frozen=True)
class SigningSession:
    envelope: Envelope
    signer_index: int

    @property
    def signer(self) -> Signer:
        return self.envelope.signers[self.signer_index]


@dataclass(frozen=True)
class _AuthorizedLink:
    envelope: Envelope
    signer_index: int
    token_hash: str


class SigningService:
    def __init__(
        self,
        db: Database,
        storage: BlobStorage,
        signatures: SignatureService,
        invitations: Invitations,
        webhooks: WebhookDispatcher,
        *,
        clock: Clock,
    ) -> None:
        self._db = db
        self._storage = storage
        self._signatures = signatures
        self._invitations = invitations
        self._webhooks = webhooks
        self._clock = clock

    async def open(self, token: str, client: ClientInfo) -> SigningSession:
        now = self._clock()
        link = await self._authorize(token)
        signer = link.envelope.signers[link.signer_index]
        async with self._db.transaction() as tx:
            await record_event(
                tx.audit,
                envelope_id=link.envelope.id,
                event_type=AuditEventType.SIGNER_VIEWED,
                actor=_actor(signer),
                now=now,
                data=_client_data(link.signer_index, client),
            )
        return SigningSession(link.envelope, link.signer_index)

    async def document(self, token: str) -> bytes:
        link = await self._authorize(token)
        return await load_pdf(self._storage, link.envelope.current_sha256)

    async def sign(self, token: str, client: ClientInfo) -> SigningSession:
        link = await self._authorize(token)
        envelope = link.envelope
        signer = envelope.signers[link.signer_index]

        # Signing happens outside the transaction: it is slow-ish CPU work and
        # produces a new immutable blob, which is harmless if we later lose a race.
        pdf = await load_pdf(self._storage, envelope.current_sha256)
        signed_pdf = await self._signatures.sign(
            pdf,
            SignerIdentity(name=signer.name, email=signer.email),
            field_name=f"Signature{link.signer_index + 1}",
            reason=f"Signed via esign-workflow-api (envelope {envelope.id})",
        )
        signed_sha256 = sha256_hex(signed_pdf)
        await self._storage.put(signed_sha256, signed_pdf)

        now = self._clock()
        envelope.record_signature(link.token_hash, signed_sha256, now)
        issued: IssuedLink | None = None
        delivery: WebhookDelivery | None = None
        async with self._db.transaction() as tx:
            await record_event(
                tx.audit,
                envelope_id=envelope.id,
                event_type=AuditEventType.SIGNER_SIGNED,
                actor=_actor(signer),
                now=now,
                data={
                    **_client_data(link.signer_index, client),
                    "document_sha256": signed_sha256,
                },
            )
            if envelope.status is EnvelopeStatus.COMPLETED:
                delivery = await self._close(tx, envelope, AuditEventType.ENVELOPE_COMPLETED, now)
            else:
                issued = await self._invitations.invite_current_signer(tx, envelope, now)
            await tx.envelopes.update(envelope)

        if issued is not None:
            self._invitations.announce(issued)
        if delivery is not None:
            self._webhooks.dispatch(delivery)
        return SigningSession(envelope, link.signer_index)

    async def decline(self, token: str, reason: str | None, client: ClientInfo) -> SigningSession:
        link = await self._authorize(token)
        envelope = link.envelope
        signer = envelope.signers[link.signer_index]
        now = self._clock()
        envelope.decline(link.token_hash, reason, now)
        async with self._db.transaction() as tx:
            await record_event(
                tx.audit,
                envelope_id=envelope.id,
                event_type=AuditEventType.SIGNER_DECLINED,
                actor=_actor(signer),
                now=now,
                data={**_client_data(link.signer_index, client), "reason": reason},
            )
            delivery = await self._close(tx, envelope, AuditEventType.ENVELOPE_DECLINED, now)
            await tx.envelopes.update(envelope)
        if delivery is not None:
            self._webhooks.dispatch(delivery)
        return SigningSession(envelope, link.signer_index)

    async def _close(
        self,
        tx: Repositories,
        envelope: Envelope,
        event_type: AuditEventType,
        now: datetime,
    ) -> WebhookDelivery | None:
        """Record the terminal event and queue the webhook, inside the caller's transaction."""
        await record_event(
            tx.audit, envelope_id=envelope.id, event_type=event_type, actor="system", now=now
        )
        delivery = self._webhooks.new_delivery(envelope, event_type.value, now)
        if delivery is not None:
            await tx.webhooks.add(delivery)
        return delivery

    async def _authorize(self, token: str) -> _AuthorizedLink:
        token_hash = hash_token(token)
        envelope = await self._db.envelopes.find_by_token_hash(token_hash)
        if envelope is None:
            raise SigningLinkNotFoundError()
        now = self._clock()
        try:
            index = envelope.authorize_link(token_hash, now)
        except SigningLinkExpiredError:
            await self._expire(envelope)
            raise
        return _AuthorizedLink(envelope, index, token_hash)

    async def _expire(self, envelope: Envelope) -> None:
        """Expiry is applied lazily, the first time someone uses an expired link."""
        now = self._clock()
        envelope.expire(now)
        try:
            async with self._db.transaction() as tx:
                await record_event(
                    tx.audit,
                    envelope_id=envelope.id,
                    event_type=AuditEventType.ENVELOPE_EXPIRED,
                    actor="system",
                    now=now,
                )
                await tx.envelopes.update(envelope)
        except ConcurrencyError:
            # A parallel request expired (or otherwise changed) it first; nothing to do.
            logger.info(
                "envelope already changed while expiring", extra={"envelope_id": envelope.id}
            )


def _actor(signer: Signer) -> str:
    return f"signer:{signer.email}"


def _client_data(signer_index: int, client: ClientInfo) -> dict[str, AuditValue]:
    return {"signer_index": signer_index, "ip": client.ip, "user_agent": client.user_agent}
