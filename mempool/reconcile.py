from __future__ import annotations

from typing import Any, Iterable

from mempool.tx_hash import tx_hash_hex as _tx_hash_hex


def _normalize_hash_hex(hash_hex: str) -> str:
    if not hash_hex:
        return hash_hex
    normalized = hash_hex if hash_hex.startswith("0x") else f"0x{hash_hex}"
    return normalized.lower()


def _extract_block_txs(block: Any) -> Iterable[Any]:
    if hasattr(block, "txs"):
        return getattr(block, "txs") or []
    if isinstance(block, dict):
        return block.get("txs", []) or []
    return []


def _canonical_hash_from_tx(tx: Any) -> str | None:
    if isinstance(tx, (bytes, bytearray, str, dict)):
        try:
            return _tx_hash_hex(tx)
        except Exception:
            return None
    raw = getattr(tx, "raw_cbor", None)
    if raw is None and hasattr(tx, "to_cbor"):
        try:
            raw = tx.to_cbor()
        except Exception:
            raw = None
    if raw:
        return _tx_hash_hex(raw)
    if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
        try:
            return "0x" + tx.hash().hex()
        except Exception:
            return None
    return None


def _tx_sender_nonce(tx: Any) -> tuple[bytes, int] | None:
    sender = None
    nonce = None

    if hasattr(tx, "unsigned"):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is not None:
            sender = getattr(unsigned, "sender", None)
            nonce = getattr(unsigned, "nonce", None)

    if isinstance(tx, dict):
        if sender is None:
            nested = tx.get("unsigned") or tx.get("tx") or {}
            if isinstance(nested, dict):
                sender = nested.get("sender") or nested.get("from") or sender
                nonce = nested.get("nonce", nonce)
        if sender is None:
            sender = tx.get("sender") or tx.get("from")
        if nonce is None:
            nonce = tx.get("nonce")

    if sender is None:
        sender = getattr(tx, "sender", getattr(tx, "from", None))
    if nonce is None:
        nonce = getattr(tx, "nonce", None)

    if isinstance(sender, str):
        if sender.startswith("0x"):
            try:
                sender = bytes.fromhex(sender[2:])
            except ValueError:
                return None
        else:
            return None
    if not isinstance(sender, (bytes, bytearray)):
        return None
    if nonce is None:
        return None
    try:
        return bytes(sender), int(nonce)
    except Exception:
        return None


def _collect_included_hashes(block: Any, tx_hashes: Iterable[str] | None) -> list[str]:
    included_hashes: list[str] = []
    if tx_hashes is not None:
        included_hashes = [_normalize_hash_hex(h) for h in tx_hashes if h]
    elif hasattr(block, "tx_hashes"):
        try:
            raw_hashes = getattr(block, "tx_hashes") or []
            included_hashes = [_normalize_hash_hex(h) for h in raw_hashes if h]
        except Exception:
            included_hashes = []
    elif isinstance(block, dict) and block.get("tx_hashes"):
        try:
            raw_hashes = block.get("tx_hashes") or []
            included_hashes = [_normalize_hash_hex(h) for h in raw_hashes if h]
        except Exception:
            included_hashes = []
    else:
        for tx in _extract_block_txs(block):
            tx_hash_hex = _canonical_hash_from_tx(tx)
            if tx_hash_hex:
                included_hashes.append(_normalize_hash_hex(tx_hash_hex))

    deduped: list[str] = []
    seen: set[str] = set()
    for tx_hash in included_hashes:
        if tx_hash in seen:
            continue
        seen.add(tx_hash)
        deduped.append(tx_hash)
    return deduped


def _collect_included_pairs(block: Any) -> set[tuple[bytes, int]]:
    out: set[tuple[bytes, int]] = set()
    for tx in _extract_block_txs(block):
        pair = _tx_sender_nonce(tx)
        if pair is not None:
            out.add(pair)
    return out


def _iter_legacy_pending_items(tx_methods: Any) -> list[tuple[str, bytes]]:
    pending_items: list[tuple[str, bytes]] = []
    pend = getattr(tx_methods, "_PEND", None)
    if pend is not None:
        if hasattr(pend, "list_raw") and callable(pend.list_raw):
            try:
                pending_items = list(pend.list_raw())
            except Exception:
                pending_items = []
        elif hasattr(pend, "items") and callable(pend.items):
            try:
                pending_items = list(pend.items())
            except Exception:
                pending_items = []
    if pending_items:
        return pending_items
    fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
    return list(fallback.items())


def _remove_legacy_pending_hashes(tx_methods: Any, tx_hashes: Iterable[str]) -> int:
    removed = 0
    remover = getattr(tx_methods, "_pending_remove", None)
    if not callable(remover):
        return removed
    for tx_hash in tx_hashes:
        try:
            if remover(tx_hash):
                removed += 1
        except Exception:
            continue
    return removed


def _collect_conflicting_legacy_hashes(
    tx_methods: Any,
    *,
    included_pairs: set[tuple[bytes, int]],
    included_hashes: set[str],
) -> list[str]:
    conflicting_hashes: list[str] = []
    pending_items = _iter_legacy_pending_items(tx_methods)
    decoder = getattr(tx_methods, "_decode_tx", None)
    if not callable(decoder):
        return conflicting_hashes

    for pending_hash, raw in pending_items:
        normalized_hash = _normalize_hash_hex(str(pending_hash))
        if normalized_hash in included_hashes:
            continue
        try:
            decoded, _obj = decoder(raw)
        except Exception:
            continue
        pair = _tx_sender_nonce(decoded)
        if pair is None or pair not in included_pairs:
            continue
        conflicting_hashes.append(normalized_hash)
    return conflicting_hashes


