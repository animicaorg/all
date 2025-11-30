from __future__ import annotations

from aicf.queue.jobkind import JobKind

from .jobkind import JobKind

"""
Persistent job queue storage for AICF.

This module provides a production-ready SQLite-backed queue with a clean
interface and a optional RocksDB key/value fallback (best-effort) for
embedded deployments. The SQLite backend is the default and recommended
choice due to robust indexing and range queries required by scheduling.

Design goals
------------
- Idempotent enqueue using caller-provided job_id (deterministic).
- Deterministic ordering: primarily by priority (desc), then by created_at.
- Safe leasing: assign + lease expiry with atomic state transitions.
- Efficient queries via covering indexes (status/kind/not_before/priority).
- Graceful concurrency with WAL & immediate transactions.
- JSON columns for flexible specs/results while keeping hot fields indexed.

Schema (SQLite)
---------------
TABLE jobs(
  job_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,                 -- QUEUED|ASSIGNED|COMPLETED|FAILED|EXPIRED|CANCELED|TOMBSTONED
  priority REAL NOT NULL,               -- higher first
  created_at INTEGER NOT NULL,          -- epoch seconds
  updated_at INTEGER NOT NULL,
  not_before INTEGER NOT NULL DEFAULT 0,-- defer scheduling until this time
  ttl_seconds INTEGER NOT NULL,         -- lifetime from created_at (for expiry)
  max_attempts INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  requester TEXT NOT NULL,              -- address or id
  assigned_to TEXT,                     -- ProviderId when ASSIGNED
  lease_expires_at INTEGER,             -- epoch seconds
  lease_id TEXT,                        -- opaque token
  spec_json TEXT NOT NULL,              -- serialized JobSpec
  metadata_json TEXT,                   -- optional
  result_json TEXT,                     -- on completion
  error TEXT                            -- on failure
);
Useful indexes:
  (status, kind, not_before, priority DESC, created_at ASC)
  (lease_expires_at) for expiry scans
  (requester) for per-requester queries
"""

import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, is_dataclass
from typing import (Any, Dict, Iterable, List, Optional, Protocol, Sequence,
                    Tuple)

from aicf.aitypes.job import JobKind, JobRecord, JobStatus, Lease
from aicf.aitypes.provider import ProviderId
from aicf.errors import AICFError

# ---------------------------
# Interface
# ---------------------------


class JobQueueStorage(Protocol):
    def enqueue(self, job: JobRecord) -> None: ...
    def get(self, job_id: str) -> Optional[JobRecord]: ...
    def list_ready(
        self, *, kind: Optional[JobKind] = None, limit: int = 100
    ) -> List[JobRecord]: ...
    def assign(self, job_id: str, provider: ProviderId, lease_secs: int) -> Lease: ...
    def renew_lease(self, job_id: str, lease_secs: int) -> Lease: ...
    def complete(
        self, job_id: str, result: Optional[Dict[str, Any]] = None
    ) -> None: ...
    def fail(self, job_id: str, *, error: str, retryable: bool) -> None: ...
    def requeue(
        self,
        job_id: str,
        *,
        priority: Optional[float] = None,
        not_before: Optional[int] = None,
    ) -> None: ...
    def cancel(self, job_id: str) -> None: ...
    def tombstone(self, job_id: str) -> None: ...
    def expire(self, *, now: Optional[int] = None) -> int: ...
    def count_by_status(self) -> Dict[str, int]: ...
    def list_assigned_to(
        self, provider: ProviderId, limit: int = 100
    ) -> List[JobRecord]: ...
    def close(self) -> None: ...


# ---------------------------
# Helpers
# ---------------------------


def _json_dumps(obj: Any) -> str:
    if is_dataclass(obj):
        obj = asdict(obj)
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _json_loads(s: Optional[str]) -> Any:
    if not s:
        return None
    return json.loads(s)


