"""
da.node_store — Node-Side DA Blob Store
========================================

A content-addressed blob store for the Animica node's Data Availability (DA)
subsystem.  This is the *node-local* store that backs the da.* RPC methods; it
is distinct from the higher-level DA layer (NMT, erasure coding, sampling) and
optimised for simple put/get/has/list/delete operations with quota enforcement.

Design choices
--------------
* **blob_id** : hex-encoded SHA3-256 digest of the raw blob bytes.
  SHA3-256 is in the Python standard library (hashlib.sha3_256), requires no
  external packages, and provides 256-bit collision resistance sufficient for a
  content-addressable store.  If BLAKE3 support is desired in future it can be
  layered on top without breaking the on-disk format.

* **Disk layout** (sharded for scalability)::

    <root_dir>/
      config.json          – persistent configuration
      blobs/
        ab/cd/<blob_id>.blob  – sharded by first 4 hex chars of blob_id
      index.sqlite          – SQLite WAL for metadata / LRU tracking
      tmp/                  – staging area for atomic writes

* **Integrity** : blob bytes are hashed on ingest and verified against blob_id.
  An optional verify flag on ``get`` can re-hash on read (off by default for
  the fast path).

* **Quota enforcement** : ``max_bytes`` hard cap.  On overflow the store either
  evicts the least-recently-used unpinned blobs (``on_full="evict"``) or
  rejects the new blob with an error (``on_full="reject"``).

* **Atomic writes** : write to ``tmp/``, fsync, rename into place, then update
  the SQLite index inside a transaction.

Thread-safety
-------------
SQLite is opened with ``check_same_thread=False`` and WAL mode; the store uses
a per-instance ``threading.Lock`` for writes.  Read-only operations (``has``,
``get``, ``list``) are safe without the lock.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("animica.da.node_store")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VERSION = "1"
_DEFAULT_MAX_BYTES: int = 10 * 1024 * 1024 * 1024  # 10 GiB
_MAX_BLOB_BYTES: int = 32 * 1024 * 1024             # 32 MiB per call
_SHARD_DEPTH = 2
_SHARD_WIDTH = 2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class NodeDAConfig:
    """Persistent configuration stored in ``<dir>/config.json``."""

    enabled: bool = False
    dir: str = ""
    max_bytes: int = _DEFAULT_MAX_BYTES
    eviction_policy: str = "lru"          # only "lru" supported for now
    on_full: str = "evict"                # "evict" | "reject"
    allow_remote_get: bool = True
    allow_remote_put: bool = False
    version: str = _VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "NodeDAConfig":
        known = {f.name for f in NodeDAConfig.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in d.items() if k in known}
        return NodeDAConfig(**filtered)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> int:
    return int(time.time())


def _compute_blob_id(data: bytes) -> str:
    """Return hex-encoded SHA3-256 digest of ``data``."""
    return hashlib.sha3_256(data).hexdigest()


def _shard_path(blob_id: str, base: str) -> str:
    """Return sharded directory path for a blob_id."""
    parts = [blob_id[i * _SHARD_WIDTH:(i + 1) * _SHARD_WIDTH] for i in range(_SHARD_DEPTH)]
    return os.path.join(base, *parts, f"{blob_id}.blob")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _atomic_write(dst: str, data: bytes) -> None:
    """Write ``data`` to ``dst`` atomically via tmp-file + rename."""
    dir_ = os.path.dirname(dst)
    _ensure_dir(dir_)
    dir_fd = os.open(dir_, os.O_RDONLY)
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(dir=dir_, delete=False) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, dst)
        os.fsync(dir_fd)
        tmp_path = None  # successfully moved; don't unlink
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)
        os.close(dir_fd)


def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _free_bytes_fs(path: str) -> int:
    """Return free bytes on the filesystem containing ``path``."""
    try:
        st = shutil.disk_usage(path)
        return st.free
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# NodeDAStore
# ---------------------------------------------------------------------------


class NodeDAStore:
    """
    Node-side DA blob store.

    Parameters
    ----------
    root_dir:
        Base directory.  Will be created if it does not exist.
    config:
        If provided, uses this config instead of loading from disk.
    """

    def __init__(self, root_dir: str, config: Optional[NodeDAConfig] = None) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.blobs_dir = os.path.join(self.root_dir, "blobs")
        self.tmp_dir = os.path.join(self.root_dir, "tmp")
        self.db_path = os.path.join(self.root_dir, "index.sqlite")
        self.config_path = os.path.join(self.root_dir, "config.json")

        _ensure_dir(self.root_dir)
        _ensure_dir(self.blobs_dir)
        _ensure_dir(self.tmp_dir)

        self._lock = threading.Lock()
        self.db = _open_db(self.db_path)
        self._ensure_schema()

        if config is not None:
            self._config = config
            self._save_config(config)
        else:
            self._config = self._load_config()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_config(self) -> NodeDAConfig:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return NodeDAConfig.from_dict(json.load(f))
            except Exception as exc:
                _log.warning("Failed to load DA config: %s", exc)
        cfg = NodeDAConfig(enabled=False, dir=self.root_dir)
        self._save_config(cfg)
        return cfg

    def _save_config(self, cfg: NodeDAConfig) -> None:
        tmp = self.config_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.config_path)
        except Exception as exc:
            _log.warning("Failed to save DA config: %s", exc)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)

    def update_config(self, **kwargs: Any) -> NodeDAConfig:
        """Update configuration fields and persist."""
        with self._lock:
            old = self._config.to_dict()
            old.update({k: v for k, v in kwargs.items() if v is not None})
            new_cfg = NodeDAConfig.from_dict(old)
            self._config = new_cfg
            self._save_config(new_cfg)
            return new_cfg

    @property
    def config(self) -> NodeDAConfig:
        return self._config

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS blobs (
                blob_id          TEXT PRIMARY KEY,
                size_bytes       INTEGER NOT NULL,
                path             TEXT NOT NULL,
                created_at       INTEGER NOT NULL,
                last_accessed_at INTEGER NOT NULL,
                content_type     TEXT,
                owner            TEXT,
                metadata_json    TEXT
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_lru ON blobs(last_accessed_at ASC)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_created ON blobs(created_at DESC)"
        )
        self.db.commit()

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def put(
        self,
        data: bytes,
        *,
        content_type: Optional[str] = None,
        owner: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, int]:
        """
        Ingest a blob.

        Returns ``(blob_id, size_bytes)``.
        Raises ``ValueError`` if the blob exceeds ``_MAX_BLOB_BYTES`` or
        quota enforcement leads to a rejection.
        """
        if len(data) > _MAX_BLOB_BYTES:
            raise ValueError(
                f"blob too large: {len(data)} > MAX_BLOB_BYTES={_MAX_BLOB_BYTES}"
            )

        blob_id = _compute_blob_id(data)
        size = len(data)

        with self._lock:
            # Idempotent: already stored
            if self._has_nolock(blob_id):
                self._touch_nolock(blob_id)
                return blob_id, size

            # Quota enforcement
            if self._config.max_bytes > 0:
                used = self._used_bytes_nolock()
                if used + size > self._config.max_bytes:
                    if self._config.on_full == "reject":
                        raise ValueError(
                            f"DA quota exceeded: used={used} + size={size} > "
                            f"max={self._config.max_bytes}"
                        )
                    else:  # evict
                        self._evict_nolock(need=used + size - self._config.max_bytes)

            # Write file atomically
            dst = _shard_path(blob_id, self.blobs_dir)
            _atomic_write(dst, data)

            # Index
            now = _now()
            meta_str = json.dumps(metadata) if metadata else None
            self.db.execute(
                """
                INSERT INTO blobs
                    (blob_id, size_bytes, path, created_at, last_accessed_at,
                     content_type, owner, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(blob_id) DO NOTHING
                """,
                (blob_id, size, dst, now, now, content_type, owner, meta_str),
            )
            self.db.commit()

        return blob_id, size

    def get(self, blob_id: str, *, verify: bool = False) -> Tuple[bytes, Dict[str, Any]]:
        """
        Retrieve a blob by id.

        Returns ``(data, metadata_dict)`` or raises ``FileNotFoundError``.
        If ``verify=True``, re-hashes the data and raises ``ValueError`` on
        mismatch.
        """
        row = self.db.execute(
            "SELECT path, size_bytes, content_type, owner, metadata_json "
            "FROM blobs WHERE blob_id=?",
            (blob_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"blob not found: {blob_id}")

        path, size_bytes, content_type, owner, metadata_json = row

        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            raise FileNotFoundError(f"blob file missing on disk: {blob_id}")

        if verify:
            got_id = _compute_blob_id(data)
            if got_id != blob_id:
                raise ValueError(
                    f"blob integrity check failed: "
                    f"stored_id={blob_id} computed_id={got_id}"
                )

        # Update access time (best-effort, don't hold lock)
        try:
            with self._lock:
                self._touch_nolock(blob_id)
        except Exception:
            pass

        meta: Dict[str, Any] = {}
        if metadata_json:
            with contextlib.suppress(Exception):
                meta = json.loads(metadata_json)
        if content_type:
            meta["content_type"] = content_type
        if owner:
            meta["owner"] = owner

        return data, meta

    def has(self, blob_id: str) -> bool:
        """Return True if the blob exists in the index."""
        row = self.db.execute(
            "SELECT 1 FROM blobs WHERE blob_id=? LIMIT 1", (blob_id,)
        ).fetchone()
        return row is not None

    def delete(self, blob_id: str) -> bool:
        """Delete a blob.  Returns True if it existed."""
        with self._lock:
            row = self.db.execute(
                "SELECT path FROM blobs WHERE blob_id=?", (blob_id,)
            ).fetchone()
            if row is None:
                return False
            path = row[0]
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
            self.db.execute("DELETE FROM blobs WHERE blob_id=?", (blob_id,))
            self.db.commit()
            return True

    def list_blobs(
        self,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
        order: str = "newest",
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        List blobs with optional cursor-based pagination.

        Parameters
        ----------
        limit:
            Maximum number of results to return.
        cursor:
            Opaque cursor from a previous call's ``next_cursor``.
            For ``order="newest"`` the cursor is a ``created_at`` timestamp
            (exclusive upper bound); for ``order="lru"`` it is
            ``last_accessed_at``.
        order:
            ``"newest"`` (most recently created first) or ``"lru"``
            (least recently used first, for eviction preview).

        Returns
        -------
        (items, next_cursor) where ``next_cursor`` is ``None`` when exhausted.
        """
        limit = min(max(1, int(limit)), 1000)

        if order == "lru":
            order_col = "last_accessed_at"
            order_dir = "ASC"
        else:
            order_col = "created_at"
            order_dir = "DESC"

        # Cursor format: "<timestamp>:<rowid>" for stable tie-breaking.
        params: List[Any] = []
        where = ""
        if cursor is not None:
            try:
                parts = str(cursor).split(":")
                cur_ts = int(parts[0])
                cur_rid = int(parts[1]) if len(parts) > 1 else None
            except (ValueError, IndexError):
                cur_ts = None
                cur_rid = None

            if cur_ts is not None:
                if order_dir == "DESC":
                    if cur_rid is not None:
                        where = (
                            f"WHERE ({order_col} < ? OR "
                            f"({order_col} = ? AND rowid < ?))"
                        )
                        params.extend([cur_ts, cur_ts, cur_rid])
                    else:
                        where = f"WHERE {order_col} < ?"
                        params.append(cur_ts)
                else:
                    if cur_rid is not None:
                        where = (
                            f"WHERE ({order_col} > ? OR "
                            f"({order_col} = ? AND rowid > ?))"
                        )
                        params.extend([cur_ts, cur_ts, cur_rid])
                    else:
                        where = f"WHERE {order_col} > ?"
                        params.append(cur_ts)

        rows = self.db.execute(
            f"SELECT rowid, blob_id, size_bytes, created_at, last_accessed_at "
            f"FROM blobs {where} "
            f"ORDER BY {order_col} {order_dir}, rowid {order_dir} "
            f"LIMIT ?",
            (*params, limit + 1),
        ).fetchall()

        items: List[Dict[str, Any]] = []
        for rowid, blob_id, size_bytes, created_at, last_accessed_at in rows[:limit]:
            items.append(
                {
                    "blob_id": blob_id,
                    "size_bytes": int(size_bytes),
                    "created_at": int(created_at),
                    "last_accessed_at": int(last_accessed_at),
                }
            )

        next_cursor: Optional[str] = None
        if len(rows) > limit:
            last = rows[limit - 1]
            last_rowid = last[0]
            last_ts = last[3] if order == "newest" else last[4]
            next_cursor = f"{last_ts}:{last_rowid}"

        return items, next_cursor

    def gc(
        self,
        *,
        target_bytes: Optional[int] = None,
        older_than_seconds: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Garbage-collect blobs.

        At least one of ``target_bytes`` or ``older_than_seconds`` must be
        provided.  Both can be combined (OR semantics: remove anything that
        matches either criterion).

        Returns ``(freed_bytes, removed_count)``.
        """
        if target_bytes is None and older_than_seconds is None:
            raise ValueError(
                "gc requires at least one of target_bytes or older_than_seconds"
            )

        with self._lock:
            freed = 0
            removed = 0

            if older_than_seconds is not None:
                cutoff = _now() - int(older_than_seconds)
                rows = self.db.execute(
                    "SELECT blob_id, path, size_bytes FROM blobs "
                    "WHERE created_at < ? ORDER BY created_at ASC",
                    (cutoff,),
                ).fetchall()
                for blob_id, path, size_bytes in rows:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(path)
                    self.db.execute("DELETE FROM blobs WHERE blob_id=?", (blob_id,))
                    freed += int(size_bytes)
                    removed += 1
                self.db.commit()

            if target_bytes is not None and target_bytes > 0:
                freed2, removed2 = self._evict_nolock(need=target_bytes)
                freed += freed2
                removed += removed2

            return freed, removed

    def stats(self) -> Dict[str, Any]:
        """Return storage stats."""
        row = self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM blobs"
        ).fetchone()
        count, used = int(row[0]), int(row[1])
        free_fs = _free_bytes_fs(self.root_dir)
        return {
            "blob_count": count,
            "used_bytes": used,
            "max_bytes": self._config.max_bytes,
            "free_bytes_fs": free_fs,
        }

    # ------------------------------------------------------------------
    # Internal helpers (call with self._lock held)
    # ------------------------------------------------------------------

    def _has_nolock(self, blob_id: str) -> bool:
        return (
            self.db.execute(
                "SELECT 1 FROM blobs WHERE blob_id=? LIMIT 1", (blob_id,)
            ).fetchone()
            is not None
        )

    def _touch_nolock(self, blob_id: str) -> None:
        self.db.execute(
            "UPDATE blobs SET last_accessed_at=? WHERE blob_id=?",
            (_now(), blob_id),
        )
        self.db.commit()

    def _used_bytes_nolock(self) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM blobs"
        ).fetchone()
        return int(row[0])

    def _evict_nolock(self, *, need: int) -> Tuple[int, int]:
        """Evict LRU blobs until ``need`` bytes have been freed."""
        freed = 0
        removed = 0
        rows = self.db.execute(
            "SELECT blob_id, path, size_bytes FROM blobs "
            "ORDER BY last_accessed_at ASC"
        ).fetchall()
        for blob_id, path, size_bytes in rows:
            if freed >= need:
                break
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
            self.db.execute("DELETE FROM blobs WHERE blob_id=?", (blob_id,))
            freed += int(size_bytes)
            removed += 1
        self.db.commit()
        return freed, removed

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.db.close()

    def __enter__(self) -> "NodeDAStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Global singleton (lazy, per data-dir)
# ---------------------------------------------------------------------------

_STORE_LOCK = threading.Lock()
_STORE_CACHE: Dict[str, "NodeDAStore"] = {}


def get_store(root_dir: str) -> NodeDAStore:
    """
    Return (or create) the ``NodeDAStore`` for ``root_dir``.

    The store is cached per resolved absolute path so that callers across the
    RPC layer share a single instance and therefore a single SQLite connection
    and lock.
    """
    key = os.path.abspath(root_dir)
    with _STORE_LOCK:
        if key not in _STORE_CACHE:
            _STORE_CACHE[key] = NodeDAStore(key)
        return _STORE_CACHE[key]


def invalidate_store(root_dir: str) -> None:
    """Remove a cached store (e.g. after reconfiguration)."""
    key = os.path.abspath(root_dir)
    with _STORE_LOCK:
        store = _STORE_CACHE.pop(key, None)
        if store is not None:
            store.close()


__all__ = [
    "NodeDAConfig",
    "NodeDAStore",
    "get_store",
    "invalidate_store",
    "_compute_blob_id",
    "_MAX_BLOB_BYTES",
    "_DEFAULT_MAX_BYTES",
]
