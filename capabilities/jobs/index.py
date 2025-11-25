from __future__ import annotations

"""
capabilities.jobs.index
-----------------------

Lightweight secondary indexes for **ResultRecord** objects:

- by caller (address / bytes) ordered by (height desc, created_at desc)
- by height (range queries)

This module can operate in two modes:

1) **Delegating** (default for SQLite-backed stores):
   Methods forward to the underlying `SqliteResultStore` which already maintains
   proper indexes in SQL.

2) **In-memory** (default for memory stores, or when `preload=True`):
   Builds/maintains sorted in-memory structures and can be kept up-to-date by
   calling `add()` / `remove()` as new results appear.

Usage
-----
    store = open_result_store("sqlite:///var/lib/animica/capabilities/results.db")
    index = ResultIndex(store)  # delegates to SQL indexes

    # Or force in-memory (e.g., for tests or when using MemoryResultStore)
    mem_store = MemoryResultStore()
    index = ResultIndex(mem_store, preload=True)

    # Query
    recent = index.list_recent(limit=20)
    mine   = index.list_by_caller(caller_addr_bytes, limit=50)
    rng    = index.list_by_height_range(min_height=1000, max_height=1200)

    # Keep in sync (when using in-memory mode)
    index.add(record)
    index.remove(record.task_id)
"""

from dataclasses import asdict, is_dataclass
from typing import Dict, Iterable, List, Optional, Tuple
import bisect
import time

from capabilities.errors import CapError
from capabilities.jobs.types import ResultRecord
from capabilities.jobs.result_store import (
    ResultStore,
    MemoryResultStore,
    SqliteResultStore,
)


def _to_map(rec: ResultRecord) -> dict:
    to_map = getattr(rec, "to_map", None)
    if callable(to_map):
        return to_map()
    if is_dataclass(rec):
        return asdict(rec)
    return {k: getattr(rec, k) for k in dir(rec) if not k.startswith("_") and not callable(getattr(rec, k))}


def _get_field(rec: ResultRecord, m: dict, name: str, default=None):
    if name in m:
        return m[name]
    return getattr(rec, name, default)


