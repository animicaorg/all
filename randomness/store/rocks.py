"""
RocksDB-backed KeyValue store (optional dependency).

This module provides a drop-in implementation of the `KeyValue` protocol
used by the randomness subsystem, backed by RocksDB via the `python-rocksdb`
package (imported as `rocksdb`). Import is guarded so deployments can run
without RocksDB by simply not importing this module.

Features
--------
- Byte-oriented KV with efficient prefix scans via iterators.
- Batched atomic writes using WriteBatch inside a transaction context.
- Read snapshots during a transaction for consistent reads.
- Tuned defaults: LRU block cache, bloom filter, LZ4 compression (if available).

Install
-------
    pip install python-rocksdb

Notes
-----
Transactions here are *application-level* batches (WriteBatch). They are not
full MVCC transactions. For node workloads (append-mostly, idempotent writes)
this is sufficient.

API matches the `KeyValue` protocol from `randomness.store.kv`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator, Iterable, Optional, Tuple

# ---- Guarded import ---------------------------------------------------------

try:
    import rocksdb  # type: ignore
except Exception as e:  # pragma: no cover
    _rocks_import_error = e
    rocksdb = None  # type: ignore


# ---- Protocol (typing-only fallback) ----------------------------------------

try:  # Prefer shared Protocol from randomness.store.kv if present.
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


# ---- Helpers ----------------------------------------------------------------

def _ensure_rocks_available() -> None:
    if rocksdb is None:  # pragma: no cover
        raise ImportError(
            "RocksDB backend not available: failed to import 'rocksdb' "
            f"({type(_rocks_import_error).__name__}: {_rocks_import_error}). "
            "Install with: pip install python-rocksdb"
        )


# ---- Implementation ----------------------------------------------------------

@dataclass
class RocksKeyValue(KeyValue):
    """
    RocksDB-backed implementation of the KeyValue protocol.

    Parameters
    ----------
    path : str
        Filesystem path to the RocksDB database directory.
    read_only : bool
        If True, opens the DB read-only.
    block_cache_mb : int
        Size of the LRU block cache for table reads.
    bloom_bits_per_key : int
        Bloom filter bits per key (10–12 typical).
    write_buffer_mb : int
        Memtable write buffer size.
    max_open_files : int
        File descriptor budget; -1 for unlimited (not recommended).

    Example
    -------
    >>> kv = RocksKeyValue("/tmp/randomness_kv_rocks")
    >>> with kv.transaction():
    ...     kv.put(b"foo", b"bar")
    ...     assert kv.get(b"foo") == b"bar"
    ...     for k, v in kv.iter_prefix(b"f"):
    ...         pass
    ...     kv.delete(b"foo")
    >>> kv.close()
    """

    path: str
    read_only: bool = False
    block_cache_mb: int = 64
    bloom_bits_per_key: int = 10
    write_buffer_mb: int = 64
    max_open_files: int = 512

    def __post_init__(self) -> None:
        _ensure_rocks_available()

        opts = rocksdb.Options()
        opts.create_if_missing = not self.read_only
        opts.paranoid_checks = True
        opts.max_open_files = self.max_open_files
        opts.write_buffer_size = self.write_buffer_mb * 1024 * 1024
        # Compression (best-effort)
        try:
            opts.compression = getattr(rocksdb.CompressionType, "lz4_compression")
        except Exception:  # pragma: no cover
            pass

        # Block-based table with LRU cache & bloom filter
        block_cache = rocksdb.LRUCache(self.block_cache_mb * 1024 * 1024)
        bloom = rocksdb.BloomFilterPolicy(self.bloom_bits_per_key)
        table_opts = rocksdb.BlockBasedTableFactory(
            block_cache=block_cache,
            filter_policy=bloom,
            cache_index_and_filter_blocks=True,
            pin_l0_filter_and_index_blocks_in_cache=True,
        )
        opts.table_factory = table_opts

        # Write & Read options
        self._wo = rocksdb.WriteOptions()
        self._wo.sync = False
        self._ro = rocksdb.ReadOptions()
        # total_order_seek ensures full-DB iteration without prefix extractor
        self._ro.total_order_seek = True

        # Open DB
        if self.read_only:
            self._db = rocksdb.DB(self.path, opts, read_only=True)
        else:
            self._db = rocksdb.DB(self.path, opts)

        self._active_batch: Optional[rocksdb.WriteBatch] = None
        self._active_snapshot = None  # set to a snapshot object during tx

    # -- Context manager ------------------------------------------------------

    def __enter__(self) -> "RocksKeyValue":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- KV API ---------------------------------------------------------------

    def put(self, key: bytes, value: bytes) -> None:
        self._check_key_value(key, value)
        if self._active_batch is not None:
            self._active_batch.put(key, value)
        else:
            self._db.put(self._wo, key, value)

    def get(self, key: bytes) -> Optional[bytes]:
        ro = self._ro if self._active_snapshot is None else self._ro_with_snapshot()
        val = self._db.get(ro, key)
        return bytes(val) if val is not None else None

    def delete(self, key: bytes) -> None:
        if self._active_batch is not None:
            self._active_batch.delete(key)
        else:
            self._db.delete(self._wo, key)

    def iter_prefix(self, prefix: bytes) -> Iterable[Tuple[bytes, bytes]]:
        ro = self._ro if self._active_snapshot is None else self._ro_with_snapshot()
        it = self._db.iteritems(ro)
        try:
            it.seek(prefix)
            for k, v in it:
                # python-rocksdb returns bytes for k,v
                if not k.startswith(prefix):
                    break
                yield (k, v)
        finally:
            del it  # ensure iterator finalizer runs

    # -- Transactions (batched writes + snapshot reads) -----------------------

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """
        Atomic batch write context. Inside the context:
          - Writes (put/delete) are added to a WriteBatch.
          - Reads use a DB snapshot for consistency.
        On success: the batch is written. On error: the batch is discarded.
        """
        if self._active_batch is not None:
            # Nested transactions are not supported (keep it simple).
            raise RuntimeError("Nested RocksKeyValue.transaction() not supported")

        self._active_batch = rocksdb.WriteBatch()
        self._active_snapshot = self._db.get_snapshot()
        try:
            yield
        except Exception:
            # Discard batch; release snapshot
            self._release_snapshot()
            self._active_batch = None
            raise
        else:
            try:
                self._db.write(self._wo, self._active_batch)
            finally:
                self._release_snapshot()
                self._active_batch = None

    # -- Maintenance ----------------------------------------------------------

    def compact(self) -> None:
        """Compact the full key range."""
        self._db.compact_range()

    def close(self) -> None:
        # Explicit close isn't provided; drop references so GC can finalize.
        self._release_snapshot()
        self._db = None  # type: ignore

    # -- Internals ------------------------------------------------------------

    def _ro_with_snapshot(self):
        ro = rocksdb.ReadOptions()
        ro.total_order_seek = True
        ro.snapshot = self._active_snapshot
        return ro

    @staticmethod
    def _check_key_value(key: bytes, value: bytes) -> None:
        if not isinstance(key, (bytes, bytearray)) or not isinstance(value, (bytes, bytearray)):
            raise TypeError("key and value must be bytes")

    def _release_snapshot(self) -> None:
        if self._active_snapshot is not None:
            try:
                self._db.release_snapshot(self._active_snapshot)
            except Exception:  # pragma: no cover
                pass
            self._active_snapshot = None


__all__ = ["RocksKeyValue"]
