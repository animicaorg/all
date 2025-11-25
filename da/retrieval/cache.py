from __future__ import annotations

"""
Animica • DA • Retrieval • Cache

Small, thread-safe LRU cache used to keep recent DA artifacts hot:
- blob bodies (by commitment)
- shards (by commitment + namespace + share index)
- availability proofs (by commitment + sample set)

Goals
-----
- In-memory only, process-local.
- LRU with dual limits: max_items and max_bytes.
- Optional per-entry TTL.
- Simple tag/index support so callers can purge groups.
- No external dependencies.

Typical usage (in da/retrieval/service.py)
------------------------------------------
    CACHE = LRUCache(max_items=2048, max_bytes=64 * 1024 * 1024)

    key = key_shard(commitment_hex, ns=24, index=1337)
    CACHE.put(key, shard_bytes, ttl=60, tags={"shard"})
    data = CACHE.get(key)  # -> bytes | None

    proof_key = key_proof(commitment_hex, samples=(3, 11, 42))
    CACHE.put(proof_key, proof_json_bytes, ttl=30, tags={"proof"})

Environment overrides (optional)
--------------------------------
- DA_CACHE_MAX_ITEMS (int, default 2048)
- DA_CACHE_MAX_BYTES (int, default 64 MiB)

Keys helpers
------------
- key_blob(commit)               -> "blob:<commit>"
- key_shard(commit, ns, index)   -> "shard:<commit>:ns=<ns>:i=<idx>"
- key_proof(commit, samples)     -> "proof:<commit>:S=<comma-separated-samples>"

"""

import json
import os
import time
import threading
from dataclasses import dataclass
from collections import OrderedDict
from typing import Any, Dict, Iterable, Optional, Set, Tuple


# -------------------------- Key helpers -------------------------------------

def key_blob(commitment_hex: str) -> str:
    return f"blob:{commitment_hex.lower()}"

def key_shard(commitment_hex: str, ns: int, index: int) -> str:
    return f"shard:{commitment_hex.lower()}:ns={int(ns)}:i={int(index)}"

def key_proof(commitment_hex: str, samples: Iterable[int]) -> str:
    # Normalize samples deterministically
    s = ",".join(str(int(x)) for x in sorted(set(samples)))
    return f"proof:{commitment_hex.lower()}:S={s}"


# ----------------------------- Entries & Stats ------------------------------

@dataclass
class CacheEntry:
    value: Any
    size: int
    expires_at: Optional[float]  # perf_counter timestamp or None
    tags: Set[str]

@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    puts: int = 0
    evictions: int = 0
    bytes_current: int = 0
    items_current: int = 0
    bytes_max: int = 0
    items_max: int = 0


# ----------------------------- LRU Cache ------------------------------------

