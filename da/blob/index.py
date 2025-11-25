"""
Animica • DA • Blob Index

Lightweight indexing of blob commitments for fast lookup by:
- commitment root (primary key)
- namespace id (secondary key)
- optional attributes (MIME, size ranges, storage key prefix)

This module is *backend-agnostic*. It ships an in-memory implementation
sufficient for tests and small deployments. The persistent KV/SQL backends
are provided by da/blob/store.py and can wrap or mirror this index.

Thread-safety: the in-memory index is not thread-safe. Wrap with your own
locks if used from multiple threads.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union
import time

from .types import Commitment, BlobMeta


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

RootLike = Union[bytes, bytearray, memoryview, str]


def _b(x: RootLike) -> bytes:
    """Normalize a root-like value to raw bytes."""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        hs = x[2:] if x.startswith(("0x", "0X")) else x
        if len(hs) % 2:
            hs = "0" + hs
        return bytes.fromhex(hs)
    raise TypeError(f"unsupported root type: {type(x)!r}")


def root_hex(root: RootLike) -> str:
    """Return 0x-prefixed hex string for a commitment root."""
    return "0x" + _b(root).hex()


# --------------------------------------------------------------------------- #
# Records & queries
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BlobIndexRecord:
    """
    Indexed material for a committed blob.

    Fields:
      root: commitment root bytes (NMT root)
      namespace: integer namespace id
      size_bytes: original blob size (not including erasure expansion)
      mime: optional MIME hint
      storage_key: opaque key used by the underlying store (e.g., content-addressed id)
      created_at: unix timestamp (seconds) when the record was inserted
      data_shards, total_shards, share_bytes: optional erasure parameters (for inspection)
    """
    root: bytes
    namespace: int
    size_bytes: int
    storage_key: str
    created_at: int
    mime: Optional[str] = None
    data_shards: Optional[int] = None
    total_shards: Optional[int] = None
    share_bytes: Optional[int] = None

    @classmethod
    def from_commit_meta(
        cls,
        *,
        storage_key: str,
        commit: Commitment,
        meta: BlobMeta,
        created_at: Optional[int] = None,
    ) -> "BlobIndexRecord":
        ts = int(time.time()) if created_at is None else int(created_at)
        return cls(
            root=bytes(commit.root),
            namespace=int(commit.namespace),
            size_bytes=int(commit.size_bytes),
            storage_key=storage_key,
            created_at=ts,
            mime=meta.mime,
            data_shards=meta.data_shards,
            total_shards=meta.total_shards,
            share_bytes=meta.share_bytes,
        )

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        d["root"] = root_hex(self.root)
        return d


class InMemoryBlobIndex:
    """
    Simple in-memory blob index.

    Primary index:
      _by_root : Dict[bytes, BlobIndexRecord]

    Secondary indexes:
      _by_ns   : Dict[int, set[bytes]]                (namespace → roots)
      _by_skey : Dict[str, set[bytes]]                (storage_key → roots)
      _recent  : List[bytes]                          (roots ordered by created_at desc)

    The secondary indexes are best-effort conveniences for common queries. They
    are rebuilt consistently on insert/delete.
    """

    __slots__ = ("_by_root", "_by_ns", "_by_skey", "_recent", "_max_recent")

    def __init__(self, *, max_recent: int = 10_000) -> None:
        self._by_root: Dict[bytes, BlobIndexRecord] = {}
        self._by_ns: Dict[int, set[bytes]] = {}
        self._by_skey: Dict[str, set[bytes]] = {}
        self._recent: List[bytes] = []  # newest first
        self._max_recent = int(max_recent)

    # -- basics -------------------------------------------------------------- #

    def put(self, rec: BlobIndexRecord) -> BlobIndexRecord:
        """
        Insert or upsert a record. If the root already exists, this will replace
        the stored record (idempotent for identical data).

        Returns the stored record (which may be the previous entry if unchanged).
        """
        root = rec.root
        existing = self._by_root.get(root)
        if existing is not None:
            # If identical, keep created_at from the oldest (first-seen) for stability.
            if (
                existing.namespace == rec.namespace
                and existing.size_bytes == rec.size_bytes
                and existing.storage_key == rec.storage_key
                and existing.mime == rec.mime
                and existing.data_shards == rec.data_shards
                and existing.total_shards == rec.total_shards
                and existing.share_bytes == rec.share_bytes
            ):
                return existing
            # Replace but preserve original created_at to keep recent order stable.
            rec = BlobIndexRecord(**{**rec.__dict__, "created_at": existing.created_at})  # type: ignore[arg-type]
            self._remove_from_secondary(existing)

        self._by_root[root] = rec
        self._add_to_secondary(rec)
        return rec

    def put_from(self, *, storage_key: str, commit: Commitment, meta: BlobMeta) -> BlobIndexRecord:
        """Convenience: build a record from Commitment + BlobMeta and insert it."""
        rec = BlobIndexRecord.from_commit_meta(storage_key=storage_key, commit=commit, meta=meta)
        return self.put(rec)

    def get(self, root: RootLike) -> Optional[BlobIndexRecord]:
        return self._by_root.get(_b(root))

    def exists(self, root: RootLike) -> bool:
        return _b(root) in self._by_root

    def delete(self, root: RootLike) -> bool:
        rb = _b(root)
        rec = self._by_root.pop(rb, None)
        if rec is None:
            return False
        self._remove_from_secondary(rec)
        return True

    # -- secondary ----------------------------------------------------------- #

    def by_namespace(self, ns: int, *, limit: Optional[int] = None, offset: int = 0) -> List[BlobIndexRecord]:
        roots = self._by_ns.get(int(ns), set())
        # Sort by recency (created_at desc), then by root to stabilize order
        records = sorted((self._by_root[r] for r in roots), key=lambda r: (-r.created_at, r.root))
        if offset:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]
        return records

    def by_storage_key(self, skey: str) -> List[BlobIndexRecord]:
        roots = self._by_skey.get(skey, set())
        return sorted((self._by_root[r] for r in roots), key=lambda r: (-r.created_at, r.root))

    def recent(self, *, limit: int = 50) -> List[BlobIndexRecord]:
        out: List[BlobIndexRecord] = []
        for r in self._recent[: max(0, limit)]:
            rec = self._by_root.get(r)
            if rec is not None:
                out.append(rec)
        return out

    def find(
        self,
        *,
        namespace: Optional[int] = None,
        mime: Optional[str] = None,
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
        storage_key_prefix: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[BlobIndexRecord]:
        """
        Flexible filter over the index. All provided predicates must match.

        Results are ordered by created_at descending, then by root.
        """
        cand: Iterable[BlobIndexRecord]
        if namespace is not None:
            cand = self.by_namespace(namespace)
        else:
            cand = (self._by_root[r] for r in self._recent)

        def ok(rec: BlobIndexRecord) -> bool:
            if mime is not None and rec.mime != mime:
                return False
            if min_size is not None and rec.size_bytes < min_size:
                return False
            if max_size is not None and rec.size_bytes > max_size:
                return False
            if storage_key_prefix is not None and not rec.storage_key.startswith(storage_key_prefix):
                return False
            return True

        filtered = [r for r in cand if ok(r)]
        # Already newest-first because we iterate over self._recent
        if offset:
            filtered = filtered[offset:]
        if limit is not None:
            filtered = filtered[:limit]
        return filtered

    def stats(self) -> Dict[str, object]:
        """Small summary useful for metrics or debugging."""
        per_ns = {ns: len(roots) for ns, roots in self._by_ns.items()}
        return {
            "total": len(self._by_root),
            "per_namespace": per_ns,
            "recent_window": min(len(self._recent), self._max_recent),
        }

    # -- internals ----------------------------------------------------------- #

    def _add_to_secondary(self, rec: BlobIndexRecord) -> None:
        # namespace
        self._by_ns.setdefault(rec.namespace, set()).add(rec.root)
        # storage key
        self._by_skey.setdefault(rec.storage_key, set()).add(rec.root)
        # recent (newest first), ensure no dup, cap length
        try:
            self._recent.remove(rec.root)
        except ValueError:
            pass
        self._recent.insert(0, rec.root)
        if len(self._recent) > self._max_recent:
            # Drop tail, but keep secondary indexes consistent for dropped roots only
            tail = self._recent[self._max_recent :]
            self._recent = self._recent[: self._max_recent]
            # No need to touch _by_root or others; those entries remain addressable.
            # We only trim the recency window.
            if tail:
                tail = []  # noqa: F841 (explicit to show dev intent)

    def _remove_from_secondary(self, rec: BlobIndexRecord) -> None:
        roots = self._by_ns.get(rec.namespace)
        if roots is not None:
            roots.discard(rec.root)
            if not roots:
                self._by_ns.pop(rec.namespace, None)
        sk = self._by_skey.get(rec.storage_key)
        if sk is not None:
            sk.discard(rec.root)
            if not sk:
                self._by_skey.pop(rec.storage_key, None)
        try:
            self._recent.remove(rec.root)
        except ValueError:
            pass


__all__ = [
    "BlobIndexRecord",
    "InMemoryBlobIndex",
    "root_hex",
]
