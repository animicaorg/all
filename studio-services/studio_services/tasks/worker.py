from __future__ import annotations

"""
Verification worker

Pulls "verify" jobs from the SQLite queue, invokes the verification service to
recompile the submitted source/manifest, compares the computed code-hash against
the on-chain artifact (address or tx-hash), and persists the result.

Design goals
------------
- Safe concurrent polling via short-lived leases (see tasks.queue).
- Idempotent behavior: if the same idempotency_key was enqueued, upstream
  enqueue code should have de-duplicated. The worker always treats jobs as
  independent units and relies on the verification service to upsert results.
- Robust to crashes: failed attempts are re-queued with exponential backoff
  until max_attempts.
- Graceful shutdown: stop via an asyncio.Event.
"""

import asyncio
import json
import logging
import os
import signal
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .queue import SQLiteTaskQueue, Task
from ..logging import get_logger  # structured logger (falls back to std logging)
from .. import errors as svc_errors

# The verification logic lives in studio_services.services.verify
# We only depend on its public coroutine helpers. To stay resilient to minor
# refactors, we detect a few common function names at runtime.
try:
    from ..services import verify as verify_service  # type: ignore
except Exception as e:  # pragma: no cover - import-time diagnostics
    verify_service = None  # resolved lazily in worker loop


@dataclass(frozen=True)
class WorkerConfig:
    kinds: tuple[str, ...] = ("verify", "verify:source")
    lease_seconds: int = 60
    poll_interval_idle: float = 0.75
    retry_backoff_base: int = 3
    retry_backoff_max: int = 300


