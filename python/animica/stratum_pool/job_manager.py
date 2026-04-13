from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, List, Optional

from .config import PoolConfig
from .core import MiningCoreAdapter, MiningJob, TemplateUnavailable


class JobManager:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        config: PoolConfig,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._callbacks: List[Callable[[MiningJob], Awaitable[None]]] = []
        self._current: Optional[MiningJob] = None
        self._stop = asyncio.Event()
        self._refresh = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._failure_streak = 0
        self._periodic_refresh_s = max(5.0, float(config.poll_interval) * 10.0)
        self._renew_margin_s = 2.0
        self._next_periodic_refresh_at = 0.0
        self._last_head_hash: Optional[str] = None
        self._log = logger or logging.getLogger("animica.stratum_pool.jobs")

    def subscribe(self, callback: Callable[[MiningJob], Awaitable[None]]) -> None:
        self._callbacks.append(callback)

    def current_job(self) -> Optional[MiningJob]:
        return self._current

    def request_refresh(self) -> None:
        self._refresh.set()

    def _next_wait(self, *, success: bool) -> float:
        """Calculate the next sleep duration with simple exponential backoff."""

        if success:
            self._failure_streak = 0
            return self._config.poll_interval

        self._failure_streak += 1
        backoff = self._config.poll_interval * (2**self._failure_streak)
        return min(backoff, 30.0)

    async def _wait_until_next_poll(self, timeout: float) -> None:
        if self._stop.is_set():
            return
        if self._refresh.is_set():
            return
        stop_task = asyncio.create_task(self._stop.wait())
        refresh_task = asyncio.create_task(self._refresh.wait())
        done, pending = await asyncio.wait(
            {stop_task, refresh_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if refresh_task in done or self._refresh.is_set():
            return

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            success = True
            wait_override: Optional[float] = None
            try:
                now = time.time()
                manual_refresh = self._refresh.is_set()
                if manual_refresh:
                    self._refresh.clear()

                head_hash = None
                try:
                    head = await self._adapter.get_head_snapshot()
                    if isinstance(head, dict):
                        raw_head_hash = (
                            head.get("hash") or head.get("block_hash") or head.get("head")
                        )
                        if isinstance(raw_head_hash, str) and raw_head_hash.strip():
                            head_hash = raw_head_hash.lower()
                            self._last_head_hash = head_hash
                except Exception:
                    head_hash = self._last_head_hash

                refresh_reason: Optional[str] = None
                def _attr(obj: object, name: str, default: object = None) -> object:
                    return getattr(obj, name, default)

                current_expires_at = _attr(self._current, "expires_at")
                current_parent_hash = _attr(self._current, "parent_hash")

                if self._current is None:
                    refresh_reason = "initial"
                elif manual_refresh:
                    refresh_reason = "manual_refresh"
                elif (
                    current_expires_at is not None
                    and now >= float(current_expires_at) - self._renew_margin_s
                ):
                    refresh_reason = "template_expired"
                elif (
                    head_hash
                    and current_parent_hash
                    and str(current_parent_hash).lower() != head_hash
                ):
                    refresh_reason = "new_head"
                elif now >= self._next_periodic_refresh_at:
                    # Avoid stale-template churn: do not ask the node for a fresh
                    # template every poll tick because each issuance carries a new
                    # templateId (and often a fresh header timestamp). We refresh
                    # periodically for mempool drift, and immediately for explicit
                    # invalidation reasons above.
                    refresh_reason = "periodic_refresh"

                if refresh_reason is None:
                    await self._wait_until_next_poll(self._next_wait(success=True))
                    continue

                job = await self._adapter.get_new_job()
                replace_reason: Optional[str] = None
                if self._current is None:
                    replace_reason = "initial"
                else:
                    prev = self._current
                    prev_parent_hash = _attr(prev, "parent_hash")
                    job_parent_hash = _attr(job, "parent_hash")
                    prev_theta_micro = _attr(prev, "theta_micro", 0)
                    job_theta_micro = _attr(job, "theta_micro", 0)
                    prev_target = _attr(prev, "target", "")
                    job_target = _attr(job, "target", "")
                    prev_mempool = _attr(prev, "mempool_fingerprint")
                    job_mempool = _attr(job, "mempool_fingerprint")
                    prev_job_id = _attr(prev, "job_id")
                    job_job_id = _attr(job, "job_id")
                    if (
                        prev_parent_hash
                        and job_parent_hash
                        and prev_parent_hash != job_parent_hash
                    ):
                        replace_reason = "new_head"
                    elif int(prev_theta_micro or 0) != int(job_theta_micro or 0):
                        replace_reason = "difficulty_changed"
                    elif str(prev_target or "") != str(job_target or ""):
                        replace_reason = "difficulty_changed"
                    elif prev_mempool and job_mempool and prev_mempool != job_mempool:
                        replace_reason = "mempool_changed"
                    elif prev_job_id != job_job_id:
                        replace_reason = (
                            "template_expired"
                            if refresh_reason == "template_expired"
                            else "manual_refresh"
                            if refresh_reason == "manual_refresh"
                            else "periodic_refresh"
                            if refresh_reason == "periodic_refresh"
                            else "template_refreshed"
                        )

                if replace_reason is not None:
                    self._current = job
                    self._log.info(
                        "job_replaced",
                        extra={
                            "reason": replace_reason,
                            "job_id": _attr(job, "job_id"),
                            "template_id": _attr(job, "template_id"),
                            "height": _attr(job, "height"),
                            "parent_hash": _attr(job, "parent_hash"),
                            "expires_at": _attr(job, "expires_at"),
                        },
                    )
                    for cb in list(self._callbacks):
                        await cb(job)
                elif self._current is not None and _attr(self._current, "job_id") == _attr(job, "job_id"):
                    # Keep lease metadata fresh when template identity stayed stable.
                    self._current = job

                now = time.time()
                job_expires_at = _attr(job, "expires_at")
                if job_expires_at is not None:
                    ttl = max(1.0, float(job_expires_at) - now)
                    self._next_periodic_refresh_at = now + min(
                        self._periodic_refresh_s, ttl / 2.0
                    )
                else:
                    self._next_periodic_refresh_at = now + self._periodic_refresh_s
            except TemplateUnavailable as exc:
                success = True
                wait_override = max(float(exc.wait_seconds or 0.0), self._config.poll_interval)
                self._log.info(
                    "job_refresh_skipped",
                    extra={
                        "reason": exc.reason,
                        "wait_seconds": exc.wait_seconds,
                        "head": exc.head,
                    },
                )
            except Exception:  # noqa: BLE001
                self._log.warning("job poll failed", exc_info=True)
                success = False
            wait_s = (
                wait_override
                if wait_override is not None
                else self._next_wait(success=success)
            )
            await self._wait_until_next_poll(wait_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._poll_loop(), name="job-manager")

    async def stop(self) -> None:
        self._stop.set()
        self._refresh.set()
        if self._task is not None:
            await self._task
            self._task = None
