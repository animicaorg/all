from __future__ import annotations

"""
SQLite-backed KV store
======================

A small, fast embedded KV using SQLite (BLOB keys & values), implementing the
`KV` / `ReadOnlyKV` / `Batch` protocols from `core.db.kv`.

- Table schema: kv(k BLOB PRIMARY KEY, v BLOB NOT NULL)
- Keys/values are raw bytes; ordering is lexicographic (memcmp).
- Prefix scans are efficient with a bounded range (prefix_hi) plus a guard
  on `substr(k, 1, len(prefix)) = prefix` to be correct for all inputs.

Pragmas tuned for node workloads:
- WAL journal, NORMAL sync, page/cache tuned, mmap enabled.

Threading:
- `check_same_thread=False` for multi-threaded access (caller provides external
  synchronization as needed). Batches execute inside a single transaction.

Python stdlib `sqlite3` on Ubuntu 22.04 is 3.37+ (supports UPSERT).
"""

import os
import sqlite3
from urllib.parse import urlparse
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable, Iterator, List, Optional, Tuple, Union

from .kv import KV, ReadOnlyKV, Batch

DEFAULT_PRAGMAS = {
    "journal_mode": "WAL",      # better concurrency
    "synchronous": "NORMAL",    # durability vs speed trade (NORMAL is fine with WAL)
    "temp_store": "MEMORY",
    "mmap_size": 512 * 1024 * 1024,  # 512 MiB
    "page_size": 4096,
    "cache_size": -256 * 1024,  # negative = KB; 256 MiB
    "locking_mode": "NORMAL",
    "foreign_keys": "OFF",
}


