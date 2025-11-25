#!/usr/bin/env python3
"""
Measure enqueue + deterministic task-id derivation overhead.

This benchmark is backend-pluggable:

  - local   : pure-Python in-memory queue (always available; default)
  - sqlite  : minimal SQLite-backed queue (no external deps)
  - cap     : try to use capabilities.jobs.* implementations if importable

Task-id derivation follows the Animica spec idea:
    task_id = H(domain | chainId | height | txHash | caller | payload)
with SHA3-256 and fixed-width big-endian ints for chainId/height in this bench.
"""

from __future__ import annotations

import argparse
import os
import random
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

try:
    # Prefer Python's stdlib SHA3 (available in 3.6+)
    from hashlib import sha3_256
except Exception as e:  # pragma: no cover
    raise SystemExit(f"Python is missing hashlib.sha3_256: {e}")

# ---- small local helpers ----------------------------------------------------

DOMAIN_TASK_ID_V1 = b"animica.capabilities.task_id.v1"

def u64be(n: int) -> bytes:
    if n < 0:
        raise ValueError("u64be: negative value")
    return int(n).to_bytes(8, "big", signed=False)

def derive_task_id_local(chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload: bytes) -> bytes:
    h = sha3_256()
    h.update(DOMAIN_TASK_ID_V1)
    h.update(u64be(chain_id))
    h.update(u64be(height))
    h.update(tx_hash)
    h.update(caller)
    h.update(payload)
    return h.digest()

def rand_bytes(n: int) -> bytes:
    return secrets.token_bytes(n)

# ---- bench harness ----------------------------------------------------------

def bench_env() -> dict:
    import platform
    return {
        "python": sys.version.split()[0],
        "impl": platform.python_implementation(),
        "platform": platform.platform(aliased=True, terse=True),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count() or 1,
        "time_source": "perf_counter",
    }

def warmup(fn: Callable[[], None], iters: int = 100) -> None:
    for _ in range(max(0, int(iters))):
        fn()

def run_bench(fn: Callable[[], None], iters: int = 100, repeat: int = 1) -> dict:
    iters = max(1, int(iters))
    repeat = max(1, int(repeat))
    samples = []
    total = 0.0
    for _ in range(repeat):
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        dt = time.perf_counter() - t0
        samples.append(dt / iters)
        total += dt
    samples_sorted = sorted(samples)
    idx50 = max(0, int(0.50 * (len(samples_sorted) - 1)))
    idx95 = max(0, int(0.95 * (len(samples_sorted) - 1)))
    return {
        "iters": iters,
        "repeat": repeat,
        "samples": samples,
        "best": min(samples),
        "avg": sum(samples) / len(samples),
        "p50": samples_sorted[idx50],
        "p95": samples_sorted[idx95],
        "total": total,
        "env": bench_env(),
    }

# ---- backends ---------------------------------------------------------------

@dataclass
class EnqueueResult:
    task_id: bytes

class Backend:
    name: str = "base"
    def enqueue(self, chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload: bytes) -> EnqueueResult:
        raise NotImplementedError
    def close(self) -> None:
        pass

class LocalMemBackend(Backend):
    name = "local"
    __slots__ = ("_items",)
    def __init__(self) -> None:
        self._items: list[tuple[bytes, bytes, bytes, int, int]] = []
    def enqueue(self, chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload: bytes) -> EnqueueResult:
        tid = derive_task_id_local(chain_id, height, tx_hash, caller, payload)
        self._items.append((tid, caller, payload, chain_id, height))
        return EnqueueResult(task_id=tid)

