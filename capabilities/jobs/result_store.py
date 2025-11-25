from __future__ import annotations

"""
capabilities.jobs.result_store
------------------------------

KV store for **ResultRecord** objects keyed by `task_id` (bytes).
Two implementations are provided:

- MemoryResultStore: in-process dict with lightweight secondary indexes.
- SqliteResultStore: persistent store using the stdlib `sqlite3` module.

Records are stored in canonical CBOR form (falling back to stable JSON if the
CBOR codec is unavailable). The SQLite backend also duplicates a few indexed
fields (caller, kind, chain_id, height, created_at) for efficient queries.

This module avoids heavy dependencies and keeps I/O deterministic.
"""

from dataclasses import asdict, is_dataclass
from typing import Iterable, List, Optional, Protocol, Tuple, Union
import json
import os
import re
import sqlite3
import threading
import time

from capabilities.errors import CapError
from capabilities.jobs.types import ResultRecord, JobKind

# -----------------------------------------------------------------------------
# Canonical serialization helpers (CBOR-first, JSON fallback)
# -----------------------------------------------------------------------------

try:  # Prefer project-wide canonical CBOR
    from capabilities.cbor.codec import dumps as _CBOR_DUMPS, loads as _CBOR_LOADS  # type: ignore
except Exception:  # pragma: no cover
    _CBOR_DUMPS = None  # type: ignore
    _CBOR_LOADS = None  # type: ignore


