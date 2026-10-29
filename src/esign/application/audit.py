"""Appending to an envelope's audit chain."""

from collections.abc import Mapping
from datetime import datetime

from esign.application.ports import AuditRepository
from esign.domain.audit import AuditEvent, AuditEventType, AuditValue, next_event


async def record_event(
    audit: AuditRepository,
    *,
    envelope_id: str,
    event_type: AuditEventType,
    actor: str,
    now: datetime,
    data: Mapping[str, AuditValue] | None = None,
) -> AuditEvent:
    """Link a new event to the latest one and store it.

    Call this inside a transaction. If two requests race, both compute the same
    next sequence number; the unique (envelope_id, sequence) index rejects the
    second insert, so the chain can never fork.
    """
    previous = await audit.last(envelope_id)
    event = next_event(
        previous,
        envelope_id=envelope_id,
        event_type=event_type,
        actor=actor,
        data=data or {},
        occurred_at=now,
    )
    await audit.append(event)
    return event
