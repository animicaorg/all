"""
mempool.reorg
=============

Utilities to keep the mempool consistent across chain reorgs.

Responsibilities
----------------
1) **Re-inject orphaned txs** from blocks that fell off the canonical chain.
2) **Drop/confirm txs** that (re-)landed on the new canonical chain.
3) **Handle replacements**: if a (sender, nonce) is now occupied by a
   *different* included tx on the new branch, do not re-inject the old one.
4) **Revert receipts** for unconfirmed txs if the pool tracks local receipts.
5) **Refresh chain nonces** for affected senders so sequence/readiness is correct.

This module is deliberately *duck-typed* to work with different pool and chain
implementations. It will opportunistically call the following methods if present:

Pool (zero or more of):
  - add(tx) or add(tx, meta=...)
  - revalidate_and_add(tx)
  - remove(tx_hash) | discard(tx_hash) | mark_confirmed(tx_hash)
  - remove_many([tx_hash, ...]) | pop_many([...])
  - on_reorg_tx_unconfirmed(tx_hash)   # optional receipt rollback hook
  - on_chain_nonce_change(address, new_nonce)    # optional nonce-refresh hook
  - sequence.update_chain_nonce(address, new_nonce)  # optional (nested)
  - recompute_readiness_for_senders({address, ...})  # optional

Chain view (one or more of):
  - get_nonce(address) -> int
  - tx_included(tx_hash) -> bool
  - current_height() -> int

Block objects should expose `txs` or `transactions` (iterable of txs).
Transaction objects should expose:
  - hash / tx_hash  (bytes or hex-string)    [required for indexing]
  - sender / from / from_addr                [optional; useful for replacements]
  - nonce                                     [optional; useful for replacements]

If any of the above are missing, we fall back to best-effort behavior.

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import (Any, Dict, Iterable, Iterator, List, Optional, Sequence,
                    Set, Tuple)

logger = logging.getLogger(__name__)


# -------------------------
# Small helpers
# -------------------------


def _first_attr(obj: Any, names: Sequence[str], default: Any = None) -> Any:
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    # mapping/dict access fallback
    try:
        for n in names:
            if n in obj:
                return obj[n]
    except Exception:
        pass
    return default


def _txs_of_block(block: Any) -> Iterable[Any]:
    txs = _first_attr(block, ("txs", "transactions", "tx_list"), None)
    return txs or ()


def _tx_hash(tx: Any) -> bytes:
    h = _first_attr(tx, ("tx_hash", "hash"), None)
    if h is None:
        return b""
    if isinstance(h, (bytes, bytearray)):
        return bytes(h)
    if isinstance(h, str):
        s = h.strip()
        if s.startswith("0x"):
            s = s[2:]
        # try hex
        try:
            return bytes.fromhex(s)
        except Exception:
            return s.encode("utf-8", "ignore")
    try:
        return bytes(h)
    except Exception:
        return str(h).encode("utf-8", "ignore")


def _sender(tx: Any) -> Optional[bytes]:
    v = _first_attr(tx, ("sender", "from_", "from_addr", "from"), None)
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("0x"):
            s = s[2:]
        try:
            return bytes.fromhex(s)
        except Exception:
            return s.encode("utf-8", "ignore")
    try:
        return bytes(v)
    except Exception:
        return None


def _nonce(tx: Any) -> Optional[int]:
    v = _first_attr(tx, ("nonce",), None)
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _pool_call(pool: Any, name: str, *args, **kwargs) -> bool:
    """Call a method if it exists; return True if called without raising."""
    if hasattr(pool, name):
        try:
            getattr(pool, name)(*args, **kwargs)
            return True
        except Exception as e:
            logger.debug("pool.%s failed: %s", name, e)
    return False


def _maybe_update_chain_nonce(pool: Any, addr: bytes, new_nonce: int) -> bool:
    if _pool_call(pool, "on_chain_nonce_change", addr, new_nonce):
        return True
    seq = getattr(pool, "sequence", None)
    if seq is not None and hasattr(seq, "update_chain_nonce"):
        try:
            seq.update_chain_nonce(addr, new_nonce)
            return True
        except Exception as e:
            logger.debug("sequence.update_chain_nonce failed: %s", e)
    return False


# -------------------------
# Public API
# -------------------------


@dataclass
class ReorgDelta:
    old_tip: Optional[bytes]  # hash
    new_tip: Optional[bytes]  # hash
    removed: Sequence[
        Any
    ]  # old branch blocks (tail->head or head->tail, we don't care)
    added: Sequence[Any]  # new branch blocks


@dataclass
class ReorgStats:
    reinjected: int = 0
    dropped_confirmed: int = 0
    skipped_duplicate: int = 0
    skipped_replaced: int = 0
    reinject_errors: int = 0
    nonce_updates: int = 0
    senders_touched: int = 0
    elapsed_ms: float = 0.0


def handle_reorg(
    pool: Any,
    chain_view: Any,
    delta: ReorgDelta,
    *,
    revalidate_before_add: bool = True,
) -> ReorgStats:
    """
    Reconcile the mempool with a chain reorg.

    Steps:
      1) Build sets/maps from `added` branch:
           - included_hashes
           - replacements: (sender, nonce) -> tx_hash
      2) From `removed` branch, consider each tx for re-injection if:
           - its hash not in included_hashes AND
           - NOT replaced by a *different* tx occupying (sender, nonce) on the new branch
      3) Remove/confirm all txs that appear in `added`.
      4) Revert receipts for txs that were previously confirmed but no longer are.
      5) Refresh chain nonces for affected senders.

    Returns a ReorgStats summary.
    """
    t0 = time.perf_counter()
    stats = ReorgStats()

    # --- 1) Analyze new branch
    included_hashes: Set[bytes] = set()
    replacements: Dict[Tuple[bytes, int], bytes] = {}

    def _record_added_tx(tx: Any):
        h = _tx_hash(tx)
        if h:
            included_hashes.add(h)
        s = _sender(tx)
        n = _nonce(tx)
        if s is not None and n is not None:
            replacements[(s, n)] = h

    for blk in delta.added:
        for tx in _txs_of_block(blk):
            _record_added_tx(tx)

    # --- 2) Build candidate list from old branch
    reinject: List[Any] = []
    affected_senders: Set[bytes] = set()

    # Helper: is replaced on new branch?
    def _is_replaced(tx: Any) -> bool:
        s = _sender(tx)
        n = _nonce(tx)
        if s is None or n is None:
            return False
        new_h = replacements.get((s, n))
        return bool(new_h and new_h != _tx_hash(tx))

    for blk in delta.removed:
        for tx in _txs_of_block(blk):
            h = _tx_hash(tx)
            if h in included_hashes:
                stats.skipped_duplicate += 1
                continue
            if _is_replaced(tx):
                stats.skipped_replaced += 1
                continue
            reinject.append(tx)
            s = _sender(tx)
            if s:
                affected_senders.add(s)

    # --- 3) Confirm/drop everything that is in the new branch
    # Prefer bulk API if pool has it.
    added_hashes = list(included_hashes)
    if added_hashes:
        if not (
            _pool_call(pool, "remove_many", added_hashes)
            or _pool_call(pool, "pop_many", added_hashes)
        ):
            # Fall back to mark_confirmed/remove one-by-one
            for h in added_hashes:
                if not (
                    _pool_call(pool, "mark_confirmed", h)
                    or _pool_call(pool, "remove", h)
                    or _pool_call(pool, "discard", h)
                    or _pool_call(pool, "pop", h)
                ):
                    # If pool cannot remove, that's okay; selection later should ignore.
                    pass
        stats.dropped_confirmed += len(added_hashes)

    # --- 4) Re-inject old-branch txs (optionally revalidate)
    for tx in reinject:
        try:
            if revalidate_before_add and hasattr(pool, "revalidate_and_add"):
                _pool_call(pool, "revalidate_and_add", tx)
            else:
                # Try (tx, meta) signature, then tx-only.
                if not _pool_call(pool, "add", tx):
                    _pool_call(pool, "add", tx, meta=None)
            stats.reinjected += 1
        except Exception as e:
            logger.warning("Re-inject failed: %s", e)
            stats.reinject_errors += 1

        s = _sender(tx)
        if s:
            affected_senders.add(s)

        # If the pool tracks receipt state, notify about un-confirmation.
        _pool_call(pool, "on_reorg_tx_unconfirmed", _tx_hash(tx))

    # --- 5) Refresh chain nonces for affected senders (best-effort)
    for addr in sorted(affected_senders):
        new_nonce = None
        for name in ("get_nonce", "account_nonce", "get_account_nonce"):
            if hasattr(chain_view, name):
                try:
                    new_nonce = int(getattr(chain_view, name)(addr))
                    break
                except Exception as e:
                    logger.debug("chain_view.%s failed: %s", name, e)
        if new_nonce is not None:
            if _maybe_update_chain_nonce(pool, addr, new_nonce):
                stats.nonce_updates += 1

    # Optional bulk readiness recompute
    if affected_senders:
        _pool_call(pool, "recompute_readiness_for_senders", affected_senders)
        stats.senders_touched = len(affected_senders)

    stats.elapsed_ms = (time.perf_counter() - t0) * 1000.0
    logger.info(
        "mempool reorg handled: reinjected=%d dropped=%d dup=%d replaced=%d nonce_updates=%d senders=%d (%.1fms)",
        stats.reinjected,
        stats.dropped_confirmed,
        stats.skipped_duplicate,
        stats.skipped_replaced,
        stats.nonce_updates,
        stats.senders_touched,
        stats.elapsed_ms,
    )
    return stats


__all__ = ["ReorgDelta", "ReorgStats", "handle_reorg"]
