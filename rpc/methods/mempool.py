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
from rpc import deps
from mempool.select import PendingTxEntry, select_for_block

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
        if hasattr(pend, "list_raw_with_ts"):
            return ((h, raw, ts) for h, raw, ts in pend.list_raw_with_ts())  # type: ignore[attr-defined]
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


@method(
    "mempool.explain",
    desc="Explain whether a pending transaction is mineable and why.",
    aliases=("mempool_explain",),
)
def mempool_explain(tx_hash: str) -> dict:
    target = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    raw = None
    for h, raw_bytes, _ts in _iter_pending():
        if h == target:
            raw = raw_bytes
            break
    if raw is None:
        return {"hash": target, "status": "not_found"}

    ctx = deps.get_ctx()
    chain_id = getattr(ctx.cfg, "chain_id", None) if ctx is not None else None
    state_db = getattr(ctx, "state_db", None) if ctx is not None else None
    tx_index = getattr(ctx, "tx_index", None) if ctx is not None else None
    min_gas_price = 0
    if ctx is not None:
        try:
            min_gas_price = int(ctx.params.get("min_gas_price", 0))
        except Exception:
            min_gas_price = 0

    def _decode(raw_tx: bytes):
        if tx_methods is None:
            return None
        return tx_methods._decode_tx(raw_tx)  # type: ignore[attr-defined]

    def _signature_validator(tx_obj, decoded_obj):
        if tx_methods is None or decoded_obj is None:
            return
        tx_methods._verify_pq_signature(  # type: ignore[attr-defined]
            tx_obj, decoded_obj, chain_id=int(chain_id or 0)
        )

    selection = select_for_block(
        head_state={"chain_id": chain_id},
        limits={"max_gas": 0, "max_bytes": 0, "max_txs": 1},
        pending=[PendingTxEntry(hash_hex=target, raw=raw, tx=None)],
        decode=_decode,
        state_db=state_db,
        policy={"min_gas_price": min_gas_price},
        tx_index=tx_index,
        signature_validator=_signature_validator,
    )
    if selection.selected:
        return {
            "hash": target,
            "status": "eligible",
            "reason": None,
        }
    reason = selection.rejected_by_hash.get(target, "unknown")
    details = selection.rejected_details_by_hash.get(target)
    return {
        "hash": target,
        "status": "rejected",
        "reason": reason,
        "details": details,
        "rejected": dict(selection.rejected),
    }


__all__.append("mempool_explain")
