from __future__ import annotations

"""
SQLite-backed FIFO queue with idempotency and safe leasing.

Design goals
------------
- FIFO with priority: higher `priority` dequeued first, then by `created_at`.
- Idempotent enqueue: if `idempotency_key` is supplied and a matching task
  exists, return that task instead of inserting a duplicate.
- Safe concurrent polling via short-lived leases. Workers call `poll()`, which
  atomically transitions a task from QUEUED -> RUNNING and sets a `lease_until`
  deadline. Expired leases can be re-queued by `requeue_expired_leases()`.
- Bounded retries with exponential backoff up to a cap, then FAIL.
- JSON payload/result stored canonically (UTF-8 text). This module does not
  attempt to interpret payloads; workers own the semantics.

Schema
------
This module expects a `queue` table. The repo's migrations create it, but for
reference the minimal compatible schema (SQLite) is:

CREATE TABLE IF NOT EXISTS queue (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  idempotency_key TEXT UNIQUE,
  payload TEXT NOT NULL,               -- JSON
  priority INTEGER NOT NULL DEFAULT 0, -- higher first
  status TEXT NOT NULL,                -- 'queued' | 'running' | 'done' | 'failed'
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 8,
  available_at INTEGER NOT NULL,       -- unix seconds
  lease_owner TEXT,                    -- worker id
  lease_until INTEGER,                 -- unix seconds
  result TEXT,                         -- JSON on success
  error TEXT,                          -- last error string on failure
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS queue_lookup_available
  ON queue(status, available_at, priority, created_at);
CREATE INDEX IF NOT EXISTS queue_kind_status
  ON queue(kind, status);
CREATE INDEX IF NOT EXISTS queue_lease_until
  ON queue(status, lease_until);

If your deployment uses a different schema file, ensure the columns above exist.
"""

import asyncio
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

try:
    import aiosqlite
except Exception:  # pragma: no cover - optional dependency
    import sqlite3

    class _AioSqliteShim:
        Row = sqlite3.Row
        Connection = sqlite3.Connection

        @staticmethod
        async def connect(*_, **__):
            raise ImportError("aiosqlite is required for SQLiteTaskQueue")

    aiosqlite = _AioSqliteShim()  # type: ignore


