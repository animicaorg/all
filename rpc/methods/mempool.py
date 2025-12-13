"""Mempool RPC methods (fallback, in-memory only).

This module exposes simple introspection endpoints backed by the same
in-process pending cache used by ``tx.sendRawTransaction`` when a full
mempool subsystem is not available. The goal is to avoid "Method not
found" errors for operators who need basic visibility during bring-up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from rpc.methods import method

try:  # pragma: no cover - optional dependency used for shared pending cache
    from rpc.methods import tx as tx_methods
except Exception:  # pragma: no cover
    tx_methods = None  # type: ignore


@dataclass
class PendingStats:
    count: int
    total_bytes: int
    oldest_age_sec: float | None

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "totalBytes": self.total_bytes,
            "oldestAgeSec": self.oldest_age_sec,
        }


def _iter_pending() -> Iterable[tuple[str, bytes, float | None]]:
    """Yield (hash_hex, raw_bytes, ts) from the fallback pending cache."""
    if tx_methods is None:
        return []
    # Prefer real pool if exposed
    pend = getattr(tx_methods, "_PEND", None)
    if pend is not None:
        if hasattr(pend, "items"):
            return ((h, raw, None) for h, raw in pend.items())  # type: ignore[attr-defined]
        if hasattr(pend, "list_raw"):
            return ((h, raw, None) for h, raw in pend.list_raw())  # type: ignore[attr-defined]
    # Fallback to in-process dicts
    cache = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
    ts_cache = getattr(tx_methods, "_FALLBACK_PENDING_TS", {}) or {}
    return ((h, raw, ts_cache.get(h)) for h, raw in cache.items())


@method(
    "mempool.getPending",
    desc="List pending transaction hashes currently held by the node.",
    aliases=("mempool_pending",),
)
def mempool_get_pending() -> list[str]:
    pending_hashes = [h for h, _raw, _ts in _iter_pending()]
    pending_hashes.sort()
    return pending_hashes


@method(
    "mempool.getStats",
    desc="Return summary stats for the pending pool (count/bytes/age).",
    aliases=("mempool_stats",),
)
def mempool_get_stats() -> dict:
    total_bytes = 0
    oldest_ts: float | None = None
    count = 0
    now = time.time()
    for _h, raw, ts in _iter_pending():
        count += 1
        total_bytes += len(raw)
        if ts is not None:
            if oldest_ts is None or ts < oldest_ts:
                oldest_ts = ts
    oldest_age = None if oldest_ts is None else max(0.0, now - oldest_ts)
    return PendingStats(count=count, total_bytes=total_bytes, oldest_age_sec=oldest_age).as_dict()


__all__ = ["mempool_get_pending", "mempool_get_stats"]
