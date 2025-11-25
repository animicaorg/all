from __future__ import annotations

"""
RocksDB-backed KV (optional)
===========================

A high-throughput KV using python-rocksdb when available. If the module or
native library is missing, this module *gracefully* falls back to SQLite (if
requested) or raises a helpful error.

Features
- Binary keys & values (bytes in, bytes out)
- Efficient prefix scans via iterator seek(prefix) → while key.startswith(prefix)
- Batched writes using WriteBatch
- Tuned defaults: block cache, Bloom filter, LZ4 compression

Usage
-----
    from core.db.rocksdb import open_rocksdb_kv
    kv = open_rocksdb_kv("/path/to/db", fallback_to_sqlite=True)

Contract
--------
Implements the KV / ReadOnlyKV / Batch protocols from `core.db.kv`.
"""

import os
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple, Union

try:
    import rocksdb  # type: ignore
    _ROCKS_OK = True
except Exception:
    rocksdb = None  # type: ignore
    _ROCKS_OK = False

from .kv import KV, ReadOnlyKV, Batch
from .sqlite import open_sqlite_kv  # for graceful fallback


def _ensure_dir(path: str) -> None:
    d = os.path.abspath(path)
    os.makedirs(d, exist_ok=True)


def _err_help(path: str) -> RuntimeError:
    return RuntimeError(
        "RocksDB backend unavailable. Install native lib & wheel:\n"
        "  • Ubuntu 22.04: sudo apt-get install -y librocksdb-dev\n"
        "  • Python: pip install python-rocksdb\n"
        f"Requested path: {path}\n"
        "Alternatively pass fallback_to_sqlite=True to auto-fallback."
    )


class RocksBatch(Batch):
    __slots__ = ("_db", "_wb", "_open")

    def __init__(self, db: "rocksdb.DB") -> None:  # type: ignore[name-defined]
        self._db = db
        self._wb = rocksdb.WriteBatch()  # type: ignore[attr-defined]
        self._open = False

    def __enter__(self) -> "RocksBatch":
        if self._open:
            raise RuntimeError("batch already open")
        self._open = True
        return self

    def put(self, key: bytes, value: bytes) -> None:
        if not self._open:
            raise RuntimeError("batch not open")
        self._wb.put(key, value)

    def delete(self, key: bytes) -> None:
        if not self._open:
            raise RuntimeError("batch not open")
        self._wb.delete(key)

    def commit(self) -> None:
        if not self._open:
            return
        self._db.write(self._wb)
        self._wb = rocksdb.WriteBatch()  # reset
        self._open = False

    def rollback(self) -> None:
        if not self._open:
            return
        # Discard the current WriteBatch by replacing it.
        self._wb = rocksdb.WriteBatch()
        self._open = False

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self._open = False
        return None


class RocksKV(KV):
    """
    RocksDB-backed KV satisfying the KV / ReadOnlyKV protocols.
    """

    __slots__ = ("_db", "_ro")

    def __init__(self, db: "rocksdb.DB", read_only: bool) -> None:  # type: ignore[name-defined]
        self._db = db
        self._ro = read_only

    # --- ReadOnlyKV ---

    def get(self, key: bytes) -> Optional[bytes]:
        v = self._db.get(key)
        # python-rocksdb returns None or bytes
        return v if v is None else bytes(v)

    def has(self, key: bytes) -> bool:
        v = self._db.get(key)
        return v is not None

    def iter_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]:
        it = self._db.iterkeys()  # We'll switch to full iterator for k,v
        # python-rocksdb's iterators can be configured; use raw for both.
        it = self._db.iteritems()
        it.seek(prefix)
        for k, v in it:
            # Defensive copy; library can reuse buffers
            if not k.startswith(prefix):
                break
            yield bytes(k), bytes(v)

    def close(self) -> None:
        # python-rocksdb doesn't expose an explicit close; help GC by dropping refs.
        try:
            del self._db
        except Exception:
            pass

    # --- KV (write methods) ---

    def put(self, key: bytes, value: bytes) -> None:
        if self._ro:
            raise PermissionError("DB is read-only")
        self._db.put(key, value)

    def delete(self, key: bytes) -> None:
        if self._ro:
            raise PermissionError("DB is read-only")
        self._db.delete(key)

    def batch(self) -> Batch:
        if self._ro:
            raise PermissionError("DB is read-only")
        return RocksBatch(self._db)


