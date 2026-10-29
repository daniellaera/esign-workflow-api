import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from esign.adapters.storage.local import LocalBlobStorage
from esign.adapters.storage.retention import FileRetentionTask
from esign.domain.common import sha256_hex


async def test_round_trip(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    key = sha256_hex(b"hello")
    await storage.put(key, b"hello")
    assert await storage.get(key) == b"hello"
    assert (tmp_path / key[:2] / f"{key}.pdf").exists()


async def test_put_is_idempotent(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    key = sha256_hex(b"hello")
    await storage.put(key, b"hello")
    await storage.put(key, b"hello")
    assert list(tmp_path.rglob("*.tmp")) == []


@pytest.mark.parametrize("key", ["../../etc/passwd", "ABC", "a" * 63, ""])
async def test_rejects_keys_that_are_not_sha256(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key"):
        await LocalBlobStorage(tmp_path).get(key)


async def test_missing_key_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await LocalBlobStorage(tmp_path).get("a" * 64)


async def test_delete_older_than_removes_only_old_files(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    old_key, new_key = sha256_hex(b"old"), sha256_hex(b"new")
    await storage.put(old_key, b"old")
    await storage.put(new_key, b"new")
    two_days_ago = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(tmp_path / old_key[:2] / f"{old_key}.pdf", (two_days_ago, two_days_ago))

    removed = await storage.delete_older_than(datetime.now(UTC) - timedelta(days=1))
    assert removed == 1
    with pytest.raises(FileNotFoundError):
        await storage.get(old_key)
    assert await storage.get(new_key) == b"new"


async def test_storing_again_refreshes_the_retention_clock(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    key = sha256_hex(b"reused")
    await storage.put(key, b"reused")
    path = tmp_path / key[:2] / f"{key}.pdf"
    two_days_ago = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(path, (two_days_ago, two_days_ago))

    await storage.put(key, b"reused")  # uploaded again
    assert await storage.delete_older_than(datetime.now(UTC) - timedelta(days=1)) == 0


async def test_retention_task_purges_with_its_clock(tmp_path: Path) -> None:
    storage = LocalBlobStorage(tmp_path)
    await storage.put(sha256_hex(b"x"), b"x")
    in_two_days = datetime.now(UTC) + timedelta(days=2)
    task = FileRetentionTask(
        storage, retention=timedelta(days=1), interval=timedelta(hours=1), clock=lambda: in_two_days
    )
    assert await task.run_once() == 1
    task.start()
    await task.stop()
