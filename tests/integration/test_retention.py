"""Demo-mode retention: MongoDB TTL indexes really delete old records.

The test container runs with ttlMonitorSleepSecs=1, so expiry happens within
seconds instead of MongoDB's default minute.
"""

import asyncio
from datetime import timedelta

import pytest

from esign.adapters.mongo.database import (
    RETENTION_FIELDS,
    RETENTION_INDEX,
    MongoDatabase,
    apply_retention,
)
from esign.domain.common import new_id, utc_now
from esign.domain.documents import Document

pytestmark = pytest.mark.integration


async def retention_seconds(database: MongoDatabase, collection: str) -> int | None:
    indexes = await database._db[collection].index_information()
    index = indexes.get(RETENTION_INDEX)
    return None if index is None else int(index["expireAfterSeconds"])


async def test_no_retention_indexes_outside_demo_mode(database: MongoDatabase) -> None:
    for collection in RETENTION_FIELDS:
        assert await retention_seconds(database, collection) is None


async def test_retention_indexes_cover_every_collection(database: MongoDatabase) -> None:
    await apply_retention(database._db, timedelta(hours=24))
    for collection in RETENTION_FIELDS:
        assert await retention_seconds(database, collection) == 24 * 3600


async def test_turning_retention_off_drops_the_indexes(database: MongoDatabase) -> None:
    await apply_retention(database._db, timedelta(hours=24))
    await apply_retention(database._db, None)
    for collection in RETENTION_FIELDS:
        assert await retention_seconds(database, collection) is None


async def test_records_older_than_the_retention_are_purged(database: MongoDatabase) -> None:
    await apply_retention(database._db, timedelta(seconds=2))
    now = utc_now()
    old = Document(new_id(), "old.pdf", 1, "a" * 64, now - timedelta(hours=1))
    fresh = Document(new_id(), "fresh.pdf", 1, "b" * 64, now + timedelta(hours=1))
    await database.documents.add(old)
    await database.documents.add(fresh)

    for _ in range(40):  # up to 20 s
        if await database._db["documents"].count_documents({"_id": old.id}) == 0:
            break
        await asyncio.sleep(0.5)
    assert await database._db["documents"].count_documents({"_id": old.id}) == 0
    assert await database.documents.get(fresh.id) == fresh
