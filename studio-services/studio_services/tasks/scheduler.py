from __future__ import annotations

"""
Task Scheduler

Starts/stops one or more background workers (currently: verification workers)
and handles graceful shutdown. Designed to be embedded into the FastAPI app
lifecycle or run as a standalone process.

Key properties
--------------
- Async-first: workers run in the current event loop.
- Clean shutdown: on stop(), signals an asyncio.Event, lets workers finish the
  current item, then cancels lingering tasks with a timeout.
- Fault visibility: task crashes are logged; a crashed worker is restarted
  (optional) to maintain desired concurrency.

Environment overrides (for __main__ runner)
-------------------------------------------
VERIFY_WORKERS      int, default 2
STUDIO_SVC_DB       path to SQLite DB, default "studio_services.sqlite"
"""

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from typing import List, Optional

from ..logging import get_logger  # structured logger; falls back to std logging in worker if absent
from .queue import SQLiteTaskQueue
from .worker import VerifyWorker, WorkerConfig


@dataclass(frozen=True)
class SchedulerConfig:
    verify_workers: int = 2
    worker_config: WorkerConfig = field(default_factory=WorkerConfig)
    restart_crashed_workers: bool = True
    shutdown_timeout: float = 15.0  # seconds to wait before cancelling tasks


class TaskScheduler:
    """
    Manages a pool of VerifyWorker instances.
    """

    def __init__(
        self,
        *,
        app,
        queue: SQLiteTaskQueue,
        config: SchedulerConfig | None = None,
    ) -> None:
        self.app = app
        self.queue = queue
        self.config = config or SchedulerConfig()
        try:
            self.log = get_logger(__name__).bind(role="scheduler")  # type: ignore[attr-defined]
        except Exception:
            self.log = logging.getLogger(__name__)
        self._stop = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._workers: List[VerifyWorker] = []
        self._restarts_enabled = self.config.restart_crashed_workers
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        await self.queue.connect()
        self._started = True
        self.log.info(
            "scheduler.start",
            verify_workers=self.config.verify_workers,
            kinds=self.config.worker_config.kinds,
        )
        # Spawn verify workers
        for i in range(self.config.verify_workers):
            worker = VerifyWorker(
                app=self.app,
                queue=self.queue,
                worker_id=f"verify-{os.getpid()}-{i}",
                config=self.config.worker_config,
            )
            self._workers.append(worker)
            t = asyncio.create_task(worker.run_forever(self._stop), name=f"verify-worker-{i}")
            t.add_done_callback(self._on_worker_done(i))
            self._tasks.append(t)

    def _on_worker_done(self, index: int):
        def _cb(task: asyncio.Task):
            try:
                task.result()
                # If result() succeeds, the worker loop exited normally (stop signalled)
                self.log.info("worker.exited", index=index)
            except asyncio.CancelledError:
                self.log.info("worker.cancelled", index=index)
            except Exception as e:
                self.log.exception("worker.crashed", index=index, error=f"{type(e).__name__}: {e}")
                if self._restarts_enabled and not self._stop.is_set():
                    self.log.warning("worker.restarting", index=index)
                    # Restart the crashed worker
                    worker = self._workers[index]
                    t = asyncio.create_task(worker.run_forever(self._stop), name=f"verify-worker-{index}")
                    t.add_done_callback(self._on_worker_done(index))
                    self._tasks[index] = t
        return _cb

    async def stop(self) -> None:
        if not self._started:
            return
        self.log.info("scheduler.stop.begin")
        self._stop.set()

        # First, let tasks wind down naturally
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=self.config.shutdown_timeout)
        except asyncio.TimeoutError:
            self.log.warning("scheduler.stop.timeout_cancel")
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            # Give a brief grace period for cancellations
            with contextlib.suppress(Exception):
                await asyncio.gather(*self._tasks, return_exceptions=True)

        self.log.info("scheduler.stop.finished")
        self._started = False

    async def __aenter__(self) -> "TaskScheduler":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def run_until_stopped(self) -> None:
        """
        Convenience: wires SIGINT/SIGTERM and blocks until stop() is called.
        """
        self._wire_signals()
        await self.start()
        await self._stop.wait()
        await self.stop()

    # --- signal handling ---

    def _wire_signals(self) -> None:
        if not hasattr(signal, "signal"):
            return

        def _handler(signame: str):
            self.log.info("scheduler.signal", signal=signame)
            self._stop.set()

        try:
            signal.signal(signal.SIGINT, lambda *_: _handler("SIGINT"))
            signal.signal(signal.SIGTERM, lambda *_: _handler("SIGTERM"))
        except Exception:  # pragma: no cover - not available on some platforms
            self.log.warning("scheduler.signal_unsupported")


# --- Convenience factory & CLI runner ------------------------------------------

import contextlib


async def run_scheduler(app, *, queue: SQLiteTaskQueue, config: SchedulerConfig | None = None) -> None:
    """
    Run a scheduler with signal handling until stopped.
    """
    scheduler = TaskScheduler(app=app, queue=queue, config=config)
    await scheduler.run_until_stopped()


# Compatibility alias for legacy imports
Scheduler = TaskScheduler


if __name__ == "__main__":  # pragma: no cover - dev convenience
    import asyncio

    try:
        from ..app import build_app  # FastAPI app factory
    except Exception as e:
        print(f"Failed to import app factory: {e}", flush=True)
        raise SystemExit(2)

    # Env overrides
    verify_workers = int(os.getenv("VERIFY_WORKERS", "2"))
    db_path = os.getenv("STUDIO_SVC_DB", "studio_services.sqlite")

    async def _main():
        app = build_app()
        queue = SQLiteTaskQueue(db_path=db_path)
        config = SchedulerConfig(verify_workers=verify_workers)
        await run_scheduler(app, queue=queue, config=config)

    asyncio.run(_main())
