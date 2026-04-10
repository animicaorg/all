"""
Per-block result cache for fast `read_result(task_id)` lookups.

Design goals
------------
- **Fast path**: in-memory O(1) get/put using an OrderedDict as a tiny LRU.
- **Block awareness**: entries are tagged with the height at which a result
  became available; old entries are evicted when advancing the head.
- **Reorg safety**: on rewind, drop entries from heights above the new head.
- **Thread-safe**: lightweight RLock guards mutations/reads.

Intended usage
--------------
- The capabilities/jobs resolver inserts ResultRecords as they become available
  (typically height H); contracts read them in the next block (H+1).
- The cache keeps only a small sliding window (e.g., 3 blocks) worth of data.

This cache is **best-effort**: the authoritative store remains the persistent
jobs result store. On a miss, upstream code may fall back to that store.

Types
-----
`ResultRecord` is imported for typing only; absence of the module at runtime
won't break this cache.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import (TYPE_CHECKING, Any, Dict, MutableMapping, Optional, Set,
                    Tuple)

if TYPE_CHECKING:
    try:
        # Only for type hints; do not import at runtime in tight loops.
        from capabilities.jobs.types import ResultRecord  # pragma: no cover
    except Exception:  # pragma: no cover
        ResultRecord = Any  # type: ignore[misc]
else:  # pragma: no cover
    ResultRecord = Any  # type: ignore[misc]


def _norm_task_id(task_id: bytes | bytearray | memoryview | str) -> bytes:
    """Normalize task_id into raw bytes; accept hex strings with or without 0x."""
    if isinstance(task_id, (bytes, bytearray, memoryview)):
        return bytes(task_id)
    if isinstance(task_id, str):
        s = task_id[2:] if task_id.startswith(("0x", "0X")) else task_id
        if len(s) % 2 != 0:
            # enforce even-length hex; be explicit rather than silently padding
            raise ValueError("task_id hex string must have even length")
        try:
            return bytes.fromhex(s)
        except ValueError as e:
            raise ValueError(f"invalid hex task_id: {e}") from e
    raise TypeError(f"unsupported task_id type: {type(task_id).__name__}")


@dataclass(frozen=True)
class _Entry:
    height: int
    value: ResultRecord


class ResultStateCache:
    """
    Small, per-process cache keyed by task_id (bytes) → ResultRecord.

    Parameters
    ----------
    max_items : int
        Hard cap on number of cached items. Older (LRU) entries are evicted
        when the cap is exceeded.
    keep_blocks : int
        How many recent block-heights to retain. When the head advances to H,
        all entries with height <= H - keep_blocks are evicted.
    """

    __slots__ = (
        "_lock",
        "_cache",
        "_by_height",
        "_max_items",
        "_keep_blocks",
        "_current_height",
        "_hits",
        "_misses",
        "_puts",
        "_evictions",
        "_reorg_resets",
    )

    def __init__(self, max_items: int = 10_000, keep_blocks: int = 3) -> None:
        if max_items <= 0:
            raise ValueError("max_items must be > 0")
        if keep_blocks <= 0:
            raise ValueError("keep_blocks must be > 0")
        self._lock = RLock()
        self._cache: "OrderedDict[bytes, _Entry]" = OrderedDict()
        self._by_height: Dict[int, Set[bytes]] = {}
        self._max_items = int(max_items)
        self._keep_blocks = int(keep_blocks)
        self._current_height: Optional[int] = None

        # stats
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._evictions = 0
        self._reorg_resets = 0

    # ------------------------ Block lifecycle ------------------------

    def begin_block(self, height: int) -> None:
        """
        Notify the cache that processing for `height` has begun.

        - If height increases, evict entries older than the sliding window.
        - If height decreases (reorg), drop entries from heights > height.
        """
        if height < 0:
            raise ValueError("height must be non-negative")

        with self._lock:
            prev = self._current_height
            self._current_height = height

            if prev is None:
                # First call: nothing to evict yet
                return

            if height > prev:
                # Normal forward progress: evict old heights
                cutoff = max(0, height - self._keep_blocks)
                self._evict_heights_at_most(cutoff)
            elif height < prev:
                # Reorg / rewind: drop future heights strictly greater than `height`
                self._evict_heights_greater_than(height)
                self._reorg_resets += 1

            # Enforce item cap regardless
            self._enforce_item_cap_locked()

    # ------------------------ Core operations ------------------------

    def put(
        self,
        task_id: bytes | bytearray | memoryview | str,
        value: ResultRecord,
        *,
        available_height: Optional[int] = None,
    ) -> None:
        """
        Insert/replace a ResultRecord for task_id.

        `available_height` should be the block height where the result became
        visible (usually H). If omitted, the current head (if known) is used,
        otherwise height 0 is assumed.
        """
        tid = _norm_task_id(task_id)
        with self._lock:
            h = (
                int(available_height)
                if available_height is not None
                else (self._current_height if self._current_height is not None else 0)
            )
            entry = _Entry(height=h, value=value)

            # Update LRU ordering: remove → reinsert at end
            if tid in self._cache:
                # remove old height index
                old = self._cache.pop(tid)
                bucket = self._by_height.get(old.height)
                if bucket:
                    bucket.discard(tid)
                    if not bucket:
                        self._by_height.pop(old.height, None)

            self._cache[tid] = entry  # LRU: newest at end
            self._by_height.setdefault(h, set()).add(tid)
            self._puts += 1

            # Enforce sliding window relative to current height, if known
            if self._current_height is not None:
                cutoff = max(0, self._current_height - self._keep_blocks)
                self._evict_heights_at_most(cutoff)

            # Enforce item cap
            self._enforce_item_cap_locked()

    def get(
        self, task_id: bytes | bytearray | memoryview | str
    ) -> Optional[ResultRecord]:
        """
        Fetch a ResultRecord by task_id. Returns None if absent.
        Touches the entry to keep LRU ordering fresh.
        """
        tid = _norm_task_id(task_id)
        with self._lock:
            entry = self._cache.get(tid)
            if entry is None:
                self._misses += 1
                return None

            # LRU touch: move to end
            self._cache.move_to_end(tid, last=True)
            self._hits += 1
            return entry.value

    def has(self, task_id: bytes | bytearray | memoryview | str) -> bool:
        """Return True if the cache currently holds task_id."""
        tid = _norm_task_id(task_id)
        with self._lock:
            return tid in self._cache

    # ------------------------ Introspection ------------------------

    @property
    def current_height(self) -> Optional[int]:
        with self._lock:
            return self._current_height

    def __len__(self) -> int:  # pragma: no cover - trivial
        with self._lock:
            return len(self._cache)

    def stats(self) -> Dict[str, int]:
        """Return basic counters suitable for metrics export."""
        with self._lock:
            return {
                "items": len(self._cache),
                "heights_tracked": len(self._by_height),
                "hits": self._hits,
                "misses": self._misses,
                "puts": self._puts,
                "evictions": self._evictions,
                "reorg_resets": self._reorg_resets,
            }

    def clear(self) -> None:
        """Drop all entries (used in tests or on fatal errors)."""
        with self._lock:
            self._cache.clear()
            self._by_height.clear()

    # ------------------------ Internal helpers ------------------------

    def _evict_heights_at_most(self, cutoff: int) -> None:
        """Evict entries whose height <= cutoff."""
        # Find all heights <= cutoff that we currently track
        targets = [h for h in self._by_height.keys() if h <= cutoff]
        for h in targets:
            tids = self._by_height.pop(h, set())
            for tid in tids:
                if tid in self._cache:
                    self._cache.pop(tid, None)
                    self._evictions += 1

    def _evict_heights_greater_than(self, height: int) -> None:
        """Evict entries whose height > the given height (reorg safety)."""
        targets = [h for h in self._by_height.keys() if h > height]
        for h in targets:
            tids = self._by_height.pop(h, set())
            for tid in tids:
                if tid in self._cache:
                    self._cache.pop(tid, None)
                    self._evictions += 1

    def _enforce_item_cap_locked(self) -> None:
        """Pop LRU entries until the item cap is satisfied."""
        while len(self._cache) > self._max_items:
            tid, entry = self._cache.popitem(last=False)  # oldest
            bucket = self._by_height.get(entry.height)
            if bucket:
                bucket.discard(tid)
                if not bucket:
                    self._by_height.pop(entry.height, None)
            self._evictions += 1