# --- Data model ----------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    payload: Any
    priority: int
    status: str
    attempts: int
    max_attempts: int
    available_at: int
    created_at: int
    updated_at: int
    idempotency_key: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_until: Optional[int] = None
    result: Optional[Any] = None
    error: Optional[str] = None

    @staticmethod
    def _loads(val: Optional[str]) -> Any:
        if val is None:
            return None
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val

    @classmethod
    def from_row(cls, row: sqlite3.Row | aiosqlite.Row) -> "Task":
        return cls(
            id=row["id"],
            kind=row["kind"],
            payload=cls._loads(row["payload"]),
            priority=row["priority"],
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            available_at=row["available_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            idempotency_key=row["idempotency_key"],
            lease_owner=row["lease_owner"],
            lease_until=row["lease_until"],
            result=cls._loads(row["result"]),
            error=row["error"],
        )


# --- Queue implementation -------------------------------------------------------


DEFAULT_MAX_ATTEMPTS = 8
BASE_BACKOFF_SECONDS = 3
MAX_BACKOFF_SECONDS = 300


def _now() -> int:
    return int(time.time())


def _canon_json(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _new_id() -> str:
    # 32 hex chars, URL-safe enough and unique for our purposes
    return secrets.token_hex(16)


class SQLiteTaskQueue:
    """
    Minimal, robust SQLite-backed task queue.

    This class manages its own SQLite connection (aiosqlite). For best
    throughput, reuse the same instance per process and call `await close()`
    on shutdown.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    @property
    def db_path(self) -> str:
        return self._db_path

    async def connect(self) -> None:
        if self._conn:
            return
        # Use autocommit mode so we can manage transactions manually (e.g. the
        # explicit BEGIN/COMMIT around poll()). SQLite's default implicitly
        # opens a transaction on the first write, which causes "cannot start a
        # transaction within a transaction" when we issue BEGIN IMMEDIATE
        # ourselves.
        conn = await aiosqlite.connect(self._db_path, isolation_level=None)
        conn.row_factory = aiosqlite.Row
        # Pragmas tuned for small, safe queues. Adjust if necessary at app level.
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA foreign_keys=ON;")
        await conn.execute("PRAGMA synchronous=NORMAL;")
        await conn.execute("PRAGMA temp_store=MEMORY;")
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # --- Enqueue / Inspect -----------------------------------------------------

    async def enqueue(
        self,
        *,
        kind: str,
        payload: Any,
        priority: int = 0,
        run_at: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Task:
        """
        Enqueue a task. If `idempotency_key` is provided and an existing task
        with the same key exists, return that row and do not insert a new one.
        """
        await self.connect()
        assert self._conn is not None

        now = _now()
        available_at = run_at if run_at is not None else now
        payload_json = _canon_json(payload)

        # Fast-path: idempotency check
        if idempotency_key:
            row = await self._conn.execute_fetchone(
                "SELECT * FROM queue WHERE idempotency_key = ?;",
                (idempotency_key,),
            )
            if row:
                return Task.from_row(row)

        # Insert new task
        task_id = _new_id()
        await self._conn.execute(
            """
            INSERT INTO queue (
              id, kind, idempotency_key, payload, priority, status,
              attempts, max_attempts, available_at, lease_owner, lease_until,
              result, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
            """,
            (
                task_id,
                kind,
                idempotency_key,
                payload_json,
                priority,
                max_attempts,
                available_at,
                now,
                now,
            ),
        )
        await self._conn.commit()
        row = await self._conn.execute_fetchone(
            "SELECT * FROM queue WHERE id = ?;", (task_id,)
        )
        assert row is not None
        return Task.from_row(row)

    async def get(self, task_id: str) -> Optional[Task]:
        await self.connect()
        assert self._conn is not None
        row = await self._conn.execute_fetchone(
            "SELECT * FROM queue WHERE id = ?;", (task_id,)
        )
        return Task.from_row(row) if row else None

    async def list(
        self,
        *,
        kinds: Optional[Sequence[str]] = None,
        statuses: Optional[Sequence[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        await self.connect()
        assert self._conn is not None
        where = []
        params: list[Any] = []
        if kinds:
            where.append("kind IN (%s)" % ",".join("?" for _ in kinds))
            params.extend(kinds)
        if statuses:
            where.append("status IN (%s)" % ",".join("?" for _ in statuses))
            params.extend(statuses)
        sql = "SELECT * FROM queue"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await self._conn.execute_fetchall(sql, params)
        return [Task.from_row(r) for r in rows]

    # --- Leases / Polling -----------------------------------------------------

    async def poll(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        kinds: Optional[Iterable[str]] = None,
    ) -> Optional[Task]:
        """
        Atomically claim the next available task for processing.
        Returns None if no task is available.

        Concurrency: Uses an IMMEDIATE transaction to avoid double-claiming.
        """
        await self.connect()
        assert self._conn is not None

        now = _now()
        lease_until = now + int(lease_seconds)

        # Serialize poll to reduce contention on the 'queue' lookup indexes.
        async with self._lock:
            await self._conn.execute("BEGIN IMMEDIATE;")

            # Choose candidate row
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                select_sql = f"""
                  SELECT id FROM queue
                  WHERE status = 'queued'
                    AND available_at <= ?
                    AND kind IN ({placeholders})
                  ORDER BY priority DESC, created_at ASC
                  LIMIT 1
                """
                params: list[Any] = [now, *list(kinds)]
            else:
                select_sql = """
                  SELECT id FROM queue
                  WHERE status = 'queued' AND available_at <= ?
                  ORDER BY priority DESC, created_at ASC
                  LIMIT 1
                """
                params = [now]

            row = await self._conn.execute_fetchone(select_sql, params)
            if not row:
                await self._conn.execute("COMMIT;")
                return None

            task_id = row["id"]

            # Attempt to transition to RUNNING with a lease
            cur = await self._conn.execute(
                """
                UPDATE queue SET
                  status = 'running',
                  attempts = attempts + 1,
                  lease_owner = ?,
                  lease_until = ?,
                  updated_at = ?
                WHERE id = ?
                  AND status = 'queued'
                  AND available_at <= ?
                """,
                (worker_id, lease_until, now, task_id, now),
            )
            if cur.rowcount == 0:
                # Raced; someone else claimed it. Try again.
                await self._conn.execute("ROLLBACK;")
                return None

            await self._conn.execute("COMMIT;")

        # Return the fresh row
        row = await self._conn.execute_fetchone(
            "SELECT * FROM queue WHERE id = ?;", (task_id,)
        )
        return Task.from_row(row) if row else None

    async def extend_lease(
        self, *, task_id: str, worker_id: str, add_seconds: int
    ) -> bool:
        await self.connect()
        assert self._conn is not None
        now = _now()
        new_until = now + int(add_seconds)
        cur = await self._conn.execute(
            """
            UPDATE queue SET lease_until = ?, updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (new_until, now, task_id, worker_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def requeue_expired_leases(self, *, limit: int = 100) -> int:
        """
        Return expired RUNNING tasks back to QUEUED so another worker can claim.
        """
        await self.connect()
        assert self._conn is not None
        now = _now()
        await self._conn.execute("BEGIN IMMEDIATE;")
        # SQLite lacks UPDATE ... RETURNING changes() in older versions, so use two-step.
        rows = await self._conn.execute_fetchall(
            """
            SELECT id FROM queue
            WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until < ?
            LIMIT ?
            """,
            (now, limit),
        )
        ids = [r["id"] for r in rows]
        if not ids:
            await self._conn.execute("COMMIT;")
            return 0
        placeholders = ",".join("?" for _ in ids)
        await self._conn.execute(
            f"""
            UPDATE queue SET
              status = 'queued',
              lease_owner = NULL,
              lease_until = NULL,
              updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (now, *ids),
        )
        await self._conn.execute("COMMIT;")
        return len(ids)

    # --- Completion / Retry ----------------------------------------------------

    async def ack_success(self, *, task_id: str, result: Any | None = None) -> bool:
        await self.connect()
        assert self._conn is not None
        now = _now()
        result_json = _canon_json(result) if result is not None else None
        cur = await self._conn.execute(
            """
            UPDATE queue SET
              status = 'done',
              result = ?,
              error = NULL,
              lease_owner = NULL,
              lease_until = NULL,
              updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (result_json, now, task_id),
        )
        await self._conn.commit()
        return cur.rowcount == 1

    async def ack_failure(
        self,
        *,
        task_id: str,
        error: str,
        backoff_base: int = BASE_BACKOFF_SECONDS,
        backoff_max: int = MAX_BACKOFF_SECONDS,
    ) -> bool:
        """
        Mark the task as failed for this attempt. If attempts < max_attempts,
        re-queue with exponential backoff; otherwise mark as permanently failed.
        """
        await self.connect()
        assert self._conn is not None
        now = _now()

        # Load attempts/max to decide path
        row = await self._conn.execute_fetchone(
            "SELECT attempts, max_attempts FROM queue WHERE id = ?;", (task_id,)
        )
        if not row:
            return False
        attempts = int(row["attempts"])
        max_attempts = int(row["max_attempts"])

        if attempts < max_attempts:
            # Re-queue with backoff (2^(attempts-1) * base), clipped.
            delay = min(backoff_max, backoff_base * (2 ** max(0, attempts - 1)))
            available_at = now + delay
            cur = await self._conn.execute(
                """
                UPDATE queue SET
                  status = 'queued',
                  error = ?,
                  lease_owner = NULL,
                  lease_until = NULL,
                  available_at = ?,
                  updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (error, available_at, now, task_id),
            )
        else:
            # Exhausted retries; mark failed.
            cur = await self._conn.execute(
                """
                UPDATE queue SET
                  status = 'failed',
                  error = ?,
                  lease_owner = NULL,
                  lease_until = NULL,
                  updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (error, now, task_id),
            )
        await self._conn.commit()
        return cur.rowcount == 1

    # --- Maintenance helpers ---------------------------------------------------

    async def purge_done_before(self, *, older_than_seconds: int) -> int:
        """
        Delete DONE/FAILED tasks older than `older_than_seconds`. Returns number deleted.
        """
        await self.connect()
        assert self._conn is not None
        now = _now()
        cutoff = now - int(older_than_seconds)
        cur = await self._conn.execute(
            """
            DELETE FROM queue
            WHERE (status = 'done' OR status = 'failed') AND updated_at < ?
            """,
            (cutoff,),
        )
        await self._conn.commit()
        return cur.rowcount or 0

    async def stats(self) -> dict[str, int]:
        """
        Quick counts by status for dashboards/metrics.
        """
        await self.connect()
        assert self._conn is not None
        out: dict[str, int] = {}
        for status in ("queued", "running", "done", "failed"):
            row = await self._conn.execute_fetchone(
                "SELECT COUNT(1) AS c FROM queue WHERE status = ?;", (status,)
            )
            out[status] = int(row["c"]) if row else 0
        return out


# Convenience factory that respects the app's configured DB path.
def create_queue_from_app(app) -> SQLiteTaskQueue:
    """
    Build a queue instance from a FastAPI app configured with studio-services config.

    Expects app.state.config or app.dependency_overrides to expose `db_path` via:
      - app.state.config.STORAGE_DIR/sqlite path, or
      - app.state.config.DB_PATH, or
      - app.state.config.db_path

    Fallback: uses 'studio_services.sqlite' in the current working directory.
    """
    db_path = None
    cfg = getattr(app.state, "config", None)
    for attr in ("DB_PATH", "db_path", "sqlite_path"):
        if cfg and getattr(cfg, attr, None):
            db_path = getattr(cfg, attr)
            break
    if not db_path and cfg and getattr(cfg, "STORAGE_DIR", None):
        db_path = str(getattr(cfg, "STORAGE_DIR")) + "/studio_services.sqlite"
    if not db_path:
        db_path = "studio_services.sqlite"
    return SQLiteTaskQueue(db_path=db_path)
