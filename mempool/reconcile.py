from __future__ import annotations

from typing import Any, Iterable

from core.utils.hash import sha3_256


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
    raw = getattr(tx, "raw_cbor", None)
    if raw is None and hasattr(tx, "to_cbor"):
        try:
            raw = tx.to_cbor()
        except Exception:
            raw = None
    if raw:
        return "0x" + sha3_256(raw).hex()
    if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
        try:
            return "0x" + tx.hash().hex()
        except Exception:
            return None
    return None


def on_block_accepted(
    block: Any,
    new_state: Any | None = None,
    *,
    tx_hashes: Iterable[str] | None = None,
) -> dict[str, int]:
    """
    Reconcile the pending pool against an accepted block.

    Removes included tx hashes and conflicting sender+nonce entries.
    """
    try:
        from rpc.methods import tx as tx_methods
    except Exception:
        return {"evicted": 0, "conflicts": 0}

    included_hashes: list[str] = []
    if tx_hashes is not None:
        included_hashes = [_normalize_hash_hex(h) for h in tx_hashes if h]
    else:
        for tx in _extract_block_txs(block):
            tx_hash_hex = _canonical_hash_from_tx(tx)
            if tx_hash_hex:
                included_hashes.append(_normalize_hash_hex(tx_hash_hex))

    evicted = 0
    for h in included_hashes:
        try:
            removed_flag = tx_methods._pending_remove(h)  # type: ignore[attr-defined]
            if removed_flag:
                evicted += 1
        except Exception:
            continue

    conflicts = 0
    included_pairs: set[tuple[bytes, int]] = set()
    for tx in _extract_block_txs(block):
        sender = None
        nonce = None
        if hasattr(tx, "unsigned"):
            unsigned = getattr(tx, "unsigned", None)
            if unsigned is not None:
                sender = getattr(unsigned, "sender", None)
                nonce = getattr(unsigned, "nonce", None)
        if sender is None:
            sender = getattr(tx, "sender", getattr(tx, "from", None))
        if nonce is None:
            nonce = getattr(tx, "nonce", None)
        if isinstance(sender, str) and sender.startswith("0x"):
            try:
                sender = bytes.fromhex(sender[2:])
            except ValueError:
                sender = None
        if sender is not None and nonce is not None:
            included_pairs.add((bytes(sender), int(nonce)))

    if included_pairs:
        pending_items: list[tuple[str, bytes]] = []
        pend = getattr(tx_methods, "_PEND", None)
        if pend is not None:
            if hasattr(pend, "list_raw") and callable(pend.list_raw):
                pending_items = list(pend.list_raw())
            elif hasattr(pend, "items") and callable(pend.items):
                pending_items = list(pend.items())

        if not pending_items:
            fallback = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
            pending_items = list(fallback.items())

        for pending_hash, raw in pending_items:
            try:
                decoded, _obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                tx_obj = decoded
                if isinstance(decoded, dict) and hasattr(tx_methods, "_decode_tx"):
                    tx_obj = decoded
                sender = None
                nonce = None
                if hasattr(tx_obj, "unsigned"):
                    unsigned = getattr(tx_obj, "unsigned", None)
                    if unsigned is not None:
                        sender = getattr(unsigned, "sender", None)
                        nonce = getattr(unsigned, "nonce", None)
                if sender is None:
                    sender = getattr(tx_obj, "sender", getattr(tx_obj, "from", None))
                if nonce is None:
                    nonce = getattr(tx_obj, "nonce", None)
                if isinstance(sender, str) and sender.startswith("0x"):
                    sender = bytes.fromhex(sender[2:])
                if sender is None or nonce is None:
                    continue
                if (bytes(sender), int(nonce)) not in included_pairs:
                    continue
                removed_flag = tx_methods._pending_remove(pending_hash)  # type: ignore[attr-defined]
                if removed_flag:
                    conflicts += 1
            except Exception:
                continue

    return {"evicted": evicted, "conflicts": conflicts}


__all__ = ["on_block_accepted"]
