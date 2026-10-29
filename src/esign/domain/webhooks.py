"""Webhook deliveries and their HMAC signature.

The signature covers ``"{timestamp}.{body}"``. Including the timestamp lets a
receiver reject replays of an old, validly signed request.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

SIGNATURE_PREFIX = "sha256="

# Header names are part of the contract with webhook receivers.
SIGNATURE_HEADER = "X-Esign-Signature"
TIMESTAMP_HEADER = "X-Esign-Timestamp"
EVENT_HEADER = "X-Esign-Event"
DELIVERY_ID_HEADER = "X-Esign-Delivery"


def compute_signature(secret: bytes, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode("ascii") + b"." + body
    return SIGNATURE_PREFIX + hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_signature(
    secret: bytes,
    timestamp: int,
    body: bytes,
    signature: str,
    *,
    now: int,
    tolerance_seconds: int = 300,
) -> bool:
    """What a webhook receiver should do. Used by our tests and documented for clients."""
    if abs(now - timestamp) > tolerance_seconds:
        return False
    # compare_digest takes constant time, so the comparison leaks nothing.
    return hmac.compare_digest(compute_signature(secret, timestamp, body), signature)


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class DeliveryAttempt:
    number: int
    attempted_at: datetime
    status_code: int | None
    error: str | None


@dataclass
class WebhookDelivery:
    id: str
    envelope_id: str
    event: str
    url: str
    payload: dict[str, Any]
    created_at: datetime
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: list[DeliveryAttempt] = field(default_factory=list)
