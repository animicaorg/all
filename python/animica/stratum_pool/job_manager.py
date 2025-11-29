from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List, Optional

from .core import MiningCoreAdapter, MiningJob
from .config import PoolConfig


class JobManager:
    def __init__(self, adapter: MiningCoreAdapter, config: PoolConfig, *, logger: Optional[logging.Logger] = None) -> None:
        self._adapter = adapter
        self._config = config
        self._callbacks: List[Callable[[MiningJob], Awaitable[None]]] = []
        self._current: Optional[MiningJob] = None
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._failure_streak = 0
        self._log = logger or logging.getLogger("animica.stratum_pool.jobs")

    def subscribe(self, callback: Callable[[MiningJob], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def current_job(self) -> Optional[MiningJob]:
        return self._current

    def _next_wait(self, *, success: bool) -> float:
        """Calculate the next sleep duration with simple exponential backoff."""

        if success:
            self._failure_streak = 0
            return self._config.poll_interval

        self._failure_streak += 1
        backoff = self._config.poll_interval * (2**self._failure_streak)
        return min(backoff, 30.0)

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            success = True
            try:
                job = await self._adapter.get_new_job()
                if self._current is None or job.job_id != self._current.job_id:
                    self._current = job
                    for cb in list(self._callbacks):
                        await cb(job)
            except Exception:  # noqa: BLE001
                self._log.warning("job poll failed", exc_info=True)
                success = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._next_wait(success=success))
            except asyncio.TimeoutError:
                continue

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop(), name="job-manager")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