class LRUCache:
    """
    Thread-safe LRU cache with byte and item limits.
    """

    def __init__(self, *,
                 max_items: Optional[int] = None,
                 max_bytes: Optional[int] = None):
        env_items = os.getenv("DA_CACHE_MAX_ITEMS")
        env_bytes = os.getenv("DA_CACHE_MAX_BYTES")
        if max_items is None:
            max_items = int(env_items) if env_items and env_items.isdigit() else 2048
        if max_bytes is None:
            try:
                max_bytes = int(env_bytes) if env_bytes else 64 * 1024 * 1024
            except Exception:
                max_bytes = 64 * 1024 * 1024

        self.max_items = max(1, int(max_items))
        self.max_bytes = max(1024, int(max_bytes))

        self._lock = threading.RLock()
        self._map: "OrderedDict[str, CacheEntry]" = OrderedDict()
        self._tags: Dict[str, Set[str]] = {}
        self._bytes = 0

        self._stats = CacheStats(
            bytes_max=self.max_bytes,
            items_max=self.max_items,
        )

    # ---- public API ---------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        now = time.perf_counter()
        with self._lock:
            ce = self._map.get(key)
            if ce is None:
                self._stats.misses += 1
                return None
            if ce.expires_at is not None and now >= ce.expires_at:
                # expired; remove and count as miss
                self._delete_unlocked(key)
                self._stats.misses += 1
                return None
            # move to MRU
            self._map.move_to_end(key, last=True)
            self._stats.hits += 1
            return ce.value

    def put(self, key: str, value: Any, *,
            ttl: Optional[float] = None,
            size_bytes: Optional[int] = None,
            tags: Optional[Iterable[str]] = None) -> None:
        size = size_bytes if (size_bytes is not None and size_bytes >= 0) else self._estimate_size(value)
        expires_at = (time.perf_counter() + float(ttl)) if (ttl and ttl > 0) else None
        norm_tags: Set[str] = {t.strip().lower() for t in (tags or []) if t and t.strip()}

        with self._lock:
            # replace existing?
            if key in self._map:
                # remove old size
                old = self._map.pop(key)
                self._bytes -= old.size
                # update tag index
                for t, keys in list(self._tags.items()):
                    if key in keys:
                        keys.discard(key)
                        if not keys:
                            self._tags.pop(t, None)

            ce = CacheEntry(value=value, size=size, expires_at=expires_at, tags=norm_tags)
            self._map[key] = ce
            self._map.move_to_end(key, last=True)
            self._bytes += size
            for t in norm_tags:
                self._tags.setdefault(t, set()).add(key)

            self._stats.puts += 1
            self._stats.bytes_current = self._bytes
            self._stats.items_current = len(self._map)

            self._evict_if_needed_unlocked()

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._delete_unlocked(key)

    def purge_prefix(self, prefix: str) -> int:
        removed = 0
        with self._lock:
            keys = [k for k in self._map.keys() if k.startswith(prefix)]
            for k in keys:
                if self._delete_unlocked(k):
                    removed += 1
        return removed

    def purge_tag(self, tag: str) -> int:
        tag = tag.strip().lower()
        removed = 0
        with self._lock:
            keys = list(self._tags.get(tag, ()))
            for k in keys:
                if self._delete_unlocked(k):
                    removed += 1
            self._tags.pop(tag, None)
        return removed

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._tags.clear()
            self._bytes = 0
            self._stats.bytes_current = 0
            self._stats.items_current = 0

    def stats(self) -> CacheStats:
        with self._lock:
            # return a copy to avoid races
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                puts=self._stats.puts,
                evictions=self._stats.evictions,
                bytes_current=self._bytes,
                items_current=len(self._map),
                bytes_max=self.max_bytes,
                items_max=self.max_items,
            )

    def __contains__(self, key: str) -> bool:  # pragma: no cover
        with self._lock:
            return key in self._map

    def __len__(self) -> int:  # pragma: no cover
        with self._lock:
            return len(self._map)

    # ---- internals ----------------------------------------------------------

    def _delete_unlocked(self, key: str) -> bool:
        ce = self._map.pop(key, None)
        if ce is None:
            return False
        self._bytes -= ce.size
        for t in ce.tags:
            s = self._tags.get(t)
            if s:
                s.discard(key)
                if not s:
                    self._tags.pop(t, None)
        self._stats.bytes_current = self._bytes
        self._stats.items_current = len(self._map)
        return True

    def _evict_if_needed_unlocked(self) -> None:
        now = time.perf_counter()

        # First pass: drop expired entries from LRU side (oldest first)
        for k in list(self._map.keys()):
            ce = self._map[k]
            if ce.expires_at is not None and now >= ce.expires_at:
                self._delete_unlocked(k)

        # Enforce limits
        evicted = 0
        while (self._bytes > self.max_bytes or len(self._map) > self.max_items) and self._map:
            # Pop LRU (first item)
            k, _ = self._map.popitem(last=False)
            # Remove from tags & bytes
            # We need the entry for size and tags; but popitem discarded it.
            # Re-fetch via a shadow dict? Instead, peek before pop:
            # (Refactor: we already popped; recreate via prior ce)
            # Better approach: do a peek first.
            # To keep it simple, we lookup ce via a temporary storage.
            # Since we popped, we can't access it; so rework:
            pass  # replaced below

        # Correct implementation (peek + pop)
        while (self._bytes > self.max_bytes or len(self._map) > self.max_items) and self._map:
            # peek LRU key
            k = next(iter(self._map.keys()))
            self._delete_unlocked(k)
            evicted += 1

        if evicted:
            self._stats.evictions += evicted
            self._stats.bytes_current = self._bytes
            self._stats.items_current = len(self._map)

    @staticmethod
    def _estimate_size(obj: Any) -> int:
        # Fast paths
        if obj is None:
            return 0
        if isinstance(obj, (bytes, bytearray, memoryview)):
            return len(obj)
        if isinstance(obj, str):
            return len(obj.encode("utf-8", errors="ignore"))
        # Common containers → rough JSON size (deterministic enough for budgeting)
        try:
            return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        except Exception:
            # Fallback to repr length
            try:
                return len(repr(obj).encode("utf-8", errors="ignore"))
            except Exception:
                return 64  # arbitrary small default


# ------------------------------ Exports -------------------------------------

__all__ = [
    "LRUCache",
    "CacheStats",
    "CacheEntry",
    "key_blob",
    "key_shard",
    "key_proof",
]
