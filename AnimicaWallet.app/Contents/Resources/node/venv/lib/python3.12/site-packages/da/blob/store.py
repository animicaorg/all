"""
Animica • DA • Local Blob Store (FS + SQLite)

A content-addressed blob store for Data Availability (DA) payloads.

Goals
-----
- **Content-addressed** by commitment root (NMT root) — storage_key == hex(root).
- **GC-safe writes**: write → fsync → atomic rename; DB upsert after the bytes land.
- **Pin/unpin**: reference tracking via a pins table (by root, optional tag).
- **Queries**: lookup by root, by namespace, recents; light stats.
- **Portable**: SQLite schema created automatically; paths are sharded for large stores.

This module is intentionally minimal and does not expose HTTP. It is used by
`da/retrieval/service.py` and friends to persist and fetch blob payloads.

Schema (SQLite)
---------------
blobs(
    root BLOB PRIMARY KEY,         -- raw bytes of NMT root
    namespace INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    mime TEXT,
    storage_key TEXT NOT NULL UNIQUE, -- "0x" + hex(root), content-address
    path TEXT NOT NULL,               -- absolute path to payload
    created_at INTEGER NOT NULL,
    data_shards INTEGER,
    total_shards INTEGER,
    share_bytes INTEGER
)
pins(
    root BLOB NOT NULL REFERENCES blobs(root) ON DELETE CASCADE,
    tag TEXT,                         -- optional human/system tag
    created_at INTEGER NOT NULL,
    PRIMARY KEY(root, tag)
)

Path layout (default sharding: depth=2, width=2)
------------------------------------------------
<root_dir>/
  objects/
    aa/
      bb/
        0xaa...bb...ff.blob      # the payload
        0xaa...bb...ff.meta.json # small metadata mirror (optional)
  db.sqlite

Notes
-----
- We store the **original payload** bytes (pre-erasure). Erasure/NMT pipelines
  are used to compute commitments and proofs elsewhere.
- The store is single-process safe via SQLite; for multi-process, each process
  should instantiate its own BlobStore pointing at the same root_dir.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import asdict
from typing import IO, Dict, Iterable, List, Optional, Tuple, Union

from ..constants import MAX_BLOB_BYTES
from .commitment import commit as compute_commitment
from .index import BlobIndexRecord, InMemoryBlobIndex, root_hex
from .types import BlobMeta, BlobRef, Commitment, Receipt

# ----------------------------- helpers ------------------------------------ #

BytesLike = Union[bytes, bytearray, memoryview]
Source = Union[BytesLike, str, os.PathLike, IO[bytes], Iterable[bytes]]


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _shard_parts(hex_str: str, depth: int, width: int) -> List[str]:
    s = hex_str[2:] if hex_str.startswith(("0x", "0X")) else hex_str
    parts = [s[i * width : (i + 1) * width] for i in range(depth)]
    return parts


def _atomic_write_bytes(dst_path: str, data: BytesLike) -> None:
    _ensure_dir(os.path.dirname(dst_path))
    dir_fd = os.open(os.path.dirname(dst_path), os.O_RDONLY)
    try:
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(dst_path), delete=False
        ) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        # Ensure directory entry is durable
        os.replace(tmp_path, dst_path)
        os.fsync(dir_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)  # if replace failed
        os.close(dir_fd)


def _atomic_copy_file(src_path: str, dst_path: str) -> None:
    _ensure_dir(os.path.dirname(dst_path))
    dir_fd = os.open(os.path.dirname(dst_path), os.O_RDONLY)
    try:
        with tempfile.NamedTemporaryFile(
            dir=os.path.dirname(dst_path), delete=False
        ) as tmp:
            with open(src_path, "rb") as fsrc:
                shutil.copyfileobj(fsrc, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, dst_path)
        os.fsync(dir_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        os.close(dir_fd)


def _buffer_iter_to_temp(
    root_dir: str, it: Iterable[bytes], *, max_bytes: int
) -> Tuple[str, int]:
    _ensure_dir(root_dir)
    total = 0
    fd, path = tempfile.mkstemp(prefix="buf_", dir=root_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in it:
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(
                        f"blob too large: {total} > MAX_BLOB_BYTES={max_bytes}"
                    )
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        return path, total
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        raise


def _now() -> int:
    return int(time.time())


def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)  # autocommit mode
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


# ------------------------------ store ------------------------------------- #


class BlobStore:
    """
    Local blob store rooted at `root_dir`.

    Parameters
    ----------
    root_dir : str
        Directory for objects/ and db.sqlite
    db_path : Optional[str]
        Custom SQLite path. If None, uses <root_dir>/db.sqlite
    shard_depth : int
        Directory sharding depth for object files (default 2)
    shard_width : int
        Characters per shard level (default 2)
    keep_meta_json : bool
        If True, write a small meta.json next to the blob for ops/debug.
    enable_mem_index : bool
        If True, mirror inserts into an in-memory index for faster list/find.
    """

    def __init__(
        self,
        root_dir: str,
        db_path: Optional[str] = None,
        *,
        shard_depth: int = 2,
        shard_width: int = 2,
        keep_meta_json: bool = True,
        enable_mem_index: bool = True,
    ) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.objects_dir = os.path.join(self.root_dir, "objects")
        _ensure_dir(self.objects_dir)

        self.db_path = db_path or os.path.join(self.root_dir, "db.sqlite")
        _ensure_dir(os.path.dirname(self.db_path))
        self.db = _open_db(self.db_path)
        self._ensure_schema()

        self.shard_depth = int(shard_depth)
        self.shard_width = int(shard_width)
        self.keep_meta_json = bool(keep_meta_json)
        self.index = InMemoryBlobIndex(max_recent=10000) if enable_mem_index else None
        if self.index:
            self._warm_index()

    # --- schema & index warming ------------------------------------------- #

    def _ensure_schema(self) -> None:
        c = self.db.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS blobs(
              root BLOB PRIMARY KEY,
              namespace INTEGER NOT NULL,
              size_bytes INTEGER NOT NULL,
              mime TEXT,
              storage_key TEXT NOT NULL UNIQUE,
              path TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              data_shards INTEGER,
              total_shards INTEGER,
              share_bytes INTEGER
            )
            """
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_blobs_ns_created ON blobs(namespace, created_at DESC)"
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS pins(
              root BLOB NOT NULL REFERENCES blobs(root) ON DELETE CASCADE,
              tag TEXT,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(root, tag)
            )
            """
        )
        c.close()

    def _warm_index(self) -> None:
        if not self.index:
            return
        c = self.db.cursor()
        for row in c.execute(
            "SELECT root, namespace, size_bytes, mime, storage_key, created_at, data_shards, total_shards, share_bytes FROM blobs ORDER BY created_at DESC LIMIT 20000"
        ):
            root_b, ns, size_b, mime, skey, created, ds, ts, sb = row
            rec = BlobIndexRecord(
                root=bytes(root_b),
                namespace=int(ns),
                size_bytes=int(size_b),
                mime=mime,
                storage_key=skey,
                created_at=int(created),
                data_shards=ds,
                total_shards=ts,
                share_bytes=sb,
            )
            self.index.put(rec)
        c.close()

    # --- path helpers ------------------------------------------------------ #

    def _object_path(self, root_hex_str: str) -> str:
        parts = _shard_parts(root_hex_str, self.shard_depth, self.shard_width)
        return os.path.join(self.objects_dir, *parts, f"{root_hex_str}.blob")

    def _meta_path(self, root_hex_str: str) -> str:
        parts = _shard_parts(root_hex_str, self.shard_depth, self.shard_width)
        return os.path.join(self.objects_dir, *parts, f"{root_hex_str}.meta.json")

    # --- public API: add/put ---------------------------------------------- #

    def add_bytes(
        self,
        data: BytesLike,
        *,
        namespace: int,
        mime: Optional[str] = None,
        erasure_params: Optional[object] = None,
    ) -> Tuple[BlobRef, Commitment, BlobMeta, Receipt]:
        if len(data) > MAX_BLOB_BYTES:
            raise ValueError(
                f"blob too large: {len(data)} > MAX_BLOB_BYTES={MAX_BLOB_BYTES}"
            )
        commitment, meta = compute_commitment(
            data, namespace, mime=mime, erasure_params=erasure_params
        )
        return self._store_and_index(
            source_bytes=data, commitment=commitment, meta=meta
        )

    def add_file(
        self,
        path: Union[str, os.PathLike],
        *,
        namespace: int,
        mime: Optional[str] = None,
        erasure_params: Optional[object] = None,
    ) -> Tuple[BlobRef, Commitment, BlobMeta, Receipt]:
        path = os.fspath(path)
        size = os.path.getsize(path)
        if size > MAX_BLOB_BYTES:
            raise ValueError(
                f"blob too large: {size} > MAX_BLOB_BYTES={MAX_BLOB_BYTES}"
            )
        commitment, meta = compute_commitment(
            path, namespace, mime=mime, erasure_params=erasure_params
        )
        return self._store_and_index(source_path=path, commitment=commitment, meta=meta)

    def add_iter(
        self,
        it: Iterable[bytes],
        *,
        namespace: int,
        mime: Optional[str] = None,
        erasure_params: Optional[object] = None,
    ) -> Tuple[BlobRef, Commitment, BlobMeta, Receipt]:
        # Buffer to temp to avoid double-consumption when computing commitment and storing
        buf_dir = os.path.join(self.root_dir, "tmp")
        _ensure_dir(buf_dir)
        tmp_path, _ = _buffer_iter_to_temp(buf_dir, it, max_bytes=MAX_BLOB_BYTES)
        try:
            commitment, meta = compute_commitment(
                tmp_path, namespace, mime=mime, erasure_params=erasure_params
            )
            return self._store_and_index(
                source_path=tmp_path, commitment=commitment, meta=meta, is_temp=True
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)

    # --- public API: lookup & IO ------------------------------------------ #

    def has(self, root: Union[bytes, str]) -> bool:
        rb = self._norm_root_bytes(root)
        cur = self.db.execute("SELECT 1 FROM blobs WHERE root=? LIMIT 1", (rb,))
        row = cur.fetchone()
        return row is not None

    def get_ref(self, root: Union[bytes, str]) -> Optional[BlobRef]:
        rb = self._norm_root_bytes(root)
        cur = self.db.execute(
            "SELECT root, namespace, size_bytes, mime, storage_key, path, created_at FROM blobs WHERE root=?",
            (rb,),
        )
        row = cur.fetchone()
        if not row:
            return None
        root_b, ns, size_b, mime, skey, path, created = row
        return BlobRef(
            root=bytes(root_b),
            namespace=int(ns),
            storage_key=skey,
            path=path,
            size_bytes=int(size_b),
            mime=mime,
            created_at=int(created),
        )

    def open(self, root: Union[bytes, str]) -> IO[bytes]:
        ref = self.get_ref(root)
        if not ref:
            raise FileNotFoundError("blob not found")
        return open(ref.path, "rb")

    def read(self, root: Union[bytes, str]) -> bytes:
        with self.open(root) as f:
            return f.read()

    def get_meta(self, root: Union[bytes, str]) -> Optional[BlobMeta]:
        rb = self._norm_root_bytes(root)
        cur = self.db.execute(
            "SELECT namespace, size_bytes, mime, data_shards, total_shards, share_bytes FROM blobs WHERE root=?",
            (rb,),
        )
        row = cur.fetchone()
        if not row:
            return None
        ns, size_b, mime, ds, ts, sb = row
        return BlobMeta(
            namespace=int(ns),
            size_bytes=int(size_b),
            mime=mime,
            data_shards=ds,
            total_shards=ts,
            share_bytes=sb,
        )

    # --- public API: list/find/stats -------------------------------------- #

    def list_by_namespace(
        self, ns: int, *, limit: int = 100, offset: int = 0
    ) -> List[BlobRef]:
        cur = self.db.execute(
            "SELECT root, namespace, size_bytes, mime, storage_key, path, created_at "
            "FROM blobs WHERE namespace=? ORDER BY created_at DESC, root LIMIT ? OFFSET ?",
            (int(ns), int(limit), int(offset)),
        )
        rows = cur.fetchall()
        return [
            BlobRef(
                root=bytes(r[0]),
                namespace=int(r[1]),
                size_bytes=int(r[2]),
                mime=r[3],
                storage_key=r[4],
                path=r[5],
                created_at=int(r[6]),
            )
            for r in rows
        ]

    def recent(self, *, limit: int = 50) -> List[BlobRef]:
        cur = self.db.execute(
            "SELECT root, namespace, size_bytes, mime, storage_key, path, created_at "
            "FROM blobs ORDER BY created_at DESC LIMIT ?",
            (int(limit),),
        )
        rows = cur.fetchall()
        return [
            BlobRef(
                root=bytes(r[0]),
                namespace=int(r[1]),
                size_bytes=int(r[2]),
                mime=r[3],
                storage_key=r[4],
                path=r[5],
                created_at=int(r[6]),
            )
            for r in rows
        ]

    def stats(self) -> Dict[str, int]:
        cur = self.db.cursor()
        total = cur.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
        pinned = cur.execute("SELECT COUNT(DISTINCT root) FROM pins").fetchone()[0]
        return {"total": int(total), "pinned": int(pinned)}

    # --- public API: pin/unpin -------------------------------------------- #

    def pin(self, root: Union[bytes, str], *, tag: Optional[str] = None) -> int:
        rb = self._norm_root_bytes(root)
        if not self.has(rb):
            raise FileNotFoundError("blob not found")
        self.db.execute(
            "INSERT OR IGNORE INTO pins(root, tag, created_at) VALUES(?, ?, ?)",
            (rb, tag, _now()),
        )
        return self._pin_count(rb)

    def unpin(self, root: Union[bytes, str], *, tag: Optional[str] = None) -> int:
        rb = self._norm_root_bytes(root)
        self.db.execute("DELETE FROM pins WHERE root=? AND tag IS ?", (rb, tag))
        return self._pin_count(rb)

    def is_pinned(self, root: Union[bytes, str]) -> bool:
        return self._pin_count(self._norm_root_bytes(root)) > 0

    # --- public API: GC ---------------------------------------------------- #

    def gc(
        self,
        *,
        dry_run: bool = True,
        older_than: Optional[int] = None,
        namespaces: Optional[List[int]] = None,
        max_delete: int = 1000,
    ) -> List[str]:
        """
        Garbage collect unpinned blobs (optionally older than a timestamp or restricted to namespaces).

        Returns list of storage_keys removed (or that would be removed in dry_run).
        """
        cond = ["NOT EXISTS (SELECT 1 FROM pins p WHERE p.root=b.root)"]
        params: List[object] = []
        if older_than is not None:
            cond.append("b.created_at < ?")
            params.append(int(older_than))
        if namespaces:
            placeholders = ",".join("?" for _ in namespaces)
            cond.append(f"b.namespace IN ({placeholders})")
            params.extend(int(x) for x in namespaces)
        where = " AND ".join(cond)
        cur = self.db.execute(
            f"SELECT b.root, b.storage_key, b.path FROM blobs b WHERE {where} ORDER BY b.created_at ASC LIMIT ?",
            (*params, int(max_delete)),
        )
        rows = cur.fetchall()
        removed: List[str] = []
        for rb, skey, path in rows:
            if dry_run:
                removed.append(skey)
                continue
            # Delete file first (best-effort), then DB row in a transaction.
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
            self.db.execute("DELETE FROM blobs WHERE root=?", (rb,))
            removed.append(skey)
            if self.index:
                rec = self.index.get(rb)  # type: ignore[arg-type]
                if rec:
                    self.index.delete(rb)  # type: ignore[arg-type]
        return removed

    # --- internals: store & index ----------------------------------------- #

    def _store_and_index(
        self,
        *,
        source_bytes: Optional[BytesLike] = None,
        source_path: Optional[str] = None,
        commitment: Commitment,
        meta: BlobMeta,
        is_temp: bool = False,
    ) -> Tuple[BlobRef, Commitment, BlobMeta, Receipt]:
        root_h = root_hex(commitment.root)
        obj_path = self._object_path(root_h)
        meta_path = self._meta_path(root_h)

        # If already present, short-circuit to ref result
        existing = self.get_ref(commitment.root)
        if existing:
            receipt = Receipt(
                commitment=commitment,
                created_at=existing.created_at,
                mime=existing.mime,
            )
            return existing, commitment, meta, receipt

        # Write the payload atomically
        if source_bytes is not None:
            _atomic_write_bytes(obj_path, source_bytes)
        elif source_path is not None:
            if is_temp:
                # Move temp file into place atomically
                _ensure_dir(os.path.dirname(obj_path))
                os.replace(source_path, obj_path)
                # Make dir durable
                dir_fd = os.open(os.path.dirname(obj_path), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            else:
                _atomic_copy_file(source_path, obj_path)
        else:  # pragma: no cover
            raise ValueError("either source_bytes or source_path must be provided")

        # Optional meta.json (non-authoritative)
        if self.keep_meta_json:
            try:
                _ensure_dir(os.path.dirname(meta_path))
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "commitment": {
                                "namespace": commitment.namespace,
                                "root": root_h,
                                "size_bytes": commitment.size_bytes,
                            },
                            "meta": asdict(meta),
                            "path": obj_path,
                            "created_at": _now(),
                        },
                        f,
                        indent=2,
                    )
            except Exception:
                # Non-fatal
                pass

        # Insert into DB (UPSERT-keep-old semantics for created_at)
        created_at = _now()
        self.db.execute(
            """
            INSERT INTO blobs(root, namespace, size_bytes, mime, storage_key, path, created_at, data_shards, total_shards, share_bytes)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(root) DO NOTHING
            """,
            (
                sqlite3.Binary(commitment.root),
                int(commitment.namespace),
                int(commitment.size_bytes),
                meta.mime,
                root_h,
                obj_path,
                created_at,
                meta.data_shards,
                meta.total_shards,
                meta.share_bytes,
            ),
        )

        # Build return objects
        ref = BlobRef(
            root=bytes(commitment.root),
            namespace=int(commitment.namespace),
            storage_key=root_h,
            path=obj_path,
            size_bytes=int(commitment.size_bytes),
            mime=meta.mime,
            created_at=created_at,
        )
        receipt = Receipt(commitment=commitment, created_at=created_at, mime=meta.mime)

        # Mirror to in-memory index if enabled
        if self.index:
            rec = BlobIndexRecord.from_commit_meta(
                storage_key=root_h, commit=commitment, meta=meta, created_at=created_at
            )
            self.index.put(rec)

        return ref, commitment, meta, receipt

    # --- utils ------------------------------------------------------------- #

    @staticmethod
    def _norm_root_bytes(root: Union[bytes, str]) -> bytes:
        if isinstance(root, bytes):
            return root
        hs = root[2:] if root.startswith(("0x", "0X")) else root
        return bytes.fromhex(hs)


__all__ = ["BlobStore"]