def _apply_pragmas(conn: sqlite3.Connection, pragmas: Optional[dict] = None) -> None:
    p = dict(DEFAULT_PRAGMAS)
    if pragmas:
        p.update(pragmas)

    # Some pragmas return a row, some don't; ignore return values.
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=%s" % p["journal_mode"])
    cur.execute("PRAGMA synchronous=%s" % p["synchronous"])
    cur.execute("PRAGMA temp_store=%s" % p["temp_store"])
    cur.execute("PRAGMA mmap_size=%d" % int(p["mmap_size"]))
    cur.execute("PRAGMA page_size=%d" % int(p["page_size"]))
    cur.execute("PRAGMA cache_size=%d" % int(p["cache_size"]))
    cur.execute("PRAGMA locking_mode=%s" % p["locking_mode"])
    cur.execute("PRAGMA foreign_keys=%s" % p["foreign_keys"])
    cur.close()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv (
            k BLOB PRIMARY KEY,
            v BLOB NOT NULL
        )
        """
    )
    # Optional covering index for prefix scans can help when the guard kicks in.
    conn.execute("CREATE INDEX IF NOT EXISTS kv_k_idx ON kv(k)")


def _prefix_hi(prefix: bytes) -> Optional[bytes]:
    """
    Return the smallest byte string that is strictly greater than all keys that have
    `prefix` as a prefix (the lexicographic upper bound). If no such value exists
    (i.e., prefix is all 0xFF), return None.

    Example: b"ab\x01" -> b"ab\x02"; b"\xff\xff" -> None
    """
    if not prefix:
        return None  # no bound (scan all keys; caller must guard)
    p = bytearray(prefix)
    for i in range(len(p) - 1, -1, -1):
        if p[i] != 0xFF:
            p[i] += 1
            del p[i + 1 :]  # truncate
            return bytes(p)
    return None


class SQLiteBatch(Batch):
    __slots__ = ("_conn", "_open")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._open = False

    def __enter__(self) -> "SQLiteBatch":
        if self._open:
            raise RuntimeError("batch already open (nested batches not supported)")
        # BEGIN IMMEDIATE prevents writer starvation, still allows concurrent readers
        self._conn.execute("BEGIN IMMEDIATE")
        self._open = True
        return self

    def put(self, key: bytes, value: bytes) -> None:
        if not self._open:
            raise RuntimeError("batch not open")
        self._conn.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (memoryview(key), memoryview(value)),
        )

    def delete(self, key: bytes) -> None:
        if not self._open:
            raise RuntimeError("batch not open")
        self._conn.execute("DELETE FROM kv WHERE k = ?", (memoryview(key),))

    def commit(self) -> None:
        if not self._open:
            return
        self._conn.execute("COMMIT")
        self._open = False

    def rollback(self) -> None:
        if not self._open:
            return
        self._conn.execute("ROLLBACK")
        self._open = False

    def __exit__(self, exc_type, exc, tb) -> Optional[bool]:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self._open = False
        # Propagate exception if any
        return None


def _open_connection(
    path: Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"],
    *,
    pragmas: Optional[dict] = None,
    create: bool = True,
    readonly: bool = False,
) -> sqlite3.Connection:
    uri_mode = False
    path_str = str(path)
    if path_str.startswith("sqlite://"):
        parsed = urlparse(path_str)
        path_str = parsed.path or ""
        if path_str.startswith("///"):
            path_str = "/" + path_str.lstrip("/")
    if readonly:
        # Use URI to pass immutable flag; also disable journal changes.
        path_str = f"file:{path_str}?mode=ro&immutable=1"
        uri_mode = True
    elif not create and not os.path.exists(path_str):
        raise FileNotFoundError(f"SQLite KV not found at {path_str}")

    conn = sqlite3.connect(
        path_str,
        detect_types=0,
        isolation_level=None,      # autocommit; we explicitly BEGIN for batches
        check_same_thread=False,   # allow multi-threaded use; caller synchronizes
        uri=uri_mode,
    )
    # Speed up row→bytes conversion slightly
    conn.text_factory = bytes  # though we store blobs; being explicit is fine

    if not readonly:
        _apply_pragmas(conn, pragmas)
        _migrate(conn)
    else:
        # In readonly we skip pragmas that mutate journal/page size.
        try:
            conn.execute("PRAGMA query_only=ON")
        except Exception:
            pass

    return conn


class SQLiteKV(KV):
    """
    SQLite-backed KV. Safe for multi-threaded access when the caller
    serializes write batches (sqlite3 enforces connection-level writer exclusivity).

    Use `open_sqlite_kv(path)` to construct.
    """

    __slots__ = ("_conn",)

    def __init__(
        self,
        conn_or_path: Union[sqlite3.Connection, str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"],
        *,
        pragmas: Optional[dict] = None,
        create: bool = True,
        readonly: bool = False,
    ) -> None:
        if isinstance(conn_or_path, sqlite3.Connection):
            self._conn = conn_or_path
        else:
            self._conn = _open_connection(
                conn_or_path, pragmas=pragmas, create=create, readonly=readonly
            )
        # `row_factory` left default; we fetch blobs as bytes.

    # --- ReadOnlyKV ---

    def get(self, key: bytes) -> Optional[bytes]:
        cur = self._conn.execute("SELECT v FROM kv WHERE k = ?", (memoryview(key),))
        row = cur.fetchone()
        cur.close()
        return bytes(row[0]) if row is not None else None

    def has(self, key: bytes) -> bool:
        cur = self._conn.execute("SELECT 1 FROM kv WHERE k = ? LIMIT 1", (memoryview(key),))
        row = cur.fetchone()
        cur.close()
        return row is not None

    def iter_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]:
        """
        Iterate keys with the given binary prefix in lexicographic order.

        We use a bounded range [prefix, prefix_hi) when available, plus a guard
        `substr(k,1,?)=prefix` to be correct even if `prefix_hi` is None.
        """
        hi = _prefix_hi(prefix)
        if hi is not None:
            sql = (
                "SELECT k, v FROM kv "
                "WHERE k >= ? AND k < ? AND substr(k,1,?) = ? "
                "ORDER BY k"
            )
            args = (memoryview(prefix), memoryview(hi), len(prefix), memoryview(prefix))
        else:
            # No finite upper bound (prefix all 0xFF): rely on guard only.
            sql = "SELECT k, v FROM kv WHERE substr(k,1,?) = ? ORDER BY k"
            args = (len(prefix), memoryview(prefix))

        cur = self._conn.execute(sql, args)
        try:
            for k, v in cur:
                yield bytes(k), bytes(v)
        finally:
            cur.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # --- KV ---

    def put(self, key: bytes, value: bytes) -> None:
        self._conn.execute(
            "INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (memoryview(key), memoryview(value)),
        )

    def delete(self, key: bytes) -> None:
        self._conn.execute("DELETE FROM kv WHERE k = ?", (memoryview(key),))

    def batch(self) -> Batch:
        return SQLiteBatch(self._conn)


def open_sqlite_kv(
    path: Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"],
    *,
    pragmas: Optional[dict] = None,
    create: bool = True,
    readonly: bool = False,
) -> SQLiteKV:
    """
    Open (or create) a SQLite KV at `path`.

    - `readonly=True` opens with immutable-like semantics (no WAL/journal change).
    - `create=False` will raise if the DB file does not exist.
    """
    conn = _open_connection(path, pragmas=pragmas, create=create, readonly=readonly)
    return SQLiteKV(conn)


__all__ = [
    "SQLiteKV",
    "SQLiteBatch",
    "open_sqlite_kv",
]