class VerifyWorker:
    """
    Background worker that processes verification jobs.
    """

    def __init__(
        self,
        *,
        app,
        queue: SQLiteTaskQueue,
        worker_id: Optional[str] = None,
        config: WorkerConfig | None = None,
    ) -> None:
        self.app = app
        self.queue = queue
        self.worker_id = worker_id or f"verify-{os.getpid()}-{id(self) & 0xFFFF:x}"
        self.config = config or WorkerConfig()
        try:
            self.log = get_logger(__name__).bind(worker_id=self.worker_id)  # type: ignore[attr-defined]
        except Exception:  # fallback to std logging
            self.log = logging.getLogger(__name__)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """
        Main loop. Polls the queue, processes a task, and repeats until stop_event is set.
        """
        self.log.info("verify_worker.start", kinds=self.config.kinds, lease=self.config.lease_seconds)
        await self.queue.connect()

        # best-effort: periodically return expired leases (in case other processes died)
        lease_cleaner = asyncio.create_task(self._periodic_lease_requeue(stop_event))

        try:
            while not stop_event.is_set():
                task = await self.queue.poll(
                    worker_id=self.worker_id,
                    lease_seconds=self.config.lease_seconds,
                    kinds=self.config.kinds,
                )
                if not task:
                    await asyncio.sleep(self.config.poll_interval_idle)
                    continue

                await self._process_task(task)
        finally:
            lease_cleaner.cancel()
            with contextlib.suppress(Exception):
                await lease_cleaner
            self.log.info("verify_worker.stop")

    async def _process_task(self, task: Task) -> None:
        """
        Execute a single task under try/except and ACK success/failure accordingly.
        """
        payload: dict[str, Any]
        try:
            if isinstance(task.payload, dict):
                payload = task.payload
            elif isinstance(task.payload, str):
                payload = json.loads(task.payload)
            else:
                raise ValueError("Task payload must be JSON object or string")

            self.log.info(
                "verify_worker.claimed",
                task_id=task.id,
                kind=task.kind,
                attempts=task.attempts,
            )

            result = await self._run_verification(payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self.log.exception("verify_worker.error", task_id=task.id, error=err)
            await self.queue.ack_failure(
                task_id=task.id,
                error=err,
                backoff_base=self.config.retry_backoff_base,
                backoff_max=self.config.retry_backoff_max,
            )
            return

        # If we reached here, verification finished (success or verified-false),
        # but the job processing itself succeeded. Persisted result is returned
        # by the service; we store a compact summary into the queue's result.
        try:
            await self.queue.ack_success(task_id=task.id, result=result)
            self.log.info(
                "verify_worker.done",
                task_id=task.id,
                ok=bool(result.get("ok")),
                address=result.get("address"),
                txHash=result.get("txHash"),
                codeHash_expected=result.get("expected_code_hash"),
                codeHash_computed=result.get("computed_code_hash"),
            )
        except Exception as e:  # rare commit failure
            err = f"Post-ack error {type(e).__name__}: {e}"
            self.log.exception("verify_worker.ack_failed", task_id=task.id, error=err)
            # best effort: mark as failure so it can be retried
            await self.queue.ack_failure(task_id=task.id, error=err)

    async def _run_verification(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Bridge into the verification service. Supports multiple call signatures:

        1) If payload includes 'job_id' (string), call:
              await verify_service.run_verify_job(app=self.app, job_id=...)
           or:
              await verify_service.process_job(app=self.app, job_id=...)
        2) Otherwise pass the whole payload as a request:
              await verify_service.run_verify_payload(app=self.app, payload=payload)
           or:
              await verify_service.verify_and_persist(app=self.app, request=payload)

        Must return a result dict with at least:
          { "ok": bool, "address"?: str, "txHash"?: str,
            "expected_code_hash"?: str, "computed_code_hash"?: str, "id": str }
        """
        service = self._resolve_verify_service()

        if "job_id" in payload and isinstance(payload["job_id"], str):
            job_id = payload["job_id"]
            for fname in ("run_verify_job", "process_job", "process_verify_job"):
                fn = getattr(service, fname, None)
                if callable(fn):
                    return await fn(app=self.app, job_id=job_id)  # type: ignore[misc]
            raise RuntimeError("Verification service does not expose a job-by-id runner")

        # Pass-through payload path
        for fname, kw in (
            ("run_verify_payload", {"payload": payload}),
            ("verify_and_persist", {"request": payload}),
            ("process_verify_payload", {"payload": payload}),
        ):
            fn = getattr(service, fname, None)
            if callable(fn):
                return await fn(app=self.app, **kw)  # type: ignore[misc]

        raise RuntimeError("Verification service missing expected entrypoints")

    def _resolve_verify_service(self):
        global verify_service
        if verify_service is None:
            # Late import to avoid circular imports during app boot
            from ..services import verify as _verify  # type: ignore
            verify_service = _verify
        return verify_service

    async def _periodic_lease_requeue(self, stop_event: asyncio.Event) -> None:
        """
        Periodically return expired leases to the queue.
        """
        try:
            while not stop_event.is_set():
                await asyncio.sleep(5.0)
                try:
                    n = await self.queue.requeue_expired_leases(limit=200)
                    if n:
                        self.log.info("verify_worker.requeue_expired", count=n)
                except Exception:
                    self.log.exception("verify_worker.requeue_error")
        except asyncio.CancelledError:
            return


# --- Convenience runner ---------------------------------------------------------

import contextlib


async def run_worker(app, queue: SQLiteTaskQueue, *, stop_event: Optional[asyncio.Event] = None) -> None:
    """
    Run a single VerifyWorker until stop_event is set. If stop_event is None,
    a new Event is created and SIGINT/SIGTERM are wired to it.
    """
    stop = stop_event or asyncio.Event()

    # Wire OS signals if we own the stop event
    def _signal_handler(signame: str):
        def _inner():
            logger = logging.getLogger(__name__)
            logger.info("verify_worker.signal", signal=signame)
            stop.set()

        return _inner

    if stop_event is None and hasattr(signal, "signal"):
        with contextlib.suppress(Exception):
            signal.signal(signal.SIGINT, lambda *_: _signal_handler("SIGINT")())
            signal.signal(signal.SIGTERM, lambda *_: _signal_handler("SIGTERM")())

    worker = VerifyWorker(app=app, queue=queue)
    await worker.run_forever(stop)


# CLI helper: python -m studio_services.tasks.worker (dev convenience)
if __name__ == "__main__":  # pragma: no cover
    # Lazy bootstrap: expects the app factory at studio_services.app:build_app()
    import asyncio

    try:
        from ..app import build_app  # type: ignore
    except Exception as e:
        print(f"Failed to import app factory: {e}", file=sys.stderr)
        sys.exit(2)

    async def _main():
        app = build_app()
        q = SQLiteTaskQueue(db_path=os.environ.get("STUDIO_SVC_DB", "studio_services.sqlite"))
        await run_worker(app, q)

    asyncio.run(_main())
