#!/usr/bin/env python3
"""
Run the Animica AICF provider worker loop outside of the HTTP server.

This script is useful when you want background job execution (AI/Quantum)
without hosting the FastAPI app — e.g., for local debugging, CI, or when
you feed jobs via stdin.

It relies on the same configuration as the server (see aicf_provider/config.py)
and the same singleton worker (aicf_provider/worker.get_worker).

Usage
-----

# 1) Basic: start worker and print periodic stats
python -m scripts.run_worker --stats-every 5

# 2) Seed a few demo jobs (processed by the local mock executors)
python -m scripts.run_worker --demo-ai 3 --demo-quantum 2 --wait

# 3) Stream jobs from stdin (JSON per line)
#    You can mix AI and quantum jobs — we infer from "kind".
cat <<EOF | python -m scripts.run_worker --stdin --wait
{"kind":"ai","prompt":"hello","max_tokens":12,"temperature":0.0}
{"kind":"quantum","n_qubits":3,"shots":64}
EOF

# 4) Override concurrency via env (preferred) or CLI
AICF_WORKER_CONCURRENCY=4 python -m scripts.run_worker
python -m scripts.run_worker --concurrency 4

Notes
-----
- With the default in-memory queue backend, jobs submitted by the HTTP server
  won't be visible to this separate process. This script is primarily for
  local/dry runs or for deployments where both HTTP and worker run in
  the same process (or when you adapt worker.py to use a shared backend).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from typing import Any, Dict, List, Optional, Sequence

# Local provider modules
try:
    # When executed via `python -m scripts.run_worker` from the template root,
    # the package root is usually already on sys.path. This is a defensive add.
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
except Exception:
    pass

from aicf_provider.config import settings
from aicf_provider.models import (
    JobRecord,
    JobStatus,
    QuantumJobIn,
    AIJobIn,
)
from aicf_provider.worker import get_worker, ProviderWorker


log = logging.getLogger("aicf_provider.run_worker")


def _setup_logging(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=lvl,
        format="%(asctime)s %(levelname)5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # FastAPI/uvicorn noise isn't present here, but keep a consistent style
    logging.getLogger("aicf_provider").setLevel(lvl)


def _resolve_int(value: Optional[int], env_name: str, default: int) -> int:
    if value is not None:
        return int(value)
    env = os.getenv(env_name)
    return int(env) if env is not None else int(default)


async def _maybe_start(worker: ProviderWorker) -> None:
    # The worker may auto-start on first enqueue; if a start() method exists,
    # call it proactively so stats show the running pool immediately.
    if hasattr(worker, "start"):
        try:
            await worker.start()  # type: ignore[attr-defined]
        except TypeError:
            # Some implementations may expose sync start()
            worker.start()  # type: ignore[attr-defined]


async def _periodic_stats(worker: ProviderWorker, interval: float) -> None:
    """Log simple queue/worker stats every `interval` seconds."""
    while True:
        try:
            qsize = getattr(getattr(worker, "_q", None), "qsize", lambda: 0)()
            maxsize = getattr(getattr(worker, "_q", None), "maxsize", 0)
            running = sum(1 for t in getattr(worker, "_workers", []) if not t.done())
            conc = getattr(worker, "concurrency", 0)
            log.info(
                "stats: queued=%s running=%s concurrency=%s queue_max=%s",
                qsize, running, conc, maxsize
            )
        except Exception as exc:
            log.debug("stats error: %s", exc)
        await asyncio.sleep(interval)


def _parse_job(obj: Dict[str, Any]):
    """Infer job type from 'kind' field and construct the appropriate model."""
    kind = obj.get("kind")
    if kind == "quantum":
        return QuantumJobIn(**obj)
    if kind == "ai":
        return AIJobIn(**obj)
    raise ValueError(f"Unknown or missing 'kind': {kind!r}")


async def _seed_demo(
    worker: ProviderWorker,
    n_ai: int,
    n_quantum: int,
    interval_s: float,
) -> List[str]:
    job_ids: List[str] = []
    for i in range(n_ai):
        job = AIJobIn(kind="ai", prompt=f"demo #{i}", max_tokens=16, temperature=0.0)
        jid = await worker.enqueue(job)
        log.info("enqueued demo AI job id=%s", jid)
        job_ids.append(jid)
        if interval_s > 0:
            await asyncio.sleep(interval_s)

    for i in range(n_quantum):
        job = QuantumJobIn(kind="quantum", n_qubits=3, shots=64, client_job_id=f"qdemo-{i}")
        jid = await worker.enqueue(job)
        log.info("enqueued demo Quantum job id=%s", jid)
        job_ids.append(jid)
        if interval_s > 0:
            await asyncio.sleep(interval_s)

    return job_ids


async def _read_stdin_stream(worker: ProviderWorker) -> List[str]:
    """
    Read newline-delimited JSON from stdin, enqueue jobs, and collect ids.
    """
    log.info("reading jobs from stdin (one JSON object per line). Ctrl-D to end.")
    job_ids: List[str] = []
    loop = asyncio.get_event_loop()

    # Use a threadpool to avoid blocking the event loop on sys.stdin.readline()
    def _readline():
        return sys.stdin.readline()

    while True:
        line = await loop.run_in_executor(None, _readline)
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            job = _parse_job(obj)
            jid = await worker.enqueue(job)
            job_ids.append(jid)
            print(jid, flush=True)  # echo id so callers can capture it
            log.info("enqueued stdin job id=%s", jid)
        except Exception as exc:
            log.error("failed to parse/enqueue line: %s (error=%s)", line, exc)
    return job_ids


async def _wait_for_all(worker: ProviderWorker, job_ids: Sequence[str]) -> List[JobRecord]:
    recs: List[JobRecord] = []
    for jid in job_ids:
        try:
            rec = await worker.wait(jid, timeout=None)
            recs.append(rec)
            status = rec.status.value if hasattr(rec.status, "value") else str(rec.status)
            log.info("job %s completed: status=%s", jid, status)
        except Exception as exc:
            log.error("error waiting for %s: %s", jid, exc)
    return recs


async def _graceful_stop(worker: ProviderWorker) -> None:
    if hasattr(worker, "stop"):
        try:
            await worker.stop()  # type: ignore[attr-defined]
        except TypeError:
            worker.stop()  # type: ignore[attr-defined]


async def async_main(args) -> int:
    # Instantiate the singleton worker using config-derived defaults
    worker = get_worker()

    # Optionally adjust concurrency at runtime if the API exists
    desired_conc = _resolve_int(
        args.concurrency, "AICF_WORKER_CONCURRENCY", getattr(settings, "worker_concurrency", 2)
    )
    if hasattr(worker, "set_concurrency"):
        try:
            worker.set_concurrency(int(desired_conc))  # type: ignore[attr-defined]
            log.info("set worker concurrency to %s", desired_conc)
        except Exception:
            log.debug("worker.set_concurrency unavailable or failed; keeping default.")

    # Start the worker pool (if supported)
    await _maybe_start(worker)

    # Periodic stats task
    stats_task: Optional[asyncio.Task] = None
    if args.stats_every and args.stats_every > 0:
        stats_task = asyncio.create_task(_periodic_stats(worker, float(args.stats_every)))

    # Seed demo jobs if requested
    seeded_ids: List[str] = []
    if args.demo_ai or args.demo_quantum:
        seeded_ids = await _seed_demo(worker, args.demo_ai, args.demo_quantum, float(args.demo_interval))

    # Optionally read stdin jobs
    stdin_ids: List[str] = []
    if args.stdin:
        stdin_ids = await _read_stdin_stream(worker)

    # Optionally wait for jobs to complete
    if args.wait:
        to_wait = [*seeded_ids, *stdin_ids]
        await _wait_for_all(worker, to_wait)

    # Idle until interrupted if neither wait nor stdin nor demo was used
    if not args.wait and not args.stdin and not (args.demo_ai or args.demo_quantum):
        log.info("worker running; press Ctrl-C to stop.")
        stop_event = asyncio.Event()

        def _on_signal(*_):
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, _on_signal)
            except NotImplementedError:
                # Windows or restricted environments: fall back to blocking input
                pass
        await stop_event.wait()

    # Cleanup
    if stats_task:
        stats_task.cancel()
        try:
            await stats_task
        except Exception:
            pass

    await _graceful_stop(worker)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AICF provider worker loop.")
    parser.add_argument("--concurrency", type=int, default=None, help="Override worker concurrency")
    parser.add_argument("--stats-every", type=float, default=5.0, help="Log stats every N seconds (0 to disable)")
    parser.add_argument("--demo-ai", type=int, default=0, help="Seed N demo AI jobs")
    parser.add_argument("--demo-quantum", type=int, default=0, help="Seed N demo Quantum jobs")
    parser.add_argument("--demo-interval", type=float, default=0.1, help="Seconds to sleep between demo job enqueues")
    parser.add_argument("--stdin", action="store_true", help="Read newline-delimited JSON jobs from stdin")
    parser.add_argument("--wait", action="store_true", help="Wait for seeded/stdin jobs to finish before exit")
    parser.add_argument("--log-level", default=str(getattr(settings, "log_level", "INFO")), help="Logging level")

    args = parser.parse_args(argv)

    _setup_logging(args.log_level)

    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        log.info("interrupted, exiting")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
