from pathlib import Path

import pytest

from esign.adapters.storage.local import LocalBlobStorage
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
