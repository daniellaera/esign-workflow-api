import dataclasses
import itertools
from datetime import UTC, datetime, timedelta

from esign.domain.audit import (
    GENESIS_HASH,
    AuditEvent,
    AuditEventType,
    next_event,
    verify_chain,
)

START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def build_chain(length: int = 4) -> list[AuditEvent]:
    events: list[AuditEvent] = []
    previous = None
    for i in range(length):
        previous = next_event(
            previous,
            envelope_id="env-1",
            event_type=AuditEventType.SIGNER_VIEWED,
            actor=f"signer:{i}@example.com",
            data={"signer_index": i, "ip": "203.0.113.7", "user_agent": None},
            occurred_at=START + timedelta(seconds=i),
        )
        events.append(previous)
    return events


def test_first_event_links_to_genesis() -> None:
    first = build_chain(1)[0]
    assert first.sequence == 1
    assert first.previous_hash == GENESIS_HASH
    assert len(first.hash) == 64


def test_events_link_to_their_predecessor() -> None:
    events = build_chain()
    for previous, current in itertools.pairwise(events):
        assert current.previous_hash == previous.hash
        assert current.sequence == previous.sequence + 1


def test_untouched_chain_verifies() -> None:
    result = verify_chain(build_chain())
    assert result.valid
    assert result.broken_at is None
    assert result.events_checked == 4


def test_empty_chain_verifies() -> None:
    assert verify_chain([]).valid


def test_edited_event_is_detected() -> None:
    events = build_chain()
    events[1] = dataclasses.replace(events[1], data={"signer_index": 99})
    result = verify_chain(events)
    assert not result.valid
    assert result.broken_at == 2


def test_rehashed_edit_breaks_the_next_link() -> None:
    """An attacker who recomputes the edited event's hash still breaks its successor."""
    events = build_chain()
    edited = next_event(
        events[0],
        envelope_id="env-1",
        event_type=AuditEventType.SIGNER_VIEWED,
        actor="signer:mallory@example.com",
        data={},
        occurred_at=events[1].occurred_at,
    )
    events[1] = edited
    assert verify_chain(events).broken_at == 3


def test_deleted_event_is_detected() -> None:
    events = build_chain()
    del events[1]
    assert verify_chain(events).broken_at == 2


def test_reordered_events_are_detected() -> None:
    events = build_chain()
    events[1], events[2] = events[2], events[1]
    assert not verify_chain(events).valid


def test_hash_ignores_timezone_representation() -> None:
    """Mongo returns UTC; a different offset for the same instant must hash identically."""
    event = build_chain(1)[0]
    shifted = dataclasses.replace(
        event, occurred_at=event.occurred_at.astimezone(datetime.now().astimezone().tzinfo)
    )
    assert verify_chain([shifted]).valid
