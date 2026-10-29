"""Small helpers shared by the domain modules."""

import hashlib
import uuid
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time truncated to milliseconds.

    MongoDB stores datetimes with millisecond precision. Truncating up front
    means a value read back from the database is identical to the one we
    hashed into the audit chain.
    """
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def new_id() -> str:
    return uuid.uuid4().hex


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
