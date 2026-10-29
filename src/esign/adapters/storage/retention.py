"""Periodic deletion of old PDF files, for demo mode.

MongoDB's TTL indexes expire the database records; this task does the same
for the files on disk.
"""

import asyncio
import contextlib
import logging
from datetime import timedelta

from esign.adapters.storage.local import LocalBlobStorage
from esign.application.ports import Clock

logger = logging.getLogger(__name__)


class FileRetentionTask:
    def __init__(
        self,
        storage: LocalBlobStorage,
        *,
        retention: timedelta,
        interval: timedelta,
        clock: Clock,
    ) -> None:
        self._storage = storage
        self._retention = retention
        self._interval = interval
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever(), name="file-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def run_once(self) -> int:
        removed = await self._storage.delete_older_than(self._clock() - self._retention)
        if removed:
            logger.info("purged old documents", extra={"files_removed": removed})
        return removed

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("file retention run failed")
            await asyncio.sleep(self._interval.total_seconds())