class ResultIndex:
    """
    Secondary index façade.

    If `store` is `SqliteResultStore` and `preload=False`, queries delegate to SQL.
    Otherwise, an in-memory index is built.

    For in-memory mode:
      - `add(rec)` and `remove(task_id)` keep the index in sync incrementally.
      - If you don't call those, use `rebuild()` to refresh from the store snapshot.
    """

    def __init__(self, store: ResultStore, *, preload: bool = False, max_cache: int = 1_000_000) -> None:
        self._store = store
        self._delegate = isinstance(store, SqliteResultStore) and not preload
        self._max_cache = int(max_cache)

        # In-memory structures
        # records: task_id -> ResultRecord
        self._records: Dict[bytes, ResultRecord] = {}
        # by_caller: caller -> sorted list of keys [(-height, -created_at, task_id)]
        self._by_caller: Dict[bytes, List[Tuple[int, int, bytes]]] = {}
        # by_height: height -> list of task_ids (in created_at desc best-effort)
        self._by_height: Dict[int, List[bytes]] = {}

        if not self._delegate:
            if preload:
                self.rebuild()

    # ---------------------------------------------------------------------
    # Public query API (delegates to SQL when available)
    # ---------------------------------------------------------------------

    def list_recent(self, *, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        if self._delegate:
            return self._store.list_recent(limit=limit, offset=offset)
        # Merge all heights by (height desc, created_at desc)
        # For scale, we avoid fully materializing; we create a heap from heads of per-caller lists
        # but a simpler approach is acceptable for moderate cache sizes.
        items: List[Tuple[int, int, bytes]] = []
        for caller_keys in self._by_caller.values():
            # Each caller list is already sorted (height desc, created_at desc via negative key)
            items.extend(caller_keys[: min(len(caller_keys), limit + offset)])
            if len(items) > (limit + offset) * 4:
                # Avoid quadratic blow-up: trim occasionally
                items.sort()
                items = items[: (limit + offset) * 2]
        items.sort()  # tuples are (-height, -created_at, task_id) so default ascending; that's OK
        # pick from the end for newest first
        sel = items[-(offset + limit) : -offset if offset else None]
        sel.reverse()
        return [self._records[tid] for _, _, tid in sel]

    def list_by_caller(self, caller: bytes, *, limit: int = 50, offset: int = 0) -> List[ResultRecord]:
        if self._delegate:
            return self._store.list_by_caller(caller, limit=limit, offset=offset)
        lst = self._by_caller.get(bytes(caller), [])
        # newest first: reverse of sorted (because we store negative keys)
        start = offset
        end = offset + limit
        # Keys are (-height, -created_at, task_id); slice in natural (ascending) then reverse range
        # Easier: index from the right
        n = len(lst)
        if start >= n:
            return []
        right_start = max(0, n - end)
        right_end = n - start
        chunk = lst[right_start:right_end]
        chunk.reverse()
        return [self._records[tid] for _, _, tid in chunk]

    def list_by_height_range(
        self,
        *,
        min_height: Optional[int] = None,
        max_height: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ResultRecord]:
        if self._delegate:
            # SQL store has no explicit range method; fallback to list_recent and filter.
            # Pull pages until we have enough or exhaust.
            acc: List[ResultRecord] = []
            page = 0
            page_size = max(limit + offset, 100)
            while len(acc) < offset + limit:
                batch = self._store.list_recent(limit=page_size, offset=page * page_size)
                if not batch:
                    break
                for rec in batch:
                    m = _to_map(rec)
                    h = int(_get_field(rec, m, "height", 0))
                    if min_height is not None and h < min_height:
                        continue
                    if max_height is not None and h > max_height:
                        continue
                    acc.append(rec)
                if len(batch) < page_size:
                    break
                page += 1
            return acc[offset : offset + limit]

        # In-memory: iterate heights in descending order, filter by range
        heights = sorted(self._by_height.keys(), reverse=True)
        out: List[ResultRecord] = []
        for h in heights:
            if max_height is not None and h > max_height:
                continue
            if min_height is not None and h < min_height:
                break  # because heights descending
            for tid in self._by_height[h]:
                out.append(self._records[tid])
                if len(out) >= offset + limit:
                    return out[offset : offset + limit]
        return out[offset : offset + limit]

    # ---------------------------------------------------------------------
    # Maintenance API (only relevant for in-memory mode)
    # ---------------------------------------------------------------------

    def add(self, rec: ResultRecord) -> None:
        """Insert/update a record in the in-memory index."""
        if self._delegate:
            # Nothing to do when delegating; SQL already indexed on insert.
            return
        m = _to_map(rec)
        tid: bytes = bytes(_get_field(rec, m, "task_id"))
        caller: bytes = bytes(_get_field(rec, m, "caller"))
        height: int = int(_get_field(rec, m, "height", 0))
        created_at: int = int(_get_field(rec, m, "created_at", int(time.time())))

        # Evict if we exceed cache capacity (simple FIFO by oldest created_at)
        if len(self._records) >= self._max_cache and tid not in self._records:
            self._evict_one()

        # Update master map
        self._records[tid] = rec

        # Update by_caller (sorted on (-height, -created_at))
        key = (-height, -created_at, tid)
        lst = self._by_caller.setdefault(caller, [])
        # Remove previous entry for tid if present
        _remove_from_sorted_keys(lst, tid)
        bisect.insort(lst, key)

        # Update by_height bucket
        bucket = self._by_height.setdefault(height, [])
        if tid not in bucket:
            bucket.append(tid)

    def remove(self, task_id: bytes) -> bool:
        """Remove a record from the in-memory index."""
        if self._delegate:
            return False
        tid = bytes(task_id)
        rec = self._records.pop(tid, None)
        if rec is None:
            return False
        m = _to_map(rec)
        caller: bytes = bytes(_get_field(rec, m, "caller"))
        height: int = int(_get_field(rec, m, "height", 0))
        # by_caller
        lst = self._by_caller.get(caller)
        if lst is not None:
            _remove_from_sorted_keys(lst, tid)
            if not lst:
                self._by_caller.pop(caller, None)
        # by_height
        hb = self._by_height.get(height)
        if hb is not None:
            try:
                hb.remove(tid)
            except ValueError:
                pass
            if not hb:
                self._by_height.pop(height, None)
        return True

    def rebuild(self, *, source: Optional[Iterable[ResultRecord]] = None) -> None:
        """
        Rebuild in-memory indexes from `source` iterable or from the underlying store snapshot.
        """
        if self._delegate:
            return
        self._records.clear()
        self._by_caller.clear()
        self._by_height.clear()

        it: Iterable[ResultRecord]
        if source is not None:
            it = source
        else:
            # Stream from store in pages using list_recent (height desc)
            page_size = 1000
            offset = 0
            def pager():
                nonlocal offset
                while True:
                    batch = self._store.list_recent(limit=page_size, offset=offset)
                    if not batch:
                        break
                    offset += len(batch)
                    for r in batch:
                        yield r
            it = pager()

        for rec in it:
            self.add(rec)

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    def _evict_one(self) -> None:
        """Evict the globally oldest record by created_at (best-effort)."""
        oldest_tid = None
        oldest_k = None  # (created_at, height)
        for caller, keys in self._by_caller.items():
            if not keys:
                continue
            # keys are sorted asc on (-height, -created_at); oldest is keys[0] in terms of created_at? No:
            # keys[-1] has smallest -created_at? Let's compute created_at from key.
            # Safer: peek both ends and compute created_at.
            k0 = keys[0]      # most negative -> highest height/created_at
            kN = keys[-1]     # least negative -> lowest height/created_at
            for k in (kN,):
                neg_h, neg_ct, tid = k
                created_at = -neg_ct
                height = -neg_h
                cmp_k = (created_at, height)
                if oldest_k is None or cmp_k < oldest_k:
                    oldest_k = cmp_k
                    oldest_tid = tid
        if oldest_tid is not None:
            self.remove(oldest_tid)


def _remove_from_sorted_keys(lst: List[Tuple[int, int, bytes]], tid: bytes) -> None:
    # Linear scan is acceptable for modest per-caller fanout; keeps code simple.
    for i in range(len(lst) - 1, -1, -1):
        if lst[i][2] == tid:
            del lst[i]
            return


__all__ = ["ResultIndex"]
