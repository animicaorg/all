"""
SQLite-backed KeyValue store for the randomness subsystem.

Features
--------
- Simple byte-oriented KV: (key BLOB PRIMARY KEY, value BLOB NOT NULL)
- Safe transactions via context manager: `with kv.transaction(): ...`
- Efficient prefix iteration using range scans (lower/upper bound).
- Pragmas tuned for node workloads (WAL, mmap, synchronous=NORMAL).
- Small, dependency-free implementation on top of stdlib `sqlite3`.

Notes
-----
Keys are arbitrary bytes. Prefix iteration relies on lexicographic byte
ordering of BLOBs. To iterate a prefix `p`, we select:
  key >= p AND key < next_prefix(p)
If `p` is all 0xFF bytes (no strict upper bound exists), we fall back to a
single-ended range and stop as soon as the prefix does not match.

This module implements the `KeyValue` protocol expected by
`randomness/store/kv.py`.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Iterable, Optional, Tuple


# --- KeyValue Protocol (imported only for typing to avoid runtime coupling) --

try:  # Prefer the shared Protocol from randomness.store if present.
    from . import KeyValue  # type: ignore
except Exception:  # pragma: no cover
    from typing import Protocol

    class KeyValue(Protocol):  # type: ignore
        def put(self, key: bytes, value: bytes) -> None: ...
        def get(self, key: bytes) -> Optional[bytes]: ...
        def delete(self, key: bytes) -> None: ...
        def iter_prefix(self, prefix: bytes) -> Iterable[Tuple[bytes, bytes]]: ...
        @contextmanager
        def transaction(self) -> Generator[None, None, None]: ...
        def close(self) -> None: ...


# --- Helpers -----------------------------------------------------------------

def _ensure_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # WAL enables concurrent readers; NORMAL keeps fsync reasonable.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    # Enable mmap when available (e.g., 64 MiB). No-op if unsupported.
    try:
        conn.execute("PRAGMA mmap_size=67108864;")
    except sqlite3.OperationalError:
        pass


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv (
            key   BLOB PRIMARY KEY,
            value BLOB NOT NULL
        );
        """
    )
    # PRIMARY KEY implies an index on key; explicit extra indices are unnecessary.
    conn.commit()


def _next_prefix(prefix: bytes) -> Optional[bytes]:
    """
    Compute the smallest byte-string that is strictly greater than all keys
    starting with `prefix`. If prefix is all 0xFF, return None (no upper bound).
    """
    if not prefix:
        return None  # no upper bound for empty prefix
    b = bytearray(prefix)
    for i in range(len(b) - 1, -1, -1):
        if b[i] != 0xFF:
            b[i] += 1
            return bytes(b[: i + 1])  # truncate after incremented byte
    return None  # all 0xFF


# --- Implementation -----------------------------------------------------------

@dataclass
class SQLiteKeyValue(KeyValue):
    """
    SQLite-backed implementation of the KeyValue protocol.

    Parameters
    ----------
    path : str
        File path to the SQLite database. Directories will be created if needed.
    read_only : bool
        If True, opens the DB in immutable/read-only mode when supported.

    Example
    -------
    >>> kv = SQLiteKeyValue("/tmp/randomness_kv.db")
    >>> with kv.transaction():
    ...     kv.put(b"hello", b"world")
    ...     assert kv.get(b"hello") == b"world"
    ...     for k, v in kv.iter_prefix(b"he"):
    ...         pass
    ...     kv.delete(b"hello")
    >>> kv.close()
    """

    path: str
    read_only: bool = False

    def __post_init__(self) -> None:
        _ensure_dir(self.path)

        uri = False
        if self.read_only:
            # Open in immutable read-only mode, when the file exists.
            db_path = f"file:{self.path}?mode=ro&immutable=1"
            uri = True
        else:
            db_path = self.path

        # isolation_level=None -> autocommit mode; we manage BEGIN/COMMIT explicitly
        self._conn = sqlite3.connect(
            db_path, detect_types=0, isolation_level=None, uri=uri, timeout=30.0
        )
        self._conn.execute("PRAGMA foreign_keys=ON;")
        _apply_pragmas(self._conn)
        if not self.read_only:
            _init_schema(self._conn)

    # --- Context manager support --------------------------------------------

    def __enter__(self) -> "SQLiteKeyValue":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- KV API --------------------------------------------------------------

    def put(self, key: bytes, value: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)) or not isinstance(value, (bytes, bytearray)):
            raise TypeError("key and value must be bytes")
        self._conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES(?, ?)", (bytes(key), bytes(value)))

    def get(self, key: bytes) -> Optional[bytes]:
        cur = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
        row = cur.fetchone()
        return bytes(row[0]) if row else None

    def delete(self, key: bytes) -> None:
        self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def iter_prefix(self, prefix: bytes) -> Iterable[Tuple[bytes, bytes]]:
        """
        Iterate (key, value) for keys that start with `prefix`.

        Uses a range-scan [prefix, next_prefix(prefix)) ordered by key ASC.
        If no strict upper-bound exists, falls back to lower-bounded scan and
        stops when the prefix no longer matches.
        """
        upper = _next_prefix(prefix)
        if upper is not None:
            sql = "SELECT key, value FROM kv WHERE key >= ? AND key < ? ORDER BY key ASC"
            args = (prefix, upper)
        else:
            sql = "SELECT key, value FROM kv WHERE key >= ? ORDER BY key ASC"
            args = (prefix,)

        cur = self._conn.execute(sql, args)
        for row in cur:
            k = bytes(row[0])
            if not k.startswith(prefix):
                # In the single-ended case, keys beyond the prefix may appear; stop early.
                if upper is None:
                    break
                continue
            yield (k, bytes(row[1]))

    # --- Transactions --------------------------------------------------------

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """
        Begin a write transaction (IMMEDIATE). Commits on success, rolls back on error.

        This context is *reentrant-safe* at the connection level but not across
        threads; a single SQLite connection should be used by a single thread.
        """
        # BEGIN IMMEDIATE to acquire a reserved lock early (reduces writer contention).
        self._conn.execute("BEGIN IMMEDIATE;")
        try:
            yield
        except Exception:
            try:
                self._conn.execute("ROLLBACK;")
            finally:
                pass
            raise
        else:
            self._conn.execute("COMMIT;")

    # --- Maintenance ---------------------------------------------------------

    def vacuum(self) -> None:
        """Run VACUUM to compact the database (offline)."""
        self._conn.execute("VACUUM;")

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


__all__ = ["SQLiteKeyValue"]
