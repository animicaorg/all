from __future__ import annotations

"""
Persistent job queue with an in-memory fast path.

- Primary store: SQLite (bundled with Python). WAL mode, idempotent enqueue.
- Optional: RocksDB read-through cache if python-rocksdb is available (graceful fallback).
- Deterministic IDs: derived via capabilities.jobs.id.derive_task_id (and stored as hex).
- Selection: highest priority first, then FIFO by enqueue time.
- Concurrency: pop_next uses a short IMMEDIATE transaction to atomically claim a job.

This module is intentionally self-contained and has zero hard non-stdlib deps.
"""

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Any, Dict, Optional, Tuple

try:  # pragma: no cover - optional dependency
    import rocksdb  # type: ignore
except Exception:  # noqa: BLE001
    rocksdb = None  # type: ignore

from capabilities.jobs.id import derive_task_id, derive_task_id_hex
from capabilities.jobs.types import JobKind, JobRequest, JobReceipt, ResultRecord

# Prefer canonical CBOR used across the project; fallback to stable JSON.
_CBOR_DUMPS = None
_CBOR_LOADS = None
try:  # pragma: no cover - exercised by higher-level tests
    from capabilities.cbor.codec import dumps as _CBOR_DUMPS, loads as _CBOR_LOADS  # type: ignore
except Exception:  # noqa: BLE001
    _CBOR_DUMPS = None  # type: ignore
    _CBOR_LOADS = None  # type: ignore


