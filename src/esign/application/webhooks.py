"""Background webhook delivery with retries and exponential backoff.

The delivery record is written in the same transaction as the state change
that triggers it, so we never notify about something that was rolled back.
Delivery itself runs in an in-process asyncio task after the commit. That task
is lost if the process dies mid-retry; see docs/adr/0006-webhook-delivery.md.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime

from esign.application.ports import Clock, Database, WebhookSender, WebhookTransportError
from esign.domain.common import new_id
from esign.domain.envelopes import Envelope
from esign.domain.webhooks import (
    DELIVERY_ID_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    DeliveryAttempt,
    DeliveryStatus,
    WebhookDelivery,
    compute_signature,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebhookTarget:
    url: str
    secret: bytes


class WebhookDispatcher:
    def __init__(
        self,
        db: Database,
        sender: WebhookSender,
        *,
        target: WebhookTarget | None,
        max_retries: int,
        backoff_seconds: float,
        clock: Clock,
    ) -> None:
        self._db = db
        self._sender = sender
        self._target = target
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        # Keep references to running tasks: asyncio only holds weak ones.
        self._tasks: set[asyncio.Task[None]] = set()

    def new_delivery(self, envelope: Envelope, event: str, now: datetime) -> WebhookDelivery | None:
        """Build the delivery record, or None when no webhook URL is configured."""
        if self._target is None:
            return None
        delivery_id = new_id()
        return WebhookDelivery(
            id=delivery_id,
            envelope_id=envelope.id,
            event=event,
            url=self._target.url,
            # No personal data in the payload: receivers fetch details via the API.
            payload={
                "delivery_id": delivery_id,
                "event": event,
                "envelope_id": envelope.id,
                "status": envelope.status.value,
                "document_sha256": envelope.current_sha256,
                "occurred_at": now.isoformat(),
            },
            created_at=now,
        )

    def dispatch(self, delivery: WebhookDelivery) -> None:
        task = asyncio.create_task(self._run(delivery), name=f"webhook-{delivery.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for all in-flight deliveries. Used by tests."""
        while self._tasks:
            await asyncio.gather(*self._tasks)

    async def shutdown(self, timeout_seconds: float) -> None:
        """Give in-flight deliveries a grace period, then cancel them."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(self._tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning("cancelled pending webhook deliveries", extra={"count": len(pending)})

    async def _run(self, delivery: WebhookDelivery) -> None:
        # Nobody awaits this task, so an exception would otherwise vanish.
        try:
            await self._deliver(delivery)
        except Exception:
            logger.exception("webhook delivery crashed", extra={"delivery_id": delivery.id})

    async def _deliver(self, delivery: WebhookDelivery) -> None:
        if self._target is None:
            return
        body = json.dumps(delivery.payload, sort_keys=True, separators=(",", ":")).encode()
        total_attempts = 1 + self._max_retries

        for number in range(1, total_attempts + 1):
            attempted_at = self._clock()
            timestamp = int(attempted_at.timestamp())
            headers = {
                "Content-Type": "application/json",
                EVENT_HEADER: delivery.event,
                DELIVERY_ID_HEADER: delivery.id,
                TIMESTAMP_HEADER: str(timestamp),
                SIGNATURE_HEADER: compute_signature(self._target.secret, timestamp, body),
            }

            status_code: int | None = None
            error: str | None = None
            try:
                status_code = await self._sender.post(delivery.url, body, headers)
            except WebhookTransportError as exc:
                error = str(exc)

            succeeded = status_code is not None and 200 <= status_code < 300
            if not succeeded and error is None:
                error = f"receiver answered HTTP {status_code}"
            if succeeded:
                status = DeliveryStatus.DELIVERED
            elif number == total_attempts:
                status = DeliveryStatus.FAILED
            else:
                status = DeliveryStatus.PENDING

            attempt = DeliveryAttempt(number, attempted_at, status_code, error)
            await self._db.webhooks.record_attempt(delivery.id, attempt, status)
            log_fields = {"delivery_id": delivery.id, "attempt": number, "status_code": status_code}
            if succeeded:
                logger.info("webhook delivered", extra=log_fields)
                return
            logger.warning("webhook attempt failed", extra={**log_fields, "error": error})
            if number < total_attempts:
                await asyncio.sleep(self._backoff_seconds * 2 ** (number - 1))
