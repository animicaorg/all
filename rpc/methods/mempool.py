"""Mempool RPC methods (fallback, in-memory only).

This module exposes simple introspection endpoints backed by the same
in-process pending cache used by ``tx.sendRawTransaction`` when a full
mempool subsystem is not available. The goal is to avoid "Method not
found" errors for operators who need basic visibility during bring-up.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable

from rpc.methods import method
from rpc import deps
from mempool.select import PendingTxEntry, select_for_block
from core.types.tx import Tx
from core.utils.tx import TxNormalizationError, normalize_tx

log = logging.getLogger(__name__)

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
    """Yield (hash_hex, raw_bytes, ts) from the canonical mempool or fallback cache."""
    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None
    mempool_service = getattr(ctx, "mempool", None) if ctx is not None else None
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=1000)
        log.info(
            "mempool._iter_pending: using ctx.mempool, count=%d",
            len(snapshot.entries),
        )
        return (
            (entry.hash_hex, entry.raw, entry.received_at)
            for entry in snapshot.entries
        )
    if tx_methods is None:
        log.info("mempool._iter_pending: tx_methods is None, returning empty")
        return []
    # Prefer real pool if exposed
    pend = getattr(tx_methods, "_PEND", None)
    if pend is not None:
        if hasattr(pend, "list_raw_with_ts"):
            result = list(pend.list_raw_with_ts())  # type: ignore[attr-defined]
            log.info(
                "mempool._iter_pending: using _PEND.list_raw_with_ts(), count=%d",
                len(result),
            )
            return ((h, raw, ts) for h, raw, ts in result)
        if hasattr(pend, "items"):
            result = list(pend.items())  # type: ignore[attr-defined]
            log.info(
                "mempool._iter_pending: using _PEND.items(), count=%d",
                len(result),
            )
            return ((h, raw, None) for h, raw in result)
        if hasattr(pend, "list_raw"):
            result = list(pend.list_raw())  # type: ignore[attr-defined]
            log.info(
                "mempool._iter_pending: using _PEND.list_raw(), count=%d",
                len(result),
            )
            return ((h, raw, None) for h, raw in result)
    # Fallback to in-process dicts
    cache = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
    ts_cache = getattr(tx_methods, "_FALLBACK_PENDING_TS", {}) or {}
    log.info(
        "mempool._iter_pending: using _FALLBACK_PENDING dict, count=%d",
        len(cache),
    )
    return ((h, raw, ts_cache.get(h)) for h, raw in cache.items())


@method(
    "mempool.getPending",
    desc="List pending transaction hashes currently held by the node.",
    aliases=("mempool_pending",),
)
def mempool_get_pending(verbose: bool | None = None) -> list[str] | list[dict]:
    pending_hashes = [h for h, _raw, _ts in _iter_pending()]
    pending_hashes.sort()
    if not verbose:
        return pending_hashes
    ctx = deps.get_ctx()
    mempool_service = getattr(ctx, "mempool", None)
    diagnostics = mempool_service.diagnose(limit=len(pending_hashes) + 1) if mempool_service else {}
    return [
        {
            "hash": h,
            "status": diagnostics.get(h, {}).get("status", "unknown"),
            "reason": diagnostics.get(h, {}).get("reason"),
        }
        for h in pending_hashes
    ]


@method(
    "mempool.getStats",
    desc="Return summary stats for the pending pool (count/bytes/age).",
    aliases=("mempool_stats",),
)
def mempool_get_stats() -> dict:
    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None
    mempool_service = getattr(ctx, "mempool", None) if ctx is not None else None
    if mempool_service is not None:
        stats = mempool_service.stats()
        return PendingStats(
            count=stats.get("count", 0),
            total_bytes=stats.get("totalBytes", 0),
            oldest_age_sec=None,
        ).as_dict()

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
    try:
        ctx = deps.get_ctx()
    except Exception:
        ctx = None
    mempool_service = getattr(ctx, "mempool", None) if ctx is not None else None
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=1000)
        for entry in snapshot.entries:
            if entry.hash_hex == target:
                raw = entry.raw
                break
        if raw is None:
            rejection = getattr(mempool_service, "get_rejection", None)
            if callable(rejection):
                rejected = rejection(target)
                if rejected:
                    return {
                        "hash": target,
                        "status": "rejected",
                        "reason": rejected.get("reason", "unknown"),
                        "details": rejected.get("details"),
                    }
    else:
        for h, raw_bytes, _ts in _iter_pending():
            if h == target:
                raw = raw_bytes
                break
    if raw is None:
        return {"hash": target, "status": "not_found", "reason": "not_found"}

    try:
        raw = normalize_tx(raw)
    except TxNormalizationError as exc:
        if mempool_service is not None:
            remover = getattr(mempool_service, "remove_included", None)
            if callable(remover):
                remover([target])
            recorder = getattr(mempool_service, "_record_rejection", None)
            if callable(recorder):
                recorder(target, exc.reason, exc.details)
        return {
            "hash": target,
            "status": "rejected",
            "reason": exc.reason,
            "details": exc.details,
        }
    except Exception as exc:
        return {
            "hash": target,
            "status": "rejected",
            "reason": "decode_error",
            "details": {"step": "normalize_tx", "error": str(exc)},
        }

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

    tx_obj = None
    if tx_methods is not None:
        try:
            decoded, _obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
            if isinstance(decoded, Tx):
                tx_obj = decoded
            elif isinstance(decoded, dict):
                from rpc.methods.miner import _normalize_tx_envelope, _construct_tx_from_dict
                normalized = _normalize_tx_envelope(decoded)
                tx_obj = _construct_tx_from_dict(normalized)
        except Exception:
            pass

    selection = select_for_block(
        head_state={"chain_id": chain_id},
        limits={"max_gas": 0, "max_bytes": 0, "max_txs": 1},
        pending=[PendingTxEntry(hash_hex=target, raw=raw, tx=tx_obj)],
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
            "reason": "eligible",
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


@method(
    "mempool.getRawTx",
    desc="Return raw CBOR bytes for a pending transaction hash (hex string).",
    aliases=("mempool_getRawTx",),
)
def mempool_get_raw_tx(tx_hash: str) -> dict:
    target = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    ctx = deps.get_ctx()
    mempool_service = getattr(ctx, "mempool", None)
    raw = None
    if mempool_service is not None:
        getter = getattr(mempool_service, "get_raw", None)
        if callable(getter):
            raw = getter(target)
        if raw is None:
            snapshot = mempool_service.snapshot(limit=1000)
            raw = snapshot.raw_by_hash.get(target)
    if raw is None:
        return {"hash": target, "raw": None}
    raw_bytes = normalize_tx(raw)
    return {"hash": target, "raw": "0x" + raw_bytes.hex()}


@method(
    "mempool.listRawTxs",
    desc="List pending transactions with raw CBOR hex payloads.",
    aliases=("mempool_listRawTxs",),
)
def mempool_list_raw_txs(limit: int | None = None) -> list[dict]:
    ctx = deps.get_ctx()
    mempool_service = getattr(ctx, "mempool", None)
    if mempool_service is None:
        return []
    lim = int(limit or 1000)
    snapshot = mempool_service.snapshot(limit=lim)
    entries = []
    for entry in snapshot.entries:
        raw = snapshot.raw_by_hash.get(entry.hash_hex, entry.raw)
        try:
            raw_bytes = normalize_tx(raw)
        except Exception:
            continue
        entries.append({"hash": entry.hash_hex, "raw": "0x" + raw_bytes.hex()})
        if len(entries) >= lim:
            break
    return entries


__all__.extend(["mempool_get_raw_tx", "mempool_list_raw_txs"])