def _b_dumps(obj: Any) -> bytes:
    if _CBOR_DUMPS is not None:
        return _CBOR_DUMPS(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b_loads(b: bytes) -> Any:
    if _CBOR_LOADS is not None:
        return _CBOR_LOADS(b)
    return json.loads(b.decode("utf-8"))


class JobStatus:
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


_SQL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    task_id        TEXT PRIMARY KEY,              -- hex(sha3_256)
    kind           TEXT NOT NULL,                 -- JobKind name
    chain_id       INTEGER NOT NULL,
    height         INTEGER NOT NULL,
    tx_hash        BLOB NOT NULL,
    caller         BLOB NOT NULL,
    payload        BLOB NOT NULL,                 -- CBOR/JSON bytes
    priority       REAL NOT NULL DEFAULT 0,       -- higher first
    status         TEXT NOT NULL,                 -- JobStatus
    attempts       INTEGER NOT NULL DEFAULT 0,
    error          TEXT,                          -- last error (if FAILED)
    result         BLOB,                          -- CBOR/JSON-encoded ResultRecord
    enqueued_at_s  INTEGER NOT NULL,
    updated_at_s   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority_time
    ON jobs(status, priority DESC, enqueued_at_s ASC);

CREATE INDEX IF NOT EXISTS idx_jobs_kind_status
    ON jobs(kind, status);
"""


class _RocksCache:  # pragma: no cover - ancillary acceleration
    """Tiny helper to use RocksDB as a read-through cache for hot rows."""

    def __init__(self, path: str):
        self.enabled = bool(rocksdb)
        self.db = None
        if self.enabled:
            opts = rocksdb.Options()
            opts.create_if_missing = True
            opts.max_open_files = 64
            self.db = rocksdb.DB(path, opts)

    def get(self, key: str) -> Optional[bytes]:
        if not self.enabled or self.db is None:
            return None
        try:
            return self.db.get(key.encode("utf-8"))
        except Exception:
            return None

    def set(self, key: str, val: bytes) -> None:
        if not self.enabled or self.db is None:
            return
        try:
            self.db.put(key.encode("utf-8"), val)
        except Exception:
            pass

    def delete(self, key: str) -> None:
        if not self.enabled or self.db is None:
            return
        try:
            self.db.delete(key.encode("utf-8"))
        except Exception:
            pass


class JobQueue:
    """
    Persistent queue facade.

    Parameters
    ----------
    sqlite_path : str
        Filesystem path to the SQLite DB file (e.g., "capabilities_jobs.db").
    rocks_path : Optional[str]
        Optional path to a RocksDB directory for read-through caching.
    cache_size : int
        Max number of hot entries to keep in a simple in-process cache.
    """

    def __init__(self, sqlite_path: str, *, rocks_path: Optional[str] = None, cache_size: int = 1024):
        self.sqlite_path = sqlite_path
        self.conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)  # autocommit mode
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

        self._lock = threading.RLock()
        self._cache_size = max(64, int(cache_size))
        self._cache: Dict[str, Dict[str, Any]] = {}  # task_id -> row dict

        self._rocks = _RocksCache(rocks_path) if rocks_path else _RocksCache("")  # disabled if no path or no lib

    # -------------------- schema --------------------

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(_SQL_SCHEMA)

    # -------------------- helpers --------------------

    @staticmethod
    def _now() -> int:
        return int(time.time())

    def _cache_put(self, task_id: str, row: Dict[str, Any]) -> None:
        if len(self._cache) >= self._cache_size:
            # Drop an arbitrary item (dict pop FIFO-ish since Py3.7 preserves insertion order)
            try:
                self._cache.pop(next(iter(self._cache)))
            except StopIteration:
                self._cache.clear()
        self._cache[task_id] = row
        # mirror to rocks if configured
        try:
            self._rocks.set(task_id, _b_dumps(row))
        except Exception:
            pass

    def _cache_get(self, task_id: str) -> Optional[Dict[str, Any]]:
        hit = self._cache.get(task_id)
        if hit is not None:
            return hit
        raw = self._rocks.get(task_id)
        if raw:
            try:
                row = _b_loads(raw)
                # keep small to avoid ballooning memory
                self._cache_put(task_id, row)
                return row
            except Exception:
                return None
        return None

    # -------------------- API --------------------

    def enqueue(
        self,
        *,
        req: JobRequest,
        chain_id: int,
        height: int,
        tx_hash: bytes,
        caller: bytes,
        priority: float = 0.0,
    ) -> JobReceipt:
        """
        Enqueue a job. Idempotent by (task_id).

        Returns a JobReceipt. If the job already exists, returns a receipt for the
        existing row (status is not changed).
        """
        if not isinstance(req.kind, JobKind):
            raise ValueError("req.kind must be JobKind")
        if not isinstance(tx_hash, (bytes, bytearray)) or len(tx_hash) == 0:
            raise ValueError("tx_hash must be non-empty bytes")
        if not isinstance(caller, (bytes, bytearray)) or len(caller) == 0:
            raise ValueError("caller must be non-empty bytes")

        task_id_hex = derive_task_id_hex(
            chain_id=chain_id,
            height=height,
            tx_hash=bytes(tx_hash),
            caller=bytes(caller),
            payload=req.payload,
        )

        payload_b = _b_dumps(req.payload)
        now = self._now()

        with self._lock, self.conn:
            # Try insert; on conflict, keep the existing row.
            self.conn.execute(
                """
                INSERT INTO jobs (task_id, kind, chain_id, height, tx_hash, caller, payload,
                                  priority, status, attempts, enqueued_at_s, updated_at_s)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(task_id) DO NOTHING
                """,
                (
                    task_id_hex,
                    req.kind.name,
                    chain_id,
                    height,
                    sqlite3.Binary(tx_hash),
                    sqlite3.Binary(caller),
                    sqlite3.Binary(payload_b),
                    float(priority),
                    JobStatus.QUEUED,
                    now,
                    now,
                ),
            )

            # Fetch row to return canonical receipt
            row = self.conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id_hex,)).fetchone()
            if row is None:
                raise RuntimeError("enqueue failed to insert or fetch row")

            # update cache
            self._cache_put(task_id_hex, self._row_to_dict(row))

        return JobReceipt(task_id=bytes.fromhex(task_id_hex), chain_id=chain_id, height=height)

    def get(self, task_id_hex: str) -> Optional[Dict[str, Any]]:
        """Return the raw row dict for a task (or None)."""
        cached = self._cache_get(task_id_hex)
        if cached is not None:
            return cached
        row = self.conn.execute("SELECT * FROM jobs WHERE task_id = ?", (task_id_hex,)).fetchone()
        if row is None:
            return None
        d = self._row_to_dict(row)
        self._cache_put(task_id_hex, d)
        return d

    def pop_next(self, *, kind: Optional[JobKind] = None) -> Optional[Tuple[str, JobRequest]]:
        """
        Atomically claim the next job: set status=IN_PROGRESS and return (task_id_hex, JobRequest).

        Selection: status=QUEUED ORDER BY priority DESC, enqueued_at_s ASC LIMIT 1,
        optionally filtered by kind.
        """
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE;")
            try:
                if kind is None:
                    row = self.conn.execute(
                        """
                        SELECT * FROM jobs
                        WHERE status = ?
                        ORDER BY priority DESC, enqueued_at_s ASC
                        LIMIT 1
                        """,
                        (JobStatus.QUEUED,),
                    ).fetchone()
                else:
                    row = self.conn.execute(
                        """
                        SELECT * FROM jobs
                        WHERE status = ? AND kind = ?
                        ORDER BY priority DESC, enqueued_at_s ASC
                        LIMIT 1
                        """,
                        (JobStatus.QUEUED, kind.name),
                    ).fetchone()

                if row is None:
                    self.conn.execute("COMMIT;")
                    return None

                task_id = row["task_id"]
                self.conn.execute(
                    "UPDATE jobs SET status = ?, updated_at_s = ? WHERE task_id = ?",
                    (JobStatus.IN_PROGRESS, self._now(), task_id),
                )
                self.conn.execute("COMMIT;")
            except Exception:
                self.conn.execute("ROLLBACK;")
                raise

        req = JobRequest(kind=JobKind[row["kind"]], payload=_b_loads(row["payload"]))
        # Update caches
        self._cache_put(task_id, self._row_to_dict(row) | {"status": JobStatus.IN_PROGRESS})
        return task_id, req

    def requeue(self, task_id_hex: str, *, backoff_seconds: int = 0) -> None:
        """Move a task back to QUEUED, increment attempts."""
        now = self._now() + max(0, int(backoff_seconds))
        with self._lock, self.conn:
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, attempts = attempts + 1, updated_at_s = ?
                WHERE task_id = ?
                """,
                (JobStatus.QUEUED, now, task_id_hex),
            )
            if cur.rowcount == 0:
                raise KeyError(f"task not found: {task_id_hex}")
            self._invalidate_cache(task_id_hex)

    def complete(self, task_id_hex: str, result: ResultRecord) -> None:
        """Mark a task COMPLETED and store its result payload."""
        result_b = _b_dumps(asdict(result))
        with self._lock, self.conn:
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, updated_at_s = ?
                WHERE task_id = ?
                """,
                (JobStatus.COMPLETED, sqlite3.Binary(result_b), self._now(), task_id_hex),
            )
            if cur.rowcount == 0:
                raise KeyError(f"task not found: {task_id_hex}")
            self._invalidate_cache(task_id_hex)

    def fail(self, task_id_hex: str, error_msg: str) -> None:
        """Mark a task FAILED and record the last error."""
        with self._lock, self.conn:
            cur = self.conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, attempts = attempts + 1, updated_at_s = ?
                WHERE task_id = ?
                """,
                (JobStatus.FAILED, error_msg, self._now(), task_id_hex),
            )
            if cur.rowcount == 0:
                raise KeyError(f"task not found: {task_id_hex}")
            self._invalidate_cache(task_id_hex)

    def list(
        self,
        *,
        status: Optional[str] = None,
        kind: Optional[JobKind] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Dict[str, Any]]:
        """List jobs with optional filters."""
        q = "SELECT * FROM jobs"
        args: list[Any] = []
        conds: list[str] = []
        if status:
            conds.append("status = ?")
            args.append(status)
        if kind:
            conds.append("kind = ?")
            args.append(kind.name)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        q += " ORDER BY enqueued_at_s DESC, priority DESC LIMIT ? OFFSET ?"
        args.extend([int(limit), int(offset)])
        rows = self.conn.execute(q, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def stats(self) -> Dict[str, int]:
        """Counts by status."""
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as c FROM jobs GROUP BY status"
        ).fetchall()
        out: Dict[str, int] = {JobStatus.QUEUED: 0, JobStatus.IN_PROGRESS: 0, JobStatus.COMPLETED: 0, JobStatus.FAILED: 0, JobStatus.EXPIRED: 0}
        for r in rows:
            out[str(r["status"])] = int(r["c"])
        return out

    def delete(self, task_id_hex: str) -> None:
        """Remove a task (mainly for tests / GC of completed items if desired)."""
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM jobs WHERE task_id = ?", (task_id_hex,))
            self._invalidate_cache(task_id_hex)

    # -------------------- internals --------------------

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "task_id": row["task_id"],
            "kind": row["kind"],
            "chain_id": int(row["chain_id"]),
            "height": int(row["height"]),
            "tx_hash": bytes(row["tx_hash"]),
            "caller": bytes(row["caller"]),
            "payload": _b_loads(bytes(row["payload"])),
            "priority": float(row["priority"]),
            "status": row["status"],
            "attempts": int(row["attempts"]),
            "error": row["error"],
            "enqueued_at_s": int(row["enqueued_at_s"]),
            "updated_at_s": int(row["updated_at_s"]),
        }
        if row["result"] is not None:
            try:
                d["result"] = _b_loads(bytes(row["result"]))
            except Exception:
                d["result"] = None
        else:
            d["result"] = None
        return d

    def _invalidate_cache(self, task_id_hex: str) -> None:
        self._cache.pop(task_id_hex, None)
        try:
            self._rocks.delete(task_id_hex)
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            finally:
                self._cache.clear()


# -------------------- convenience helpers --------------------

def enqueue_and_receipt(
    queue: JobQueue,
    *,
    req: JobRequest,
    chain_id: int,
    height: int,
    tx_hash: bytes,
    caller: bytes,
    priority: float = 0.0,
) -> JobReceipt:
    """
    Helper for one-shot enqueue from a higher layer.

    Returns the JobReceipt using the same deterministic task-id the rest of the system will derive.
    """
    return queue.enqueue(
        req=req,
        chain_id=chain_id,
        height=height,
        tx_hash=tx_hash,
        caller=caller,
        priority=priority,
    )


__all__ = [
    "JobQueue",
    "JobStatus",
    "enqueue_and_receipt",
]
