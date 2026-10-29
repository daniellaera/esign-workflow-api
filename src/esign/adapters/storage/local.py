"""Blob storage on a local directory (a Docker volume in deployment).

An object store such as S3 would implement the same two methods.
"""

import asyncio
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

# Keys are SHA-256 hex digests. Rejecting anything else rules out path traversal.
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class LocalBlobStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._write, self._path(key), data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Delete blobs last written before ``cutoff``. Used by demo-mode retention."""
        return await asyncio.to_thread(self._delete_older_than, cutoff.timestamp())

    def _delete_older_than(self, cutoff_timestamp: float) -> int:
        removed = 0
        for path in self._root.glob("??/*.pdf"):
            if path.stat().st_mtime < cutoff_timestamp:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(f"invalid storage key: {key!r}")
        # Two-character fan-out keeps directories small.
        return self._root / key[:2] / f"{key}.pdf"

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.exists():
            # Content-addressed: same key, same bytes. Refresh the timestamp so
            # retention counts from the latest use, not the first upload.
            os.utime(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file and rename, so readers never see a half-written file.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