def _utcnow() -> int:
    return int(time.time())


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    spec = _json_loads(row["spec_json"]) or {}
    meta = _json_loads(row["metadata_json"]) or None
    result = _json_loads(row["result_json"]) or None
    lease: Optional[Lease] = None
    if row["assigned_to"] and row["lease_expires_at"]:
        lease = Lease(
            lease_id=row["lease_id"] or "",
            provider_id=ProviderId(row["assigned_to"]),
            expires_at=int(row["lease_expires_at"]),
        )

    return JobRecord(
        job_id=row["job_id"],
        kind=JobKind(row["kind"]),
        status=JobStatus(row["status"]),
        priority=float(row["priority"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        not_before=int(row["not_before"]),
        ttl_seconds=int(row["ttl_seconds"]),
        max_attempts=int(row["max_attempts"]),
        attempts=int(row["attempts"]),
        requester=row["requester"],
        lease=lease,
        spec=spec,
        metadata=meta,
        result=result,
        error=row["error"],
    )


# ---------------------------
# SQLite implementation
# ---------------------------


class SQLiteJobQueueStorage:
    """
    SQLite-backed implementation.

    Parameters
    ----------
    path : str
        SQLite database file path. Use ':memory:' for in-memory (tests).
    pragmas : Sequence[Tuple[str, Any]]
        Extra PRAGMAs. WAL and sensible defaults are applied automatically.
    """

    def __init__(
        self, path: str, pragmas: Optional[Sequence[Tuple[str, Any]]] = None
    ) -> None:
        self.path = path
        self._conn = sqlite3.connect(
            path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # autocommit; we manage BEGIN IMMEDIATE
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas(pragmas)
        self._migrate()

    def _apply_pragmas(self, pragmas: Optional[Sequence[Tuple[str, Any]]]) -> None:
        cur = self._conn.cursor()
        defaults: Sequence[Tuple[str, Any]] = (
            ("journal_mode", "WAL"),
            ("synchronous", "NORMAL"),
            ("temp_store", "MEMORY"),
            ("mmap_size", 64 * 1024 * 1024),
            ("cache_size", -64 * 1024),  # 64MB
            ("foreign_keys", 1),
        )
        for name, val in defaults:
            cur.execute(f"PRAGMA {name} = {val}")
        if pragmas:
            for name, val in pragmas:
                cur.execute(f"PRAGMA {name} = {val}")

    def _migrate(self) -> None:
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs(
              job_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              priority REAL NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              not_before INTEGER NOT NULL DEFAULT 0,
              ttl_seconds INTEGER NOT NULL,
              max_attempts INTEGER NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              requester TEXT NOT NULL,
              assigned_to TEXT,
              lease_expires_at INTEGER,
              lease_id TEXT,
              spec_json TEXT NOT NULL,
              metadata_json TEXT,
              result_json TEXT,
              error TEXT
            )
            """
        )
        # Covering indexes for the hot paths
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_ready
            ON jobs(status, kind, not_before, priority DESC, created_at ASC)
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_lease_expires ON jobs(lease_expires_at)"
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_requester ON jobs(requester)")

    # ---- transaction helper
    def _begin(self) -> sqlite3.Cursor:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        return cur

    # ---- public API

    def enqueue(self, job: JobRecord) -> None:
        now = _utcnow()
        row = {
            "job_id": job.job_id,
            "kind": job.kind.value if hasattr(job.kind, "value") else str(job.kind),
            "status": (
                job.status.value if hasattr(job.status, "value") else str(job.status)
            ),
            "priority": job.priority,
            "created_at": job.created_at or now,
            "updated_at": now,
            "not_before": job.not_before or 0,
            "ttl_seconds": job.ttl_seconds,
            "max_attempts": job.max_attempts,
            "attempts": job.attempts or 0,
            "requester": job.requester,
            "assigned_to": job.lease.provider_id if job.lease else None,
            "lease_expires_at": job.lease.expires_at if job.lease else None,
            "lease_id": job.lease.lease_id if job.lease else None,
            "spec_json": _json_dumps(job.spec),
            "metadata_json": (
                _json_dumps(job.metadata) if job.metadata is not None else None
            ),
            "result_json": _json_dumps(job.result) if job.result is not None else None,
            "error": job.error,
        }
        cur = self._begin()
        try:
            cur.execute(
                """
                INSERT INTO jobs(
                  job_id, kind, status, priority, created_at, updated_at, not_before,
                  ttl_seconds, max_attempts, attempts, requester,
                  assigned_to, lease_expires_at, lease_id,
                  spec_json, metadata_json, result_json, error
                ) VALUES(
                  :job_id, :kind, :status, :priority, :created_at, :updated_at, :not_before,
                  :ttl_seconds, :max_attempts, :attempts, :requester,
                  :assigned_to, :lease_expires_at, :lease_id,
                  :spec_json, :metadata_json, :result_json, :error
                )
                """,
                row,
            )
            cur.execute("COMMIT")
        except sqlite3.IntegrityError:
            cur.execute("ROLLBACK")
            # Idempotency: update if same job_id exists but still QUEUED
            existing = self.get(job.job_id)
            if existing:
                return
            raise

    def get(self, job_id: str) -> Optional[JobRecord]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        return _row_to_job(row) if row else None

    def list_ready(
        self, *, kind: Optional[JobKind] = None, limit: int = 100
    ) -> List[JobRecord]:
        now = _utcnow()
        cur = self._conn.cursor()
        if kind is None:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'QUEUED' AND not_before <= ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (now, int(limit)),
            )
        else:
            cur.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'QUEUED' AND kind = ? AND not_before <= ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (kind.value if hasattr(kind, "value") else str(kind), now, int(limit)),
            )
        return [_row_to_job(r) for r in cur.fetchall()]

    def assign(self, job_id: str, provider: ProviderId, lease_secs: int) -> Lease:
        now = _utcnow()
        lease_exp = now + int(lease_secs)
        lease_id = str(uuid.uuid4())
        cur = self._begin()
        try:
            # only transition from QUEUED -> ASSIGNED atomically
            cur.execute(
                """
                UPDATE jobs
                SET status = 'ASSIGNED',
                    assigned_to = ?,
                    lease_expires_at = ?,
                    lease_id = ?,
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE job_id = ?
                  AND status = 'QUEUED'
                """,
                (str(provider), lease_exp, lease_id, now, job_id),
            )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot assign job {job_id}: not in QUEUED state")
            cur.execute("COMMIT")
            return Lease(lease_id=lease_id, provider_id=provider, expires_at=lease_exp)
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def renew_lease(self, job_id: str, lease_secs: int) -> Lease:
        now = _utcnow()
        lease_exp = now + int(lease_secs)
        cur = self._begin()
        try:
            cur.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND status = 'ASSIGNED'
                """,
                (lease_exp, now, job_id),
            )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot renew lease: job {job_id} not ASSIGNED")
            # fetch provider & lease_id for return
            cur.execute(
                "SELECT assigned_to, lease_id FROM jobs WHERE job_id = ?", (job_id,)
            )
            r = cur.fetchone()
            cur.execute("COMMIT")
            return Lease(
                lease_id=r["lease_id"],
                provider_id=ProviderId(r["assigned_to"]),
                expires_at=lease_exp,
            )
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def complete(self, job_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        now = _utcnow()
        cur = self._begin()
        try:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'COMPLETED',
                    result_json = ?,
                    updated_at = ?,
                    assigned_to = NULL,
                    lease_expires_at = NULL,
                    lease_id = NULL
                WHERE job_id = ? AND status IN ('ASSIGNED','QUEUED')
                """,
                (_json_dumps(result) if result is not None else None, now, job_id),
            )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot complete job {job_id}: not ASSIGNED/QUEUED")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def fail(self, job_id: str, *, error: str, retryable: bool) -> None:
        now = _utcnow()
        new_status = "QUEUED" if retryable else "FAILED"
        cur = self._begin()
        try:
            if retryable:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'QUEUED',
                        error = ?,
                        assigned_to = NULL,
                        lease_expires_at = NULL,
                        lease_id = NULL,
                        updated_at = ?
                    WHERE job_id = ? AND status IN ('ASSIGNED','QUEUED')
                    """,
                    (error, now, job_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE jobs
                    SET status = 'FAILED',
                        error = ?,
                        assigned_to = NULL,
                        lease_expires_at = NULL,
                        lease_id = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (error, now, job_id),
                )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot set {new_status} for job {job_id}")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def requeue(
        self,
        job_id: str,
        *,
        priority: Optional[float] = None,
        not_before: Optional[int] = None,
    ) -> None:
        now = _utcnow()
        fields = [
            "status = 'QUEUED'",
            "assigned_to = NULL",
            "lease_expires_at = NULL",
            "lease_id = NULL",
            "updated_at = ?",
        ]
        values: List[Any] = [now]
        if priority is not None:
            fields.append("priority = ?")
            values.append(float(priority))
        if not_before is not None:
            fields.append("not_before = ?")
            values.append(int(not_before))
        set_clause = ", ".join(fields)
        values.append(job_id)

        cur = self._begin()
        try:
            cur.execute(f"UPDATE jobs SET {set_clause} WHERE job_id = ?", values)
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot requeue job {job_id}")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def cancel(self, job_id: str) -> None:
        now = _utcnow()
        cur = self._begin()
        try:
            cur.execute(
                "UPDATE jobs SET status = 'CANCELED', updated_at = ?, assigned_to=NULL, lease_expires_at=NULL, lease_id=NULL WHERE job_id = ?",
                (now, job_id),
            )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot cancel job {job_id}")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def tombstone(self, job_id: str) -> None:
        now = _utcnow()
        cur = self._begin()
        try:
            cur.execute(
                "UPDATE jobs SET status = 'TOMBSTONED', updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            if cur.rowcount != 1:
                cur.execute("ROLLBACK")
                raise AICFError(f"cannot tombstone job {job_id}")
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def expire(self, *, now: Optional[int] = None) -> int:
        """
        Mark jobs EXPIRED when:
          - created_at + ttl_seconds < now (hard TTL), or
          - status == ASSIGNED and lease_expires_at < now (lease timeout).
        Returns number of rows updated.
        """
        ts = _utcnow() if now is None else int(now)
        cur = self._begin()
        try:
            cur.execute(
                """
                UPDATE jobs
                SET status = 'EXPIRED',
                    assigned_to = NULL,
                    lease_expires_at = NULL,
                    lease_id = NULL,
                    updated_at = ?
                WHERE
                  (created_at + ttl_seconds) < ?
                  AND status IN ('QUEUED','ASSIGNED')
                """,
                (ts, ts),
            )
            n1 = cur.rowcount
            cur.execute(
                """
                UPDATE jobs
                SET status = 'QUEUED',
                    assigned_to = NULL,
                    lease_expires_at = NULL,
                    lease_id = NULL,
                    updated_at = ?
                WHERE
                  status = 'ASSIGNED'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (ts, ts),
            )
            n2 = cur.rowcount
            cur.execute("COMMIT")
            return (n1 or 0) + (n2 or 0)
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def count_by_status(self) -> Dict[str, int]:
        cur = self._conn.cursor()
        cur.execute("SELECT status, COUNT(*) as c FROM jobs GROUP BY status")
        return {row["status"]: int(row["c"]) for row in cur.fetchall()}

    def list_assigned_to(
        self, provider: ProviderId, limit: int = 100
    ) -> List[JobRecord]:
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'ASSIGNED' AND assigned_to = ?
            ORDER BY lease_expires_at ASC
            LIMIT ?
            """,
            (str(provider), int(limit)),
        )
        return [_row_to_job(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


# ---------------------------
# Optional RocksDB (KV) implementation
# ---------------------------


class RocksJobQueueStorage:
    """
    Minimal RocksDB-backed storage.

    Notes:
      - Stores each job as a single JSON blob under key b"job:<id>".
      - Maintains secondary sets (by status/kind) as prefix lists b"ix:<field>:<value>:<id>".
      - Only recommended if python-rocksdb is available and range scans are acceptable.
    """

    def __init__(self, path: str) -> None:
        try:
            import rocksdb  # type: ignore
        except Exception as e:  # pragma: no cover - dependency optional
            raise RuntimeError("python-rocksdb not available") from e

        self._rocksdb = rocksdb
        opts = rocksdb.Options()
        opts.create_if_missing = True
        opts.max_open_files = 512
        self._db = rocksdb.DB(path, opts)

    # Helper keys
    @staticmethod
    def _k_job(job_id: str) -> bytes:
        return f"job:{job_id}".encode()

    @staticmethod
    def _k_ix(field: str, value: str, job_id: str) -> bytes:
        # index key ordering allows prefix iteration
        return f"ix:{field}:{value}:{job_id}".encode()

    def _put_indexes(self, batch, job: Dict[str, Any]) -> None:
        batch.put(self._k_ix("status", job["status"], job["job_id"]), b"")
        batch.put(self._k_ix("kind", job["kind"], job["job_id"]), b"")

    def _del_indexes(self, batch, job: Dict[str, Any]) -> None:
        batch.delete(self._k_ix("status", job["status"], job["job_id"]))
        batch.delete(self._k_ix("kind", job["kind"], job["job_id"]))

    # API (subset, used for tests/dev environments where RocksDB is preferred)
    def enqueue(self, job: JobRecord) -> None:
        j = {
            "job_id": job.job_id,
            "kind": job.kind.value if hasattr(job.kind, "value") else str(job.kind),
            "status": (
                job.status.value if hasattr(job.status, "value") else str(job.status)
            ),
            "priority": float(job.priority),
            "created_at": int(job.created_at or _utcnow()),
            "updated_at": int(_utcnow()),
            "not_before": int(job.not_before or 0),
            "ttl_seconds": int(job.ttl_seconds),
            "max_attempts": int(job.max_attempts),
            "attempts": int(job.attempts or 0),
            "requester": job.requester,
            "assigned_to": job.lease.provider_id if job.lease else None,
            "lease_expires_at": job.lease.expires_at if job.lease else None,
            "lease_id": job.lease.lease_id if job.lease else None,
            "spec": job.spec,
            "metadata": job.metadata,
            "result": job.result,
            "error": job.error,
        }
        batch = self._rocksdb.WriteBatch()
        key = self._k_job(job.job_id)
        if self._db.get(key) is not None:  # idempotent
            return
        batch.put(key, _json_dumps(j).encode())
        self._put_indexes(batch, j)
        self._db.write(batch)

    def get(self, job_id: str) -> Optional[JobRecord]:
        raw = self._db.get(self._k_job(job_id))
        if raw is None:
            return None
        j = _json_loads(raw.decode())
        lease: Optional[Lease] = None
        if j.get("assigned_to") and j.get("lease_expires_at"):
            lease = Lease(
                lease_id=j.get("lease_id") or "",
                provider_id=ProviderId(j["assigned_to"]),
                expires_at=int(j["lease_expires_at"]),
            )
        return JobRecord(
            job_id=j["job_id"],
            kind=JobKind(j["kind"]),
            status=JobStatus(j["status"]),
            priority=float(j["priority"]),
            created_at=int(j["created_at"]),
            updated_at=int(j["updated_at"]),
            not_before=int(j["not_before"]),
            ttl_seconds=int(j["ttl_seconds"]),
            max_attempts=int(j["max_attempts"]),
            attempts=int(j["attempts"]),
            requester=j["requester"],
            lease=lease,
            spec=j.get("spec") or {},
            metadata=j.get("metadata"),
            result=j.get("result"),
            error=j.get("error"),
        )

    # For brevity, the RocksDB backend implements only basic operations.
    # Advanced scheduling (ready/assign/expire) is handled in-memory by the
    # scheduler that reads/writes jobs via get/enqueue/complete/fail/requeue.

    def list_ready(
        self, *, kind: Optional[JobKind] = None, limit: int = 100
    ) -> List[JobRecord]:
        # Inefficient scan, acceptable for small dev deployments
        it = self._db.iterkeys()
        it.seek(b"job:")
        out: List[JobRecord] = []
        now = _utcnow()
        for k in it:
            if not k.startswith(b"job:"):
                break
            j = _json_loads(self._db.get(k).decode())
            if j["status"] == "QUEUED" and j["not_before"] <= now:
                if kind is None or j["kind"] == (
                    kind.value if hasattr(kind, "value") else str(kind)
                ):
                    out.append(self.get(j["job_id"]))  # type: ignore
            if len(out) >= limit:
                break
        # sort like SQLite
        out.sort(key=lambda r: (-r.priority, r.created_at))
        return out

    def assign(self, job_id: str, provider: ProviderId, lease_secs: int) -> Lease:
        j = self.get(job_id)
        if not j or j.status != JobStatus.QUEUED:
            raise AICFError(f"cannot assign job {job_id}")
        lease_id = str(uuid.uuid4())
        lease_exp = _utcnow() + int(lease_secs)
        j.status = JobStatus.ASSIGNED
        j.attempts += 1
        j.lease = Lease(lease_id=lease_id, provider_id=provider, expires_at=lease_exp)
        j.updated_at = _utcnow()
        self._overwrite(j)
        return j.lease  # type: ignore

    def _overwrite(self, job: JobRecord) -> None:
        # Update JSON atomically
        j = {
            "job_id": job.job_id,
            "kind": job.kind.value if hasattr(job.kind, "value") else str(job.kind),
            "status": (
                job.status.value if hasattr(job.status, "value") else str(job.status)
            ),
            "priority": float(job.priority),
            "created_at": int(job.created_at),
            "updated_at": int(job.updated_at),
            "not_before": int(job.not_before),
            "ttl_seconds": int(job.ttl_seconds),
            "max_attempts": int(job.max_attempts),
            "attempts": int(job.attempts),
            "requester": job.requester,
            "assigned_to": job.lease.provider_id if job.lease else None,
            "lease_expires_at": job.lease.expires_at if job.lease else None,
            "lease_id": job.lease.lease_id if job.lease else None,
            "spec": job.spec,
            "metadata": job.metadata,
            "result": job.result,
            "error": job.error,
        }
        batch = self._rocksdb.WriteBatch()
        key = self._k_job(job.job_id)
        old_raw = self._db.get(key)
        if old_raw:
            old = _json_loads(old_raw.decode())
            # update indexes if status or kind changed
            if old.get("status") != j["status"] or old.get("kind") != j["kind"]:
                self._del_indexes(batch, old)
                self._put_indexes(batch, j)
        batch.put(key, _json_dumps(j).encode())
        self._db.write(batch)

    def renew_lease(self, job_id: str, lease_secs: int) -> Lease:
        j = self.get(job_id)
        if not j or j.status != JobStatus.ASSIGNED or not j.lease:
            raise AICFError(f"cannot renew lease for {job_id}")
        j.lease = Lease(
            lease_id=j.lease.lease_id,
            provider_id=j.lease.provider_id,
            expires_at=_utcnow() + int(lease_secs),
        )
        j.updated_at = _utcnow()
        self._overwrite(j)
        return j.lease

    def complete(self, job_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        j = self.get(job_id)
        if not j:
            raise AICFError(f"unknown job {job_id}")
        j.status = JobStatus.COMPLETED
        j.result = result
        j.lease = None
        j.updated_at = _utcnow()
        self._overwrite(j)

    def fail(self, job_id: str, *, error: str, retryable: bool) -> None:
        j = self.get(job_id)
        if not j:
            raise AICFError(f"unknown job {job_id}")
        j.error = error
        j.status = JobStatus.QUEUED if retryable else JobStatus.FAILED
        if not retryable:
            j.lease = None
        j.updated_at = _utcnow()
        self._overwrite(j)

    def requeue(
        self,
        job_id: str,
        *,
        priority: Optional[float] = None,
        not_before: Optional[int] = None,
    ) -> None:
        j = self.get(job_id)
        if not j:
            raise AICFError(f"unknown job {job_id}")
        j.status = JobStatus.QUEUED
        j.lease = None
        if priority is not None:
            j.priority = float(priority)
        if not_before is not None:
            j.not_before = int(not_before)
        j.updated_at = _utcnow()
        self._overwrite(j)

    def cancel(self, job_id: str) -> None:
        j = self.get(job_id)
        if not j:
            raise AICFError(f"unknown job {job_id}")
        j.status = JobStatus.CANCELED
        j.lease = None
        j.updated_at = _utcnow()
        self._overwrite(j)

    def tombstone(self, job_id: str) -> None:
        j = self.get(job_id)
        if not j:
            raise AICFError(f"unknown job {job_id}")
        j.status = JobStatus.TOMBSTONED
        j.updated_at = _utcnow()
        self._overwrite(j)

    def expire(self, *, now: Optional[int] = None) -> int:
        # Cheap implementation: linear scan
        ts = _utcnow() if now is None else int(now)
        it = self._db.iterkeys()
        it.seek(b"job:")
        n = 0
        for k in it:
            if not k.startswith(b"job:"):
                break
            j = _json_loads(self._db.get(k).decode())
            changed = False
            if j["status"] in ("QUEUED", "ASSIGNED") and (
                j["created_at"] + j["ttl_seconds"] < ts
            ):
                j["status"] = "EXPIRED"
                j["assigned_to"] = None
                j["lease_expires_at"] = None
                j["lease_id"] = None
                j["updated_at"] = ts
                changed = True
            if (
                j["status"] == "ASSIGNED"
                and j.get("lease_expires_at")
                and j["lease_expires_at"] < ts
            ):
                j["status"] = "QUEUED"
                j["assigned_to"] = None
                j["lease_expires_at"] = None
                j["lease_id"] = None
                j["updated_at"] = ts
                changed = True
            if changed:
                n += 1
                self._overwrite(self.get(j["job_id"]))  # type: ignore
        return n

    def count_by_status(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        it = self._db.iterkeys()
        it.seek(b"job:")
        for k in it:
            if not k.startswith(b"job:"):
                break
            j = _json_loads(self._db.get(k).decode())
            counts[j["status"]] = counts.get(j["status"], 0) + 1
        return counts

    def list_assigned_to(
        self, provider: ProviderId, limit: int = 100
    ) -> List[JobRecord]:
        out: List[JobRecord] = []
        it = self._db.iterkeys()
        it.seek(b"job:")
        for k in it:
            if not k.startswith(b"job:"):
                break
            j = _json_loads(self._db.get(k).decode())
            if j["status"] == "ASSIGNED" and j.get("assigned_to") == str(provider):
                out.append(self.get(j["job_id"]))  # type: ignore
            if len(out) >= limit:
                break
        out.sort(key=lambda r: (r.lease.expires_at if r.lease else 0))  # type: ignore
        return out

    def close(self) -> None:
        # rocksdb DB has no explicit close; let GC handle it
        pass


# ---------------------------
# Factory
# ---------------------------


def open_storage(url: str) -> JobQueueStorage:
    """
    Open a job queue storage backend.

    URL forms:
      - sqlite:///absolute/path/to/aicf_queue.db
      - sqlite:///:memory:
      - rocksdb:///absolute/path/to/aicf_queue.rocks

    Returns an instance implementing JobQueueStorage.
    """
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///") :]
        return SQLiteJobQueueStorage(path)
    if url.startswith("sqlite:///:memory:"):
        return SQLiteJobQueueStorage(":memory:")
    if url.startswith("rocksdb:///"):
        path = url[len("rocksdb:///") :]
        return RocksJobQueueStorage(path)
    # bare path fallback -> sqlite
    return SQLiteJobQueueStorage(url)


__all__ = [
    "JobQueueStorage",
    "SQLiteJobQueueStorage",
    "RocksJobQueueStorage",
    "open_storage",
]
