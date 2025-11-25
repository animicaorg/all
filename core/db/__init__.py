from __future__ import annotations

"""
core.db
=======

Thin facade for key–value database backends used by Animica core modules.

Backends
--------
- SQLite (default, always available)
- RocksDB (optional; imported if library present)

URIs
----
- "sqlite:///path/to/animica.db"   → SQLite file
- "sqlite:///:memory:"             → in-memory SQLite (tests)
- "rocksdb:///path/to/dir"         → RocksDB (directory), if python-rocksdb is installed
- "memory://"                      → alias of "sqlite:///:memory:"
- Bare path heuristics:
    * endswith(".db") → treated as sqlite file path
    * otherwise       → treated as rocksdb directory if available, else sqlite file

API
---
- open_kv(uri: str, create: bool = True) -> KV
    Open a KV store for general use.
- prefer_rocks() -> bool
    Returns True if RocksDB backend is available.

The actual typed KV interface is defined in core.db.kv.
This module only handles backend selection and re-exports common types and helpers.

Example
-------
>>> from core.db import open_kv
>>> kv = open_kv("sqlite:///:memory:")
>>> with kv.batch() as b:
...     b.put(b"s:key", b"hello")
>>> kv.get(b"s:key")
b'hello'
"""

from typing import Optional, Tuple

# Re-exports of common types/aliases
from .kv import (
    KV,
    ReadOnlyKV,
    Batch,
    Prefix,
    iter_prefixed,
)

# --- Required backend: SQLite -------------------------------------------------

from . import sqlite as _sqlite_backend  # noqa: F401

# --- Optional backend: RocksDB ------------------------------------------------

try:
    from . import rocksdb as _rocks_backend  # type: ignore  # noqa: F401
    _HAS_ROCKS = True
except Exception:
    _HAS_ROCKS = False


def prefer_rocks() -> bool:
    """Return True if RocksDB backend is importable."""
    return _HAS_ROCKS


def _parse_uri(uri: str) -> Tuple[str, str]:
    """
    Parse a DB URI into (backend, path_or_spec).

    Returns:
        ("sqlite", path) or ("rocksdb", path) or ("memory", "")
    """
    u = uri.strip()
    if u.startswith("sqlite:///"):
        return ("sqlite", u[len("sqlite:///") :])
    if u.startswith("rocksdb:///"):
        return ("rocksdb", u[len("rocksdb:///") :])
    if u.startswith("memory://"):
        return ("memory", "")
    # Heuristics for bare paths to keep CLI simple
    if u.endswith(".db"):
        return ("sqlite", u)
    # If we have rocks, prefer it for non-file-suffixed directories
    if prefer_rocks():
        return ("rocksdb", u)
    # Fallback to sqlite file
    return ("sqlite", u or ":memory:")


def open_kv(uri: str, create: bool = True) -> KV:
    """
    Open a KV database by URI. See module docstring for supported forms.

    Args:
        uri: backend spec / path.
        create: create structures if missing.

    Raises:
        RuntimeError if the chosen backend is unavailable.
        ValueError for invalid URIs.
    """
    backend, spec = _parse_uri(uri)

    if backend == "memory":
        return _sqlite_backend.open_sqlite_kv(":memory:", create=True)

    if backend == "sqlite":
        path = spec or ":memory:"
        return _sqlite_backend.open_sqlite_kv(path, create=create)

    if backend == "rocksdb":
        if not prefer_rocks():
            raise RuntimeError("RocksDB backend requested but python-rocksdb is not installed.")
        path = spec or "./animica.rocks"
        return _rocks_backend.open_rocks_kv(path, create=create)  # type: ignore

    raise ValueError(f"Unsupported DB backend in URI: {uri!r}")


__all__ = [
    # interfaces
    "KV",
    "ReadOnlyKV",
    "Batch",
    "Prefix",
    "iter_prefixed",
    # helpers
    "open_kv",
    "prefer_rocks",
]
