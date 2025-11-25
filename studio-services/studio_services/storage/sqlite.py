"""
SQLite connection & migrations runner for Animica Studio Services.

- Provides a per-thread SQLite connection via `get_db()`.
- Applies idempotent schema from `storage/schema.sql` via `run_migrations()`.
- Sets sane PRAGMAs for web workloads (WAL, busy_timeout, foreign_keys).

Environment overrides:
  STUDIO_DB_PATH or STORAGE_DB   -> path to sqlite file (default: ./.studio-services/meta.db)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

try:
    # Python 3.9+
    from importlib.resources import files as pkg_files
except ImportError:  # pragma: no cover
    from importlib_resources import files as pkg_files  # type: ignore


# ------------------------------- Path resolution -------------------------------

def _default_db_path() -> Path:
    return Path("./.studio-services/meta.db").resolve()


def _resolve_db_path() -> Path:
    env = os.getenv("STUDIO_DB_PATH") or os.getenv("STORAGE_DB")
    path = Path(env).expanduser().resolve() if env else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_DB_PATH = _resolve_db_path()


# --------------------------- Thread-local connection ---------------------------

_tlocal = threading.local()


def _configure_connection(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL for concurrency; NORMAL is a good latency/durability tradeoff
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA mmap_size=268435456;")  # 256 MiB (advisory)
    conn.execute("PRAGMA busy_timeout=5000;")    # 5s
    # Use UTF-8 by default
    # Note: isolation_level=None enables autocommit; we keep explicit transactions with ctx manager


def _open_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        _DB_PATH.as_posix(),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,  # safely used across threads with our thread-local
        isolation_level=None,     # autocommit by default; explicit transactions when needed
    )
    _configure_connection(conn)
    return conn


def get_db() -> sqlite3.Connection:
    """
    Get the current thread's SQLite connection, opening it if needed.
    """
    conn: Optional[sqlite3.Connection] = getattr(_tlocal, "conn", None)
    if conn is None:
        conn = _open_connection()
        _tlocal.conn = conn
    return conn


def close_db() -> None:
    """Close and clear the thread-local connection, if any."""
    conn: Optional[sqlite3.Connection] = getattr(_tlocal, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _tlocal.conn = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """
    Context manager for an explicit transaction:

        with transaction() as db:
            db.execute("INSERT ...")
            db.execute("UPDATE ...")

    Commits on success, rolls back on exception.
    """
    db = get_db()
    try:
        db.execute("BEGIN;")
        yield db
        db.execute("COMMIT;")
    except Exception:
        try:
            db.execute("ROLLBACK;")
        finally:
            raise


# --------------------------------- Migrations ---------------------------------

def _schema_sql_text() -> str:
    """
    Load the baseline schema SQL bundled at:
      studio_services/storage/schema.sql
    The schema MUST be idempotent (CREATE TABLE IF NOT EXISTS ...).
    """
    resource = pkg_files("studio_services.storage").joinpath("schema.sql")
    return resource.read_text(encoding="utf-8")


def run_migrations() -> None:
    """
    Apply the bundled schema in a single transactional script execution.
    Safe to call multiple times (idempotent).
    """
    script = _schema_sql_text()
    with transaction() as db:
        db.executescript(script)
        # Optional: record a simple applied flag/version (kept minimal for now)
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS meta_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT OR REPLACE INTO meta_kv(key, value) VALUES('schema_applied', '1');"
        )


# ------------------------------- Utility helpers -------------------------------

def vacuum_analyze() -> None:
    """
    Optional housekeeping: run ANALYZE and VACUUM (non-blocking with WAL).
    """
    db = get_db()
    db.execute("ANALYZE;")
    # VACUUM cannot run inside a transaction; ensure autocommit mode
    db.execute("VACUUM;")


__all__ = [
    "get_db",
    "close_db",
    "transaction",
    "run_migrations",
    "vacuum_analyze",
]
