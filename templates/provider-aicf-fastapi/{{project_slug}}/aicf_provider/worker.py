"""
Provider-side async worker for handling AICF jobs (template).

This module offers a minimal-yet-practical in-memory job queue and worker
pool for two job kinds:
  - "quantum": executed via the local simple sampler backend
  - "ai":      a tiny deterministic echo-style model (demo only)

It is intentionally dependency-free (uses only the stdlib) and designed
to be embedded by the FastAPI service layer:

Typical usage from your FastAPI route handlers
----------------------------------------------
from .worker import get_worker
from .models import QuantumJobIn, AIJobIn

worker = get_worker()

# Enqueue a job
job_id = await worker.enqueue(QuantumJobIn(...))
# Return {job_id} to the client immediately

# Poll status/result later
record = worker.get(job_id)  # returns JobRecord

Notes
-----
- This template stores all state *in memory*. For production, back with
  Redis/SQS/Kafka + a durable database for job records.
- The "AI" runner below is a placeholder. Replace with your real
  model adapter (OpenAI, local LLM, etc.).
- The "quantum" runner delegates to a small sampler in `quantum.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Dict, List, Optional, Union

from .models import JobRecord  # Pydantic model for storage/return
from .models import \
    JobStatus  # Enum[str]: "queued" | "running" | "done" | "failed"
from .models import AIJobIn, AIResult, QuantumJobIn, QuantumResult
from .quantum import run_quantum_job

# -----------------------------------------------------------------------------
# Configuration (environment-driven for the template)
# -----------------------------------------------------------------------------

DEFAULT_CONCURRENCY = int(os.getenv("AICF_WORKER_CONCURRENCY", "2"))
DEFAULT_QUEUE_MAX = int(os.getenv("AICF_WORKER_QUEUE_MAX", "1024"))
DEFAULT_RESULT_TTL_S = int(os.getenv("AICF_WORKER_RESULT_TTL_S", "3600"))
DEFAULT_PRUNE_INTERVAL_S = int(os.getenv("AICF_WORKER_PRUNE_INTERVAL_S", "30"))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _now_s() -> float:
    return time.time()


def make_job_id(prefix: str = "job") -> str:
    """
    Generate a reasonably sortable, collision-resistant ID:
      job_<epoch_ms_hex>_<12-hex>
    """
    ts_ms = int(time.time() * 1000)
    return f"{prefix}_{ts_ms:x}_{secrets.token_hex(6)}"


def _stable_seed_for_ai(job: AIJobIn) -> int:
    """
    Produce a stable seed from job parameters to keep demo outputs
    reproducible across runs (unless AICF_PROVIDER_SEED is set).
    """
    env = os.getenv("AICF_PROVIDER_SEED")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass

    data = json.dumps(
        {
            "prompt": getattr(job, "prompt", ""),
            "max_tokens": getattr(job, "max_tokens", None),
            "temperature": getattr(job, "temperature", None),
            "model": getattr(job, "model", None),
            "client_job_id": getattr(job, "client_job_id", None),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    h = hashlib.sha3_256()
    h.update(b"animica:aicf:ai-demo-v1|")
    h.update(data)
    return int.from_bytes(h.digest()[:4], "big")


def _ai_demo_generate(job: AIJobIn) -> AIResult:
    """
    Extremely small placeholder AI runner.

    Behavior:
      - Deterministic text transformation seeded from job fields
      - "Tokens" are estimated from whitespace splits (demo only)
    """
    # Create deterministic pseudo-random choices
    seed = _stable_seed_for_ai(job)
    # Avoid importing 'random' to keep the function simple—output is fully
    # deterministic from the hash above.
    prompt = (job.prompt or "").strip()

    # Simple transforms chosen deterministically from the seed
    mode = seed % 4
    if mode == 0:
        out = f"Echo: {prompt}"
    elif mode == 1:
        out = f"Upper: {prompt.upper()}"
    elif mode == 2:
        out = f"Lower: {prompt.lower()}"
    else:
        out = f"Summary(naive): {prompt[:96]}{'...' if len(prompt) > 96 else ''}"

    # Apply max_tokens as a *very* naive limiter (count roughly by words)
    max_toks = getattr(job, "max_tokens", None)
    words: List[str] = out.split()
    if isinstance(max_toks, int) and max_toks > 0:
        words = words[:max_toks]
    out = " ".join(words)

    tokens_in = max(1, len(prompt.split()))
    tokens_out = max(1, len(out.split()))
    duration_s = 0.010  # pretend it was instant

    return AIResult(
        job_id="",  # to be filled by caller
        kind="ai",
        model=getattr(job, "model", None) or "template-echo/v1",
        text=out,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        finish_reason="stop",
        duration_s=duration_s,
        raw={"seed": seed},
    )


# -----------------------------------------------------------------------------
# Worker
# -----------------------------------------------------------------------------


class ProviderWorker:
    """
    In-memory async queue + worker pool.
    Not production-hard; replace storage/queueing for real deployments.
    """

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        queue_maxsize: int = DEFAULT_QUEUE_MAX,
        result_ttl_s: int = DEFAULT_RESULT_TTL_S,
        prune_interval_s: int = DEFAULT_PRUNE_INTERVAL_S,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.log = logger or logging.getLogger("aicf_provider.worker")
        self.concurrency = max(1, concurrency)
        self.result_ttl_s = max(30, result_ttl_s)
        self.prune_interval_s = max(5, prune_interval_s)

        self._q: "asyncio.Queue[str]" = asyncio.Queue(maxsize=max(1, queue_maxsize))
        self._jobs: Dict[str, Union[QuantumJobIn, AIJobIn]] = {}
        self._records: Dict[str, JobRecord] = {}

        self._workers: List[asyncio.Task] = []
        self._pruner: Optional[asyncio.Task] = None
        self._started = False

    # ---- lifecycle ----------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        for i in range(self.concurrency):
            t = asyncio.create_task(self._worker_loop(i), name=f"aicf-worker-{i}")
            self._workers.append(t)
        self._pruner = asyncio.create_task(
            self._prune_loop(), name="aicf-worker-pruner"
        )
        self.log.info(
            "provider worker started",
            extra={"concurrency": self.concurrency, "queue_max": self._q.maxsize},
        )

    async def stop(self) -> None:
        for t in self._workers:
            t.cancel()
        if self._pruner:
            self._pruner.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        if self._pruner:
            await asyncio.gather(self._pruner, return_exceptions=True)
        self._workers.clear()
        self._pruner = None
        self._started = False
        self.log.info("provider worker stopped")

    # ---- API ----------------------------------------------------------------

    async def enqueue(self, job: Union[QuantumJobIn, AIJobIn]) -> str:
        """
        Add a job to the queue and create its JobRecord.
        Returns the job_id to be sent back to clients.
        """
        self._ensure_started()
        job_id = make_job_id()
        now = _now_s()
        record = JobRecord(
            job_id=job_id,
            kind=job.kind,
            status=JobStatus.queued,
            submitted_at=now,
            started_at=None,
            finished_at=None,
            result=None,
            error=None,
        )
        self._jobs[job_id] = job
        self._records[job_id] = record

        await self._q.put(job_id)
        return job_id

    def get(self, job_id: str) -> JobRecord:
        """
        Fetch the JobRecord (raises KeyError if unknown or pruned).
        """
        rec = self._records[job_id]  # may raise KeyError
        return rec

    async def wait(self, job_id: str, *, timeout: Optional[float] = None) -> JobRecord:
        """
        Wait until the job is done/failed (polling in-memory state).
        Useful for unit/integration tests.
        """
        deadline = None if timeout is None else (_now_s() + timeout)
        while True:
            rec = self.get(job_id)
            if rec.status in (JobStatus.done, JobStatus.failed):
                return rec
            await asyncio.sleep(0.02)
            if deadline is not None and _now_s() > deadline:
                raise TimeoutError(f"job {job_id} did not finish before timeout")

    # ---- internal loops -----------------------------------------------------

    async def _worker_loop(self, idx: int) -> None:
        try:
            while True:
                job_id = await self._q.get()
                try:
                    await self._run_one(job_id)
                except Exception as exc:  # pragma: no cover - defensive
                    self.log.exception("worker loop error: %s", exc)
                finally:
                    self._q.task_done()
        except asyncio.CancelledError:
            return

    async def _run_one(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            # Job was pruned or unknown; nothing to do
            return

        rec = self._records.get(job_id)
        if rec is None:
            return

        rec.status = JobStatus.running
        rec.started_at = _now_s()

        try:
            if job.kind == "quantum":
                result = run_quantum_job(job, job_id=job_id)  # type: ignore[arg-type]
            elif job.kind == "ai":
                result = _ai_demo_generate(job)  # type: ignore[arg-type]
                result.job_id = job_id
            else:
                raise ValueError(f"unsupported job kind: {job.kind!r}")

            # Attach result & mark success
            rec.result = result  # type: ignore[assignment]
            rec.status = JobStatus.done
            rec.finished_at = _now_s()

        except Exception as exc:
            rec.status = JobStatus.failed
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.finished_at = _now_s()
            # leave result=None on failure
            self.log.warning("job failed", extra={"job_id": job_id, "error": rec.error})

    async def _prune_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.prune_interval_s)
                self._prune_expired()
        except asyncio.CancelledError:
            return

    def _prune_expired(self) -> None:
        """
        Drop finished/failed job records after ttl to bound memory.
        """
        if not self._records:
            return
        now = _now_s()
        to_drop: List[str] = []
        for jid, rec in self._records.items():
            if rec.finished_at and (now - rec.finished_at) > self.result_ttl_s:
                to_drop.append(jid)

        for jid in to_drop:
            self._records.pop(jid, None)
            self._jobs.pop(jid, None)
        if to_drop:
            self.log.info("pruned %d job(s)", len(to_drop))


# -----------------------------------------------------------------------------
# Singleton accessor
# -----------------------------------------------------------------------------

_worker_singleton: Optional[ProviderWorker] = None


def get_worker() -> ProviderWorker:
    """
    Global singleton used by route modules.
    """
    global _worker_singleton
    if _worker_singleton is None:
        _worker_singleton = ProviderWorker()
    return _worker_singleton


__all__ = [
    "ProviderWorker",
    "get_worker",
    "make_job_id",
]