def _default_options() -> "rocksdb.Options":  # type: ignore[name-defined]
    """
    Sensible defaults for node workloads: point lookups + prefix scans + write bursts.
    """
    opts = rocksdb.Options()  # type: ignore[call-arg,attr-defined]
    opts.create_if_missing = True
    opts.max_open_files = 512
    opts.bytes_per_sync = 4 * 1024 * 1024
    opts.use_fsync = False
    opts.compaction_style = rocksdb.CompactionStyle.universal  # type: ignore[attr-defined]
    opts.compression = rocksdb.CompressionType.lz4_compression  # type: ignore[attr-defined]
    opts.level_compaction_dynamic_level_bytes = True
    # Block-based table with cache & Bloom filter
    table = rocksdb.BlockBasedTableFactory(  # type: ignore[attr-defined]
        block_cache=rocksdb.LRUCache(256 * 1024 * 1024),  # 256 MiB
        block_size=16 * 1024,
        filter_policy=rocksdb.BloomFilterPolicy(10),  # 10 bits per key
        format_version=5,
        whole_key_filtering=True,
    )
    opts.table_factory = table
    # We do not set a fixed prefix extractor because our prefixes vary in length.
    # Prefix scans use iterator seek(prefix) + startswith guard.
    return opts


def _default_read_options() -> "rocksdb.ReadOptions":  # type: ignore[name-defined]
    ro = rocksdb.ReadOptions()  # type: ignore[attr-defined]
    ro.verify_checksums = False
    ro.fill_cache = True
    return ro


def _default_write_options() -> "rocksdb.WriteOptions":  # type: ignore[name-defined]
    wo = rocksdb.WriteOptions()  # type: ignore[attr-defined]
    wo.sync = False
    wo.disableWAL = False
    return wo


def open_rocksdb_kv(
    path: Union[str, bytes, "os.PathLike[str]", "os.PathLike[bytes]"],
    *,
    create: bool = True,
    readonly: bool = False,
    fallback_to_sqlite: bool = True,
    options: Optional["rocksdb.Options"] = None,  # type: ignore[name-defined]
) -> KV:
    """
    Open a RocksDB KV at `path`.

    Args:
        create: create if missing (ignored when readonly)
        readonly: open in read-only mode
        fallback_to_sqlite: if RocksDB is unavailable, return a SQLiteKV at the same path+".sqlite"
        options: custom rocksdb.Options (advanced use)

    Returns:
        KV implementation (RocksKV or SQLiteKV on fallback)

    Raises:
        RuntimeError if RocksDB unavailable and fallback_to_sqlite=False
    """
    db_path = os.fspath(path)

    if not _ROCKS_OK:
        if fallback_to_sqlite:
            # SQLite file sits beside the requested RocksDB dir.
            sqlite_path = db_path + ".sqlite"
            return open_sqlite_kv(sqlite_path, create=True, readonly=readonly)
        raise _err_help(db_path)

    if not readonly and create:
        _ensure_dir(db_path)

    opts = options or _default_options()
    try:
        if readonly:
            db = rocksdb.DB(db_path, opts, read_only=True)  # type: ignore[attr-defined]
        else:
            db = rocksdb.DB(db_path, opts)  # type: ignore[attr-defined]
    except Exception as e:
        # e.g., path not found in readonly, or permissions; allow fallback path
        if fallback_to_sqlite:
            sqlite_path = db_path + ".sqlite"
            return open_sqlite_kv(sqlite_path, create=True, readonly=readonly)
        raise RuntimeError(f"Failed to open RocksDB at {db_path}: {e}") from e

    return RocksKV(db, read_only=readonly)


__all__ = [
    "open_rocksdb_kv",
    "RocksKV",
    "RocksBatch",
    "_ROCKS_OK",
]