def _canon_dumps(obj) -> bytes:
    if _CBOR_DUMPS is not None:
        return _CBOR_DUMPS(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _canon_loads(b: bytes):
    if _CBOR_LOADS is not None:
        return _CBOR_LOADS(b)
    return json.loads(b.decode("utf-8"))


def _record_to_map(rec: ResultRecord) -> dict:
    # Prefer a structured map if the type exposes it.
    to_map = getattr(rec, "to_map", None)
    if callable(to_map):
        return to_map()
    if is_dataclass(rec):
        return asdict(rec)
    # Fall back to shallow dict of attributes (best-effort)
    return {k: getattr(rec, k) for k in dir(rec) if not k.startswith("_") and not callable(getattr(rec, k))}


def _map_to_record(m: dict) -> ResultRecord:
    # Prefer classmethod constructor if available.
    from_map = getattr(ResultRecord, "from_map", None)
    if callable(from_map):
        return from_map(m)  # type: ignore[return-value]
    # Last resort: attempt direct kwargs (works for simple dataclasses)
    return ResultRecord(**m)  # type: ignore[arg-type]


def _record_to_bytes(rec: ResultRecord) -> bytes:
    return _canon_dumps(_record_to_map(rec))


def _bytes_to_record(b: bytes) -> ResultRecord:
    return _map_to_record(_canon_loads(b))


# -----------------------------------------------------------------------------
# Store protocol & factory
# -----------------------------------------------------------------------------

class ResultStore(Protocol):
    def put(self, rec: ResultRecord) -> None: ...
    def get(self, task_id: bytes) -> Optional[ResultRecord]: ...
    def has(self, task_id: bytes) -> bool: ...
    def delete(self, task_id: bytes) -> bool: ...
    def list_recent(self, limit: int = 50, offset: int = 0) -> List[ResultRecord]: ...
    def list_by_caller(self, caller: bytes, limit: int = 50, offset: int = 0) -> List[ResultRecord]: ...
    def close(self) -> None: ...


def open_result_store(url: str) -> ResultStore:
    """
    Open a result store from a URL.

    Supported:
      - "memory:" → in-memory store
      - "sqlite:///:memory:" → SQLite in-memory
      - "sqlite:///path/to/results.db" → SQLite file
      - "file:/path/to/results.db" → alias for sqlite
    """
    if url.startswith("memory:"):
        return MemoryResultStore()

    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///") :]
        return SqliteResultStore(path)
    if url == "sqlite:///:memory:":
        return SqliteResultStore(":memory:")
    if url.startswith("file:"):
        path = url[len("file:") :]
        return SqliteResultStore(path)

    raise CapError(f"unsupported result-store URL: {url}")


# -----------------------------------------------------------------------------
# Memory store
# -----------------------------------------------------------------------------

class MemoryResultStore(ResultStore):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[bytes, ResultRecord] = {}
        self._by_caller: dict[bytes, set[bytes]] = {}  # caller → {task_id}
        self._by_height: dict[int, set[bytes]] = {}    # height → {task_id}
        self._created_at: dict[bytes, int] = {}        # task_id → created_at

    def put(self, rec: ResultRecord) -> None:
        with self._lock:
            m = _record_to_map(rec)
            task_id: bytes = m.get("task_id") or getattr(rec, "task_id")
            caller: bytes = m.get("caller") or getattr(rec, "caller")
            height: int = int(m.get("height") if m.get("height") is not None else getattr(rec, "height", 0))
            created_at: int = int(m.get("created_at") if m.get("created_at") is not None else int(time.time()))
            if not isinstance(task_id, (bytes, bytearray)):
                raise CapError("ResultRecord.task_id must be bytes")
            task_id = bytes(task_id)
            self._by_id[task_id] = rec
            self._created_at[task_id] = created_at
            self._by_caller.setdefault(bytes(caller), set()).add(task_id)
            self._by_height.setdefault(height, set()).add(task_id)

    def get(self, task_id: bytes) -> Optional[ResultRecord]:
        with self._lock:
            return self._by_id.get(bytes(task_id))

    def has(self, task_id: bytes) -> bool:
        with self._lock:
            return bytes(task_id) in self._by_id

    def delete(self, task_id: bytes) -> bool:
        with self._lock:
            tid = bytes(task_id)
            rec = self._by_id.pop(tid, None)
            if rec is None:
                return False
            m = _record_to_map(rec)
            caller: bytes = m.get("caller") or getattr(rec, "caller")
            height: int = int(m.get("height") if m.get("height") is not None else getattr(rec, "height", 0))
            self._created_at.pop(tid, None)
            if bytes(caller) in self._by_caller:
                self._by_caller[bytes(caller)].discard(tid)
                if not self._by_caller[bytes(caller)]:
                    del self._by_caller[bytes(caller)]
            if height in self._by_height:
                self._by_height[height].discard(tid)
                if not self._by_height[height]:
                    del self._by_height[height]
            return True

    def list_recent(self, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        with self._lock:
            # Sort by height desc, created_at desc as tiebreaker
            def key_fn(tid: bytes) -> Tuple[int, int]:
                rec = self._by_id[tid]
                m = _record_to_map(rec)
                height = int(m.get("height") if m.get("height") is not None else getattr(rec, "height", 0))
                created = self._created_at.get(tid, 0)
                return (height, created)

            tids = sorted(self._by_id.keys(), key=key_fn, reverse=True)
            sel = tids[offset : offset + limit]
            return [self._by_id[tid] for tid in sel]

    def list_by_caller(self, caller: bytes, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        with self._lock:
            tids = list(self._by_caller.get(bytes(caller), set()))
            # same ordering heuristic as list_recent
            def key_fn(tid: bytes) -> Tuple[int, int]:
                rec = self._by_id[tid]
                m = _record_to_map(rec)
                height = int(m.get("height") if m.get("height") is not None else getattr(rec, "height", 0))
                created = self._created_at.get(tid, 0)
                return (height, created)

            tids.sort(key=key_fn, reverse=True)
            sel = tids[offset : offset + limit]
            return [self._by_id[tid] for tid in sel]

    def close(self) -> None:  # no-op
        pass


# -----------------------------------------------------------------------------
# SQLite store
# -----------------------------------------------------------------------------

_SQL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS results (
  task_id     BLOB PRIMARY KEY,
  caller      BLOB NOT NULL,
  kind        INTEGER NOT NULL,
  chain_id    INTEGER NOT NULL,
  height      INTEGER NOT NULL,
  created_at  INTEGER NOT NULL,
  record      BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_caller   ON results(caller, height DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_results_height   ON results(height DESC, created_at DESC);
"""


def _extract_index_fields(rec: ResultRecord) -> Tuple[bytes, bytes, int, int, int, int]:
    m = _record_to_map(rec)
    try:
        task_id: bytes = bytes(m["task_id"])
        caller: bytes = bytes(m["caller"])
        # JobKind can be enum; store as int
        kind_val = m.get("kind")
        if isinstance(kind_val, JobKind):
            kind = int(kind_val.value if hasattr(kind_val, "value") else int(kind_val))
        else:
            kind = int(kind_val)
        chain_id = int(m["chain_id"])
        height = int(m["height"])
        created_at = int(m.get("created_at", int(time.time())))
        return (task_id, caller, kind, chain_id, height, created_at)
    except Exception as e:  # noqa: BLE001
        raise CapError(f"malformed ResultRecord for indexing: {e}") from e


class SqliteResultStore(ResultStore):
    def __init__(self, path: str) -> None:
        self._path = os.path.abspath(path) if path != ":memory:" else ":memory:"
        self._conn = sqlite3.connect(self._path, detect_types=0, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SQL_SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()

    # Context manager convenience
    def __enter__(self) -> "SqliteResultStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def put(self, rec: ResultRecord) -> None:
        b = _record_to_bytes(rec)
        task_id, caller, kind, chain_id, height, created_at = _extract_index_fields(rec)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO results(task_id, caller, kind, chain_id, height, created_at, record) "
                "VALUES(?,?,?,?,?,?,?)",
                (task_id, caller, kind, chain_id, height, created_at, b),
            )

    def get(self, task_id: bytes) -> Optional[ResultRecord]:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT record FROM results WHERE task_id = ?", (bytes(task_id),))
            row = cur.fetchone()
            if not row:
                return None
            (blob,) = row
            return _bytes_to_record(blob)

    def has(self, task_id: bytes) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("SELECT 1 FROM results WHERE task_id = ? LIMIT 1", (bytes(task_id),))
            return cur.fetchone() is not None

    def delete(self, task_id: bytes) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM results WHERE task_id = ?", (bytes(task_id),))
            return cur.rowcount > 0

    def list_recent(self, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT record FROM results ORDER BY height DESC, created_at DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            )
            return [_bytes_to_record(row[0]) for row in cur.fetchall()]

    def list_by_caller(self, caller: bytes, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "SELECT record FROM results WHERE caller = ? ORDER BY height DESC, created_at DESC LIMIT ? OFFSET ?",
                (bytes(caller), int(limit), int(offset)),
            )
            return [_bytes_to_record(row[0]) for row in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


# -----------------------------------------------------------------------------
# Small URL helper (optional)
# -----------------------------------------------------------------------------

_SQLITE_URL_RE = re.compile(r"^sqlite:///(.+)$")


def parse_result_store_env(value: Optional[str]) -> ResultStore:
    """
    Convenience for wiring from env like:
      RESULT_STORE=memory:
      RESULT_STORE=sqlite:///:memory:
      RESULT_STORE=sqlite:///var/lib/animica/capabilities/results.db
    """
    if not value:
        # Sensible default: in-memory (tests/dev)
        return MemoryResultStore()
    return open_result_store(value)


__all__ = [
    "ResultStore",
    "MemoryResultStore",
    "SqliteResultStore",
    "open_result_store",
    "parse_result_store_env",
]