class SQLiteBackend(Backend):
    """
    Minimal SQLite-backed queue to approximate disk/SQL overhead.
    """
    name = "sqlite"
    def __init__(self, path: str, fast: bool = False) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=30.0)
        self._db.execute("PRAGMA journal_mode=WAL;")
        self._db.execute(f"PRAGMA synchronous={'OFF' if fast else 'NORMAL'};")
        self._db.execute("PRAGMA temp_store=MEMORY;")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks(
                task_id   BLOB PRIMARY KEY,
                caller    BLOB NOT NULL,
                payload   BLOB NOT NULL,
                chain_id  INTEGER NOT NULL,
                height    INTEGER NOT NULL,
                tx_hash   BLOB NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self._ins = self._db.cursor()
    def enqueue(self, chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload: bytes) -> EnqueueResult:
        tid = derive_task_id_local(chain_id, height, tx_hash, caller, payload)
        now = time.time()
        # Single-row transaction via autocommit cursor
        self._ins.execute(
            "INSERT OR IGNORE INTO tasks(task_id, caller, payload, chain_id, height, tx_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (tid, caller, payload, chain_id, height, tx_hash, now),
        )
        return EnqueueResult(task_id=tid)
    def close(self) -> None:
        try:
            self._ins.close()
        finally:
            self._db.close()

class CapabilitiesBackend(Backend):
    """
    Attempts to use the real capabilities.jobs queue & id derivation if present.
    Falls back to local derivation for the task_id if the function name differs.
    """
    name = "cap"
    def __init__(self, db_uri: Optional[str] = None) -> None:
        # Lazy/defensive imports to avoid hard coupling.
        try:
            from capabilities.jobs import id as id_mod  # type: ignore
            from capabilities.jobs.queue import JobQueue  # type: ignore
        except Exception as e:
            raise RuntimeError(f"capabilities backend unavailable: {e}") from e

        # Pick derivation function
        self._derive: Callable[[int, int, bytes, bytes, bytes], bytes]
        if hasattr(id_mod, "derive_task_id"):
            self._derive = id_mod.derive_task_id  # type: ignore[attr-defined]
        elif hasattr(id_mod, "task_id"):
            self._derive = id_mod.task_id  # type: ignore[attr-defined]
        else:
            self._derive = derive_task_id_local

        # Construct queue (support a couple of likely ctor shapes)
        try:
            if db_uri:
                self._queue = JobQueue(db_uri=db_uri)  # type: ignore[call-arg]
            else:
                self._queue = JobQueue()  # type: ignore[call-arg]
        except TypeError:
            # Maybe it takes a filesystem path instead:
            self._queue = JobQueue(db_uri or "sqlite:///capabilities_bench.db")  # type: ignore[call-arg]
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"failed to construct JobQueue: {e}") from e

        # Find an enqueue-ish method
        if hasattr(self._queue, "enqueue_raw"):
            self._enqueue_fn = self._queue.enqueue_raw  # type: ignore[attr-defined]
        elif hasattr(self._queue, "enqueue"):
            self._enqueue_fn = self._queue.enqueue  # type: ignore[attr-defined]
        else:  # pragma: no cover
            raise RuntimeError("JobQueue has no enqueue/enqueue_raw")

    def enqueue(self, chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload: bytes) -> EnqueueResult:
        tid = self._derive(chain_id, height, tx_hash, caller, payload)
        # Be generous with accepted shapes: many queues accept a dict-like request.
        try:
            self._enqueue_fn(  # type: ignore[misc]
                {
                    "task_id": tid,
                    "chain_id": chain_id,
                    "height": height,
                    "tx_hash": tx_hash,
                    "caller": caller,
                    "payload": payload,
                }
            )
        except TypeError:
            # Maybe (task_id, payload, meta...)
            self._enqueue_fn(tid, payload, {"chain_id": chain_id, "height": height, "tx_hash": tx_hash, "caller": caller})  # type: ignore[misc]
        return EnqueueResult(task_id=tid)

    def close(self) -> None:
        if hasattr(self._queue, "close"):
            try:
                self._queue.close()  # type: ignore[attr-defined]
            except Exception:
                pass

# ---- main bench logic -------------------------------------------------------

