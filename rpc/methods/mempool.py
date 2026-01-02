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
from typing import Any, Iterable

from rpc.methods import method
from rpc import deps
from mempool.select import PendingTxEntry, select_for_block
from mempool.types import EffectiveFee
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


def _get_mempool_service() -> Any | None:
    if tx_methods is not None:
        getter = getattr(tx_methods, "_get_mempool_service", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                return None
    try:
        ctx = deps.get_ctx()
    except Exception:
        return None
    return getattr(ctx, "mempool", None) if ctx is not None else None


def _iter_pending() -> Iterable[tuple[str, bytes, float | None]]:
    """Yield (hash_hex, raw_bytes, ts) from the canonical mempool or fallback cache."""
    mempool_service = _get_mempool_service()
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=1000)
        log.info(
            "mempool._iter_pending: using mempool service, count=%d",
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


def _format_sender(sender: Any) -> str | None:
    if sender is None:
        return None
    if isinstance(sender, (bytes, bytearray)):
        return "0x" + bytes(sender).hex()
    if isinstance(sender, str):
        if sender.startswith("0x"):
            return sender
        if all(c in "0123456789abcdefABCDEF" for c in sender):
            return "0x" + sender
        return sender
    return str(sender)


def _hash_hex_to_bytes(hash_hex: str) -> bytes | None:
    text = hash_hex[2:] if hash_hex.startswith("0x") else hash_hex
    try:
        return bytes.fromhex(text)
    except Exception:
        return None


def _entry_details(
    *,
    entry: PendingTxEntry,
    raw: bytes,
    meta: Any | None,
    diagnostics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sender = getattr(meta, "sender", None) or getattr(entry.tx, "sender", None)
    nonce = getattr(meta, "nonce", None) or getattr(entry.tx, "nonce", None)
    fee = getattr(meta, "effective_fee_wei", None)
    if fee is None:
        try:
            if entry.tx is not None:
                fee = int(EffectiveFee.from_tx(entry.tx).effective_gas_price(None))
        except Exception:
            fee = None
    size = getattr(meta, "size_bytes", None)
    if size is None:
        try:
            size = len(raw)
        except Exception:
            size = None
    received_at = entry.received_at or getattr(meta, "first_seen", None)
    origin_peer = None
    if meta is not None:
        origin_peer = getattr(meta, "origin", None)
        if origin_peer is None and hasattr(meta, "local"):
            origin_peer = "local" if bool(getattr(meta, "local")) else "p2p"
    if origin_peer is None:
        origin_peer = "local"

    diag = diagnostics.get(entry.hash_hex, {})
    status = diag.get("status") or "eligible"
    reason = diag.get("reason")
    if status == "rejected":
        if isinstance(reason, str) and "nonce" in reason:
            status = "pending_nonce"
        elif reason in {"replaced", "replaced_by_fee", "replaced_by_nonce"}:
            status = "replaced"
        else:
            status = "rejected"
    return {
        "hash": entry.hash_hex,
        "from": _format_sender(sender),
        "nonce": int(nonce) if nonce is not None else None,
        "fee": fee,
        "received_at": received_at,
        "origin": origin_peer,
        "origin_peer": origin_peer,
        "size": size,
        "status": status,
        "reason": reason,
    }


@method(
    "mempool.getPending",
    desc="List pending transaction hashes currently held by the node.",
    aliases=("mempool_pending",),
)
def mempool_get_pending(verbose: bool | None = None) -> list[str] | list[dict]:
    mempool_service = _get_mempool_service()
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=1000)
        pending_hashes = [entry.hash_hex for entry in snapshot.entries]
        pending_hashes.sort()
        if not verbose:
            return pending_hashes
        diagnostics = mempool_service.diagnose(limit=len(pending_hashes) + 1)
        details: list[dict] = []
        pool = getattr(mempool_service, "pool", None)
        for entry in snapshot.entries:
            meta = None
            if pool is not None:
                hash_bytes = _hash_hex_to_bytes(entry.hash_hex)
                if hash_bytes is not None:
                    pool_entry = pool.index.get(hash_bytes)
                    meta = getattr(pool_entry, "meta", None) if pool_entry else None
            details.append(
                _entry_details(
                    entry=entry,
                    raw=entry.raw,
                    meta=meta,
                    diagnostics=diagnostics,
                )
            )
        return details

    pending_items = list(_iter_pending())
    pending_hashes = [h for h, _raw, _ts in pending_items]
    pending_hashes.sort()
    if not verbose:
        return pending_hashes
    diagnostics: dict[str, dict[str, Any]] = {}
    details = []
    for h, raw, ts in pending_items:
        entry = PendingTxEntry(hash_hex=h, raw=raw, tx=None, received_at=ts, expires_at=None)
        details.append(
            _entry_details(entry=entry, raw=raw, meta=None, diagnostics=diagnostics)
        )
    return details


@method(
    "mempool.getInfo",
    desc="Return mempool service identity and persistence path (if available).",
)
def mempool_get_info() -> dict[str, Any]:
    mempool_service = _get_mempool_service()
    if mempool_service is None:
        return {"enabled": False, "mempool_id": None, "mempool_path": None}
    persist_path = getattr(mempool_service, "_persist_path", None)
    return {
        "enabled": True,
        "mempool_id": hex(id(mempool_service)),
        "mempool_path": str(persist_path) if persist_path else None,
    }


@method(
    "mempool.getStats",
    desc="Return summary stats for the pending pool (count/bytes/age).",
    aliases=("mempool_stats",),
)
def mempool_get_stats() -> dict:
    mempool_service = _get_mempool_service()
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
__all__.append("mempool_get_status")


@method(
    "mempool.getStatus",
    desc="Return whether a transaction is known to the mempool and its current state.",
    aliases=("mempool_getStatus",),
)
def mempool_get_status(tx_hash: str) -> dict:
    target = tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"
    mempool_service = _get_mempool_service()
    if mempool_service is not None:
        snapshot = mempool_service.snapshot(limit=1000)
        for entry in snapshot.entries:
            if entry.hash_hex == target:
                return {
                    "hash": target,
                    "known": True,
                    "state": "pending",
                    "reason": None,
                }
        rejection = getattr(mempool_service, "get_rejection", None)
        if callable(rejection):
            rejected = rejection(target)
            if rejected:
                return {
                    "hash": target,
                    "known": True,
                    "state": "rejected",
                    "reason": rejected.get("reason"),
                    "details": rejected.get("details"),
                }

    for h, _raw, _ts in _iter_pending():
        if h == target:
            return {
                "hash": target,
                "known": True,
                "state": "pending",
                "reason": None,
                "source": "fallback",
            }

    return {"hash": target, "known": False, "state": "unknown", "reason": "not_found"}


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
