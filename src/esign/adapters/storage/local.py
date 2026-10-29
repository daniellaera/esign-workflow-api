"""Blob storage on a local directory (a Docker volume in deployment).

An object store such as S3 would implement the same two methods.
"""

import asyncio
import os
import re
import tempfile
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

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(f"invalid storage key: {key!r}")
        # Two-character fan-out keeps directories small.
        return self._root / key[:2] / f"{key}.pdf"

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        if path.exists():
            return  # content-addressed: same key, same bytes
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
