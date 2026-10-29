"""Issuing signing links to the signer whose turn it is."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from esign.application.audit import record_event
from esign.application.ports import OutboxMessage, Repositories
from esign.domain.audit import AuditEventType
from esign.domain.common import new_id
from esign.domain.envelopes import Envelope
from esign.domain.tokens import SigningToken

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IssuedLink:
    envelope_id: str
    signer_index: int
    url: str


class Invitations:
    def __init__(self, *, public_base_url: str, link_ttl: timedelta, log_links: bool) -> None:
        self._public_base_url = public_base_url.rstrip("/")
        self._link_ttl = link_ttl
        self._log_links = log_links

    async def invite_current_signer(
        self, tx: Repositories, envelope: Envelope, now: datetime
    ) -> IssuedLink:
        """Give the current signer a fresh link and queue the 'email' in the outbox.

        Mutates ``envelope``; the caller saves it in the same transaction.
        """
        index = envelope.current_signer_index
        if index is None:
            raise RuntimeError("invite_current_signer called with nobody left to sign")
        token = SigningToken.generate()
        expires_at = now + self._link_ttl
        signer = envelope.invite_current_signer(token.hash, expires_at)
        url = f"{self._public_base_url}/sign/{token.value}"

        await tx.outbox.add(
            OutboxMessage(
                id=new_id(),
                envelope_id=envelope.id,
                recipient_email=signer.email,
                subject=f"Please sign: {envelope.title}",
                signing_link=url,
                created_at=now,
            )
        )
        await record_event(
            tx.audit,
            envelope_id=envelope.id,
            event_type=AuditEventType.SIGNER_INVITED,
            actor="system",
            now=now,
            data={"signer_index": index, "link_expires_at": expires_at.isoformat()},
        )
        return IssuedLink(envelope_id=envelope.id, signer_index=index, url=url)

    def announce(self, link: IssuedLink) -> None:
        """Log the issued link. Call only after the transaction has committed.

        The link is a bearer credential, so it is logged only when explicitly
        enabled (local development). Signer names and emails are never logged.
        """
        fields: dict[str, object] = {
            "envelope_id": link.envelope_id,
            "signer_index": link.signer_index,
        }
        if self._log_links:
            fields["signing_link"] = link.url
        logger.info("signing link issued", extra=fields)
