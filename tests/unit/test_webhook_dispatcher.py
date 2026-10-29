"""Retry and backoff logic, with in-memory fakes instead of Mongo and HTTP."""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from esign.application.ports import Database, WebhookTransportError
from esign.application.webhooks import WebhookDispatcher, WebhookTarget
from esign.domain.envelopes import Envelope
from esign.domain.webhooks import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    DeliveryAttempt,
    DeliveryStatus,
    verify_signature,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SECRET = b"secret"


class FakeDeliveries:
    def __init__(self) -> None:
        self.attempts: list[tuple[DeliveryAttempt, DeliveryStatus]] = []

    async def record_attempt(
        self, delivery_id: str, attempt: DeliveryAttempt, status: DeliveryStatus
    ) -> None:
        self.attempts.append((attempt, status))


class FakeDatabase:
    def __init__(self) -> None:
        self.webhooks = FakeDeliveries()


class ScriptedSender:
    """Replays a list of outcomes: an int is a status code, an exception is raised."""

    def __init__(self, outcomes: list[int | Exception]) -> None:
        self._outcomes = outcomes
        self.requests: list[tuple[bytes, Mapping[str, str]]] = []

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> int:
        self.requests.append((body, headers))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr("esign.application.webhooks.asyncio.sleep", fake_sleep)
    return recorded


def make_dispatcher(
    sender: ScriptedSender, *, target: WebhookTarget | None = None
) -> tuple[WebhookDispatcher, FakeDatabase]:
    db = FakeDatabase()
    dispatcher = WebhookDispatcher(
        cast(Database, db),
        sender,
        target=target or WebhookTarget("https://hooks.test/esign", SECRET),
        max_retries=3,
        backoff_seconds=1.0,
        clock=lambda: NOW,
    )
    return dispatcher, db


def envelope() -> Envelope:
    return Envelope.create(
        title="t",
        document_id="d",
        document_sha256="a" * 64,
        signers=[("A", "a@example.com")],
        now=NOW,
    )


async def run(dispatcher: WebhookDispatcher) -> None:
    delivery = dispatcher.new_delivery(envelope(), "envelope.completed", NOW)
    assert delivery is not None
    dispatcher.dispatch(delivery)
    await dispatcher.drain()


async def test_first_success_stops(sleeps: list[float]) -> None:
    sender = ScriptedSender([204])
    dispatcher, db = make_dispatcher(sender)
    await run(dispatcher)
    assert [status for _, status in db.webhooks.attempts] == [DeliveryStatus.DELIVERED]
    assert sleeps == []


async def test_retries_with_exponential_backoff_then_succeeds(sleeps: list[float]) -> None:
    sender = ScriptedSender([500, WebhookTransportError("connection refused"), 503, 200])
    dispatcher, db = make_dispatcher(sender)
    await run(dispatcher)
    statuses = [status for _, status in db.webhooks.attempts]
    assert statuses == [DeliveryStatus.PENDING] * 3 + [DeliveryStatus.DELIVERED]
    assert sleeps == [1.0, 2.0, 4.0]
    errors = [attempt.error for attempt, _ in db.webhooks.attempts]
    assert errors[0] == "receiver answered HTTP 500"
    assert errors[1] == "connection refused"
    assert errors[3] is None


async def test_gives_up_after_three_retries(sleeps: list[float]) -> None:
    sender = ScriptedSender([500, 500, 500, 500])
    dispatcher, db = make_dispatcher(sender)
    await run(dispatcher)
    assert len(sender.requests) == 4  # one attempt plus three retries
    assert db.webhooks.attempts[-1][1] is DeliveryStatus.FAILED


async def test_requests_carry_a_verifiable_signature(sleeps: list[float]) -> None:
    sender = ScriptedSender([200])
    dispatcher, _ = make_dispatcher(sender)
    await run(dispatcher)
    body, headers = sender.requests[0]
    timestamp = int(headers[TIMESTAMP_HEADER])
    assert verify_signature(SECRET, timestamp, body, headers[SIGNATURE_HEADER], now=timestamp)


async def test_crash_in_delivery_is_logged_not_raised(
    sleeps: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    sender = ScriptedSender([])  # pop from empty list -> IndexError
    dispatcher, _ = make_dispatcher(sender)
    await run(dispatcher)
    assert "webhook delivery crashed" in caplog.text


def test_no_delivery_without_configured_target() -> None:
    db = FakeDatabase()
    dispatcher = WebhookDispatcher(
        cast(Database, db),
        ScriptedSender([]),
        target=None,
        max_retries=3,
        backoff_seconds=1.0,
        clock=lambda: NOW,
    )
    assert dispatcher.new_delivery(envelope(), "envelope.completed", NOW) is None


def test_payload_contains_no_personal_data() -> None:
    dispatcher, _ = make_dispatcher(ScriptedSender([]))
    delivery = dispatcher.new_delivery(envelope(), "envelope.completed", NOW)
    assert delivery is not None
    flattened: Any = str(delivery.payload)
    assert "a@example.com" not in flattened