def _collect_conflicting_mempool_hashes(
    mempool_service: Any,
    tx_methods: Any,
    *,
    included_pairs: set[tuple[bytes, int]],
    included_hashes: set[str],
) -> list[str]:
    conflicting_hashes: list[str] = []
    decoder = getattr(tx_methods, "_decode_tx", None)
    if not callable(decoder):
        return conflicting_hashes

    try:
        pool = getattr(mempool_service, "pool", None)
        snapshot_limit = len(pool) + 1 if pool is not None and hasattr(pool, "__len__") else 1000
        snapshot = mempool_service.snapshot(limit=snapshot_limit)
        entries = list(getattr(snapshot, "entries", []) or [])
    except Exception:
        return conflicting_hashes

    for entry in entries:
        hash_hex = _normalize_hash_hex(str(getattr(entry, "hash_hex", "")))
        if not hash_hex or hash_hex in included_hashes:
            continue
        raw = getattr(entry, "raw", None)
        if not isinstance(raw, (bytes, bytearray)):
            continue
        try:
            decoded, _obj = decoder(bytes(raw))
        except Exception:
            continue
        pair = _tx_sender_nonce(decoded)
        if pair is None or pair not in included_pairs:
            continue
        conflicting_hashes.append(hash_hex)

    return conflicting_hashes


def _notify_txrelay_confirmed(ctx: Any, included_hashes: list[str]) -> int:
    services = [
        getattr(ctx, "p2p_service", None),
        getattr(ctx, "core_p2p_service", None),
    ]
    for service in services:
        if service is None:
            continue
        relay = (
            getattr(service, "tx_relay_service", None)
            or getattr(service, "_txrelay", None)
            or getattr(service, "_tx_relay", None)
        )
        if relay is None:
            continue
        handler = getattr(relay, "on_block_accepted", None)
        if not callable(handler):
            continue
        try:
            result = handler(included_hashes)
            if isinstance(result, dict):
                return int(result.get("confirmed", 0))
            return len(included_hashes)
        except Exception:
            continue
    return 0


def on_block_accepted(
    block: Any,
    new_state: Any | None = None,
    *,
    tx_hashes: Iterable[str] | None = None,
) -> dict[str, int]:
    """
    Reconcile all local pending/import state against an accepted canonical block.

    This evicts included transactions from canonical and legacy pending stores,
    drops sender+nonce conflicts, marks relay/import tracking as confirmed, and
    revalidates remaining mempool state.
    """
    try:
        from rpc import deps

        ctx = deps.get_ctx()
        mempool_service = getattr(ctx, "mempool", None)
    except Exception:
        ctx = None
        mempool_service = None

    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        tx_methods = None

    included_hash_list = _collect_included_hashes(block, tx_hashes)
    included_hashes = set(included_hash_list)
    included_pairs = _collect_included_pairs(block)

    evicted_mempool = 0
    if mempool_service is not None and included_hash_list:
        try:
            evicted_mempool = int(mempool_service.remove_included(included_hash_list) or 0)
        except Exception:
            evicted_mempool = 0

    evicted_legacy = 0
    if tx_methods is not None and included_hash_list:
        evicted_legacy = _remove_legacy_pending_hashes(tx_methods, included_hash_list)

    conflict_hashes: list[str] = []
    if included_pairs and tx_methods is not None:
        conflict_hashes.extend(
            _collect_conflicting_legacy_hashes(
                tx_methods,
                included_pairs=included_pairs,
                included_hashes=included_hashes,
            )
        )
        if mempool_service is not None:
            conflict_hashes.extend(
                _collect_conflicting_mempool_hashes(
                    mempool_service,
                    tx_methods,
                    included_pairs=included_pairs,
                    included_hashes=included_hashes,
                )
            )

    dedup_conflicts: list[str] = []
    seen_conflicts: set[str] = set()
    for tx_hash in conflict_hashes:
        if tx_hash in included_hashes or tx_hash in seen_conflicts:
            continue
        seen_conflicts.add(tx_hash)
        dedup_conflicts.append(tx_hash)

    conflicts_mempool = 0
    if mempool_service is not None and dedup_conflicts:
        try:
            conflicts_mempool = int(mempool_service.remove_included(dedup_conflicts) or 0)
        except Exception:
            conflicts_mempool = 0

    conflicts_legacy = 0
    if tx_methods is not None and dedup_conflicts:
        conflicts_legacy = _remove_legacy_pending_hashes(tx_methods, dedup_conflicts)

    if mempool_service is not None:
        try:
            mempool_service.revalidate()
        except Exception:
            pass

    relay_confirmed = 0
    if ctx is not None and included_hash_list:
        relay_confirmed = _notify_txrelay_confirmed(ctx, included_hash_list)

    return {
        "evicted": evicted_mempool + evicted_legacy,
        "conflicts": conflicts_mempool + conflicts_legacy,
        "relay_confirmed": relay_confirmed,
    }


__all__ = ["on_block_accepted"]