def build_backend(args: argparse.Namespace) -> Backend:
    if args.backend == "local":
        return LocalMemBackend()
    if args.backend == "sqlite":
        return SQLiteBackend(path=args.sqlite_path, fast=args.sqlite_fast)
    if args.backend == "cap":
        return CapabilitiesBackend(db_uri=args.cap_db_uri)
    raise SystemExit(f"Unknown backend {args.backend!r}")

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--backend", choices=("local", "sqlite", "cap"), default="local", help="which queue backend to benchmark")
    # Batch/config
    p.add_argument("--batch", type=int, default=512, help="operations per measured batch")
    p.add_argument("--iters", type=int, default=50, help="measured batches per repeat")
    p.add_argument("--repeat", type=int, default=3, help="number of timing repeats")
    p.add_argument("--warmup", type=int, default=50, help="unmeasured warmup batches")
    # Payload/caller settings
    p.add_argument("--payload-bytes", type=int, default=256, help="payload size in bytes")
    p.add_argument("--payload-min", type=int, default=None, help="if set, jitter payload size uniformly in [min, max]")
    p.add_argument("--payload-max", type=int, default=None, help="upper bound for jittered payload size")
    p.add_argument("--unique-caller", action="store_true", help="use a new random caller per op (otherwise fixed caller)")
    p.add_argument("--unique-txhash", action="store_true", help="use a new random tx hash per op (otherwise fixed tx hash)")
    # Chain/height
    p.add_argument("--chain-id", type=int, default=1337, help="chain id to bind into task-id")
    p.add_argument("--height", type=int, default=1, help="block height to bind into task-id")
    # SQLite tuning
    p.add_argument("--sqlite-path", type=str, default="bench_data/enqueue.sqlite", help="path for sqlite backend DB")
    p.add_argument("--sqlite-fast", action="store_true", help="use PRAGMA synchronous=OFF (less durable, faster)")
    # Capabilities backend
    p.add_argument("--cap-db-uri", type=str, default=None, help="optional db uri for capabilities backend JobQueue")
    # Output
    p.add_argument("--print-samples", action="store_true", help="print per-repeat average seconds (debug)")
    args = p.parse_args(argv)

    # Validate jitter args
    jitter = args.payload_min is not None and args.payload_max is not None
    if jitter:
        if not (0 <= args.payload_min <= args.payload_max):
            p.error("--payload-min must be <= --payload-max and both non-negative")

    backend = build_backend(args)
    env = bench_env()
    print(f"# enqueue_latency backend={backend.name} env={env}")

    rnd = random.Random(0xA11CE)

    fixed_caller = rand_bytes(32)
    fixed_tx = rand_bytes(32)

    def rand_payload() -> bytes:
        if jitter:
            n = rnd.randint(args.payload_min, args.payload_max)  # type: ignore[arg-type]
        else:
            n = args.payload_bytes
        return rand_bytes(n)

    def do_batch() -> None:
        for _ in range(args.batch):
            caller = rand_bytes(32) if args.unique_caller else fixed_caller
            txh = rand_bytes(32) if args.unique_txhash else fixed_tx
            backend.enqueue(args.chain_id, args.height, txh, caller, rand_payload())

    # Warmup
    warmup(do_batch, iters=max(0, int(args.warmup)))

    # Measure
    stats = run_bench(do_batch, iters=args.iters, repeat=args.repeat)
    if args.print_samples:
        print("# samples (seconds per batch):", [round(s, 6) for s in stats["samples"]])

    sec_per_batch = stats["avg"]
    sec_best = stats["best"]
    ops_per_batch = args.batch
    sec_per_op = sec_per_batch / ops_per_batch
    ops_per_sec = 1.0 / sec_per_op

    # Rough throughput estimate for hashing bytes (only payload, not metadata)
    avg_payload = (
        (args.payload_min + args.payload_max) / 2 if jitter else args.payload_bytes
    )
    bytes_per_sec = ops_per_sec * avg_payload
    mib_per_sec = bytes_per_sec / (1024 * 1024)

    print(
        "result:",
        {
            "backend": backend.name,
            "batch": args.batch,
            "iters": args.iters,
            "repeat": args.repeat,
            "avg_sec_per_batch": round(sec_per_batch, 6),
            "best_sec_per_batch": round(sec_best, 6),
            "avg_sec_per_op": round(sec_per_op, 9),
            "ops_per_sec": int(ops_per_sec),
            "approx_mib_per_sec_payload": round(mib_per_sec, 2),
            "payload_bytes": args.payload_bytes if not jitter else None,
            "payload_jitter": (args.payload_min, args.payload_max) if jitter else None,
            "unique_caller": bool(args.unique_caller),
            "unique_txhash": bool(args.unique_txhash),
            "chain_id": args.chain_id,
            "height": args.height,
        },
    )

    # Clean up
    try:
        backend.close()
    except Exception:
        pass

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
