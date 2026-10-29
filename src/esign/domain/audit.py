"""Tamper-evident audit trail.

Each envelope has its own chain of events. An event's hash covers its content
plus the previous event's hash, so editing, deleting or reordering any stored
event breaks every hash after it. This detects tampering; it does not prevent
it (an attacker with write access could rebuild the whole chain). Anchoring the
latest hash somewhere external, e.g. in the signed PDF or a timestamping
service, would close that gap.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

GENESIS_HASH: Final = "0" * 64

type AuditValue = str | int | None


class AuditEventType(StrEnum):
    ENVELOPE_CREATED = "envelope.created"
    ENVELOPE_SENT = "envelope.sent"
    SIGNER_INVITED = "signer.invited"
    SIGNER_VIEWED = "signer.viewed"
    SIGNER_SIGNED = "signer.signed"
    SIGNER_DECLINED = "signer.declined"
    ENVELOPE_COMPLETED = "envelope.completed"
    ENVELOPE_DECLINED = "envelope.declined"
    ENVELOPE_EXPIRED = "envelope.expired"


@dataclass(frozen=True)
class AuditEvent:
    envelope_id: str
    sequence: int
    type: AuditEventType
    occurred_at: datetime
    actor: str
    data: Mapping[str, AuditValue]
    previous_hash: str
    hash: str


@dataclass(frozen=True)
class ChainVerification:
    valid: bool
    # Sequence number of the first event that does not verify, if any.
    broken_at: int | None = None
    events_checked: int = field(default=0)


def compute_event_hash(
    *,
    envelope_id: str,
    sequence: int,
    event_type: str,
    occurred_at: datetime,
    actor: str,
    data: Mapping[str, AuditValue],
    previous_hash: str,
) -> str:
    # Canonical JSON: sorted keys, no whitespace, fixed timestamp format.
    # Any change to this encoding invalidates every stored chain.
    canonical = json.dumps(
        {
            "envelope_id": envelope_id,
            "sequence": sequence,
            "type": event_type,
            "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="milliseconds"),
            "actor": actor,
            "data": dict(data),
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def next_event(
    previous: AuditEvent | None,
    *,
    envelope_id: str,
    event_type: AuditEventType,
    actor: str,
    data: Mapping[str, AuditValue],
    occurred_at: datetime,
) -> AuditEvent:
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = GENESIS_HASH if previous is None else previous.hash
    return AuditEvent(
        envelope_id=envelope_id,
        sequence=sequence,
        type=event_type,
        occurred_at=occurred_at,
        actor=actor,
        data=dict(data),
        previous_hash=previous_hash,
        hash=compute_event_hash(
            envelope_id=envelope_id,
            sequence=sequence,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            data=data,
            previous_hash=previous_hash,
        ),
    )


def verify_chain(events: Sequence[AuditEvent]) -> ChainVerification:
    """Recompute every hash and check each event links to its predecessor.

    ``events`` must be ordered by sequence, as stored.
    """
    expected_previous = GENESIS_HASH
    for position, event in enumerate(events, start=1):
        recomputed = compute_event_hash(
            envelope_id=event.envelope_id,
            sequence=event.sequence,
            event_type=event.type,
            occurred_at=event.occurred_at,
            actor=event.actor,
            data=event.data,
            previous_hash=event.previous_hash,
        )
        if (
            event.sequence != position
            or event.previous_hash != expected_previous
            or event.hash != recomputed
        ):
            return ChainVerification(valid=False, broken_at=position, events_checked=position)
        expected_previous = event.hash
    return ChainVerification(valid=True, events_checked=len(events))
