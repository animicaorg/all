"""
randomness.beacon.history
=========================

A small in-memory ring buffer for finalized beacon outputs with simple,
index-free pagination helpers.

Design goals
------------
- O(1) append and eviction.
- O(1) lookup by round_id for items still in the window.
- O(log n + k) pagination using round_id anchors (bisect over a monotonic id list).
- Thread-safe for light concurrent readers/writers.

This module is intentionally storage-agnostic. For persistence, mirror
appends into your KV/DB layer and reconstruct the buffer on boot.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple
import threading
import bisect

from randomness.types.core import BeaconOut, RoundId


class BeaconHistory:
    """
    Ring buffer of recent :class:`BeaconOut` items keyed by round id.

    Assumes strictly increasing round ids on append. If you need to deal with
    reorg-like rewinds, call :meth:`truncate_from` and re-append.
    """

    __slots__ = (
        "_cap",
        "_buf",
        "_rids",
        "_by_id",
        "_lock",
    )

    def __init__(self, capacity: int = 2048):
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._cap: int = int(capacity)
        self._buf: Deque[BeaconOut] = deque()
        self._rids: List[int] = []  # ascending RoundId list aligned with _buf
        self._by_id: Dict[int, BeaconOut] = {}
        self._lock = threading.RLock()

    # ------------------------ properties ------------------------

    @property
    def capacity(self) -> int:
        return self._cap

    def __len__(self) -> int:  # pragma: no cover - trivial
        with self._lock:
            return len(self._rids)

    def min_round_id(self) -> Optional[int]:
        """Oldest round id in buffer, or None if empty."""
        with self._lock:
            return self._rids[0] if self._rids else None

    def max_round_id(self) -> Optional[int]:
        """Newest round id in buffer, or None if empty."""
        with self._lock:
            return self._rids[-1] if self._rids else None

    # ------------------------ mutation ------------------------

    def append(self, item: BeaconOut) -> None:
        """
        Append a finalized beacon. Round ids must be strictly increasing.
        Evicts the oldest item when capacity is exceeded.
        """
        rid: int = int(item.round_id)  # RoundId is an int-like
        with self._lock:
            if self._rids and rid <= self._rids[-1]:
                raise ValueError(
                    f"round_id must increase (got {rid}, last {self._rids[-1]})"
                )
            self._buf.append(item)
            self._rids.append(rid)
            self._by_id[rid] = item

            # Evict if over capacity
            if len(self._rids) > self._cap:
                oldest = self._buf.popleft()
                oldest_id = int(oldest.round_id)
                del self._by_id[oldest_id]
                # pop first rid
                self._rids.pop(0)

    def truncate_from(self, round_id: RoundId) -> None:
        """
        Truncate history starting *from* the given round id (inclusive),
        dropping that id and everything after it. No-op if round id not present.
        """
        rid = int(round_id)
        with self._lock:
            idx = self._index_of(rid)
            if idx is None:
                return
            # drop tail inclusive
            to_drop = self._rids[idx:]
            for r in to_drop:
                self._by_id.pop(r, None)
            # rebuild structures
            keep_count = idx
            if keep_count == 0:
                self._buf.clear()
                self._rids.clear()
                return
            # keep first idx elements of deque: simplest is to rebuild
            kept: Deque[BeaconOut] = deque()
            for i, rec in enumerate(self._buf):
                if i >= keep_count:
                    break
                kept.append(rec)
            self._buf = kept
            self._rids = self._rids[:keep_count]

    # ------------------------ lookup ------------------------

    def latest(self) -> Optional[BeaconOut]:
        """Return newest item, or None if empty."""
        with self._lock:
            return self._buf[-1] if self._rids else None

    def get(self, round_id: RoundId) -> Optional[BeaconOut]:
        """Return the item for round_id if still retained."""
        rid = int(round_id)
        with self._lock:
            return self._by_id.get(rid)

    def window(self, start_inclusive: RoundId, end_inclusive: RoundId) -> List[BeaconOut]:
        """
        Return a contiguous window between two round ids (inclusive),
        clipped to what the buffer retains. Returns [] if no overlap.
        """
        a = int(start_inclusive)
        b = int(end_inclusive)
        if b < a:
            a, b = b, a
        with self._lock:
            if not self._rids:
                return []
            # leftmost idx >= a ; rightmost idx <= b
            li = bisect.bisect_left(self._rids, a)
            ri = bisect.bisect_right(self._rids, b) - 1
            if li >= len(self._rids) or ri < 0 or li > ri:
                return []
            return list(self._slice(li, ri + 1))

    # ------------------------ pagination ------------------------

    def paginate(
        self,
        *,
        start_round: Optional[RoundId] = None,
        limit: int = 50,
        direction: str = "backward",
    ) -> List[BeaconOut]:
        """
        Page through history using an anchor round id.

        Parameters
        ----------
        start_round : Optional[RoundId]
            Anchor round id. If None, uses newest for backward, oldest for forward.
            If not present, we anchor at the insertion point as if it existed.
        limit : int
            Max number of items to return (clamped to capacity).
        direction : str
            'backward' returns items at or before the anchor (newest first).
            'forward' returns items at or after the anchor (oldest first).

        Returns
        -------
        List[BeaconOut]
        """
        if limit <= 0:
            return []
        limit = min(limit, self._cap)
        direction = direction.lower()
        if direction not in ("backward", "forward"):
            raise ValueError("direction must be 'backward' or 'forward'")

        with self._lock:
            n = len(self._rids)
            if n == 0:
                return []

            if start_round is None:
                if direction == "backward":
                    # newest → older
                    start_idx = n - 1
                else:
                    # oldest → newer
                    start_idx = 0
            else:
                rid = int(start_round)
                if direction == "backward":
                    # want index of last id <= rid
                    pos = bisect.bisect_right(self._rids, rid) - 1
                    if pos < 0:
                        return []
                    start_idx = pos
                else:
                    # want index of first id >= rid
                    pos = bisect.bisect_left(self._rids, rid)
                    if pos >= n:
                        return []
                    start_idx = pos

            if direction == "backward":
                # slice [start_idx down to start_idx - limit + 1]
                lo = max(0, start_idx - limit + 1)
                hi = start_idx + 1
                return list(reversed(list(self._slice(lo, hi))))
            else:
                # slice [start_idx up to start_idx + limit)
                lo = start_idx
                hi = min(n, start_idx + limit)
                return list(self._slice(lo, hi))

    # ------------------------ internals ------------------------

    def _index_of(self, rid: int) -> Optional[int]:
        """Binary search for rid; returns index or None."""
        idx = bisect.bisect_left(self._rids, rid)
        if idx != len(self._rids) and self._rids[idx] == rid:
            return idx
        return None

    def _slice(self, lo: int, hi: int) -> Iterable[BeaconOut]:
        """
        Yield items for indices [lo, hi) from the deque. Since deque does not
        support slicing efficiently, we iterate—bounded by capacity and used on
        small windows; acceptable for pagination.
        """
        # Fast path: if we often page near the end, iterate once.
        for i, rec in enumerate(self._buf):
            if i < lo:
                continue
            if i >= hi:
                break
            yield rec


__all__ = ["BeaconHistory"]
