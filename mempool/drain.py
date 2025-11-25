"""
mempool.drain
=============

Block-builder facing selection/drain API.

This module chooses an ordered set of *ready* transactions from the
mempool under configurable gas/byte budgets, then (when the pool
supports it) pops those transactions atomically from the pool.

Design notes
------------
- **Ready-only**: we assume nonce sequencing and per-sender readiness is
  already handled by the mempool (see mempool.sequence). We never
  reorder a sender's nonces; instead we iterate the pool's *ready view*.
- **Greedy-by-priority**: we iterate ready txs in descending priority
  (via mempool.priority.effective_priority if available; otherwise a
  stable fallback) and include those that fit the remaining budgets.
- **Knobs**: the caller can bound gas, bytes, and max tx count. Large
  txs that don't fit are simply skipped; we keep scanning for smaller
  ones that do fit.
- **Duck-typed pool**: we support multiple pool shapes:
    * `pool.iter_ready(now_s) -> Iterable[(tx, meta)]` (preferred)
    * `pool.fetch_ready() -> List[(tx, meta)]`
  To pop:
    * `pool.remove_many([tx_hash, ...])`
    * or `pool.pop_many([...])`
    * or per-tx: `pool.remove(tx_hash)` / `pool.pop(tx_hash)` / `pool.discard(tx_hash)`
  If no pop/remove is available, we return the selection without mutating.

Expected fields (duck-typed)
----------------------------
Each tx/meta should provide:
  - meta.tx_hash  (bytes or hex str)
  - meta.size_bytes (int)
  - tx.gas_limit  or tx.intrinsic_gas or meta.gas_limit (int)
  - meta.first_seen_s (float) [optional]
  - meta.local (bool) [optional]
  - meta.effective_fee_wei or tx.effective_fee_wei (int) [for fallback priority]

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, Optional, Sequence, Tuple
import time

# Optional coupling with mempool.priority
try:
    from . import priority as _priority  # type: ignore
except Exception:  # pragma: no cover
    _priority = None  # type: ignore

Clock = Callable[[], float]


# -------------------------
# Config & result types
# -------------------------

@dataclass
class DrainConfig:
    gas_limit: int
    byte_limit: int
    max_txs: Optional[int] = None
    # If True, we allow ending early when remaining gas/bytes are too small for the
    # smallest seen tx (fast exit); otherwise we scan full ready view.
    early_exit_on_starvation: bool = True


@dataclass
class DrainStats:
    selected_gas: int = 0
    selected_bytes: int = 0
    considered: int = 0
    selected_count: int = 0
    skipped_too_big: int = 0
    skipped_other: int = 0


@dataclass
class DrainResult:
    """Selection plus stats and a note describing stop condition."""
    picked: List[Tuple[Any, Any]]
    stats: DrainStats
    stop_reason: str  # "budget_exhausted" | "no_ready" | "scanned_all" | "pool_no_pop_api"


# -------------------------
# Helpers (duck-typed accessors)
# -------------------------

def _now() -> float:
    return time.monotonic()


def _tx_hash(meta: Any, tx: Any) -> bytes:
    h = getattr(meta, "tx_hash", getattr(tx, "tx_hash", getattr(tx, "hash", None)))
    if h is None:
        return b""
    if isinstance(h, (bytes, bytearray)):
        return bytes(h)
    if isinstance(h, str):
        s = h.strip()
        if s.startswith("0x"):
            s = s[2:]
        try:
            return bytes.fromhex(s)
        except Exception:
            return s.encode("utf-8", "ignore")
    try:
        return bytes(h)
    except Exception:
        return str(h).encode("utf-8", "ignore")


def _size_bytes(tx: Any, meta: Any) -> int:
    v = getattr(meta, "size_bytes", None)
    if v is None:
        v = getattr(tx, "size_bytes", None)
    return int(v or 0)


def _gas_cost(tx: Any, meta: Any) -> int:
    for attr in ("gas_limit", "intrinsic_gas", "gas"):
        v = getattr(tx, attr, None)
        if v is not None:
            return int(v)
    v = getattr(meta, "gas_limit", None)
    return int(v or 0)


def _effective_priority(tx: Any, meta: Any, now_s: float) -> float:
    if _priority is not None and hasattr(_priority, "effective_priority"):
        try:
            return float(_priority.effective_priority(tx, meta, now_s))  # type: ignore[attr-defined]
        except Exception:
            pass
    # Fallback: fee-per-byte with a small age boost (≤10%)
    eff_fee = getattr(meta, "effective_fee_wei", getattr(tx, "effective_fee_wei", 0)) or 0
    size = _size_bytes(tx, meta) or 1
    first_seen = float(getattr(meta, "first_seen_s", now_s))
    age_boost = min(max(0.0, now_s - first_seen) / 600.0, 0.10)
    return (int(eff_fee) / max(1, int(size))) * (1.0 + age_boost)


# -------------------------
# Pool adapter
# -------------------------

class _PoolAdapter:
    def __init__(self, pool: Any, clock: Clock):
        self.pool = pool
        self.clock = clock

    # Ready iterator in *descending* priority if the pool can provide it,
    # otherwise we sort here using effective_priority.
    def iter_ready(self) -> Iterator[Tuple[Any, Any]]:
        now_s = self.clock()

        # 1) Preferred: pool.iter_ready(now_s)
        if hasattr(self.pool, "iter_ready"):
            it = self.pool.iter_ready(now_s)  # type: ignore[attr-defined]
            # Assume pool yields in the right order
            for item in it:
                yield item
            return

        # 2) Alternative snapshot: pool.fetch_ready()
        ready: Optional[Iterable[Tuple[Any, Any]]] = None
        if hasattr(self.pool, "fetch_ready"):
            ready = self.pool.fetch_ready()  # type: ignore[attr-defined]
        elif hasattr(self.pool, "get_ready_snapshot"):
            ready = self.pool.get_ready_snapshot()  # type: ignore[attr-defined]

        if ready is not None:
            # Sort by our local notion of priority
            buff: List[Tuple[Any, Any, float]] = []
            for tx, meta in ready:
                pr = _effective_priority(tx, meta, now_s)
                buff.append((tx, meta, pr))
            buff.sort(key=lambda t: t[2], reverse=True)
            for tx, meta, _ in buff:
                yield (tx, meta)
            return

        # 3) Fallback: nothing to iterate
        return
        yield  # pragma: no cover

    def pop_many(self, tx_hashes: Sequence[bytes]) -> bool:
        """Attempt to atomically remove from the pool. Returns True if successful."""
        p = self.pool
        if hasattr(p, "remove_many"):
            p.remove_many(tx_hashes)  # type: ignore[attr-defined]
            return True
        if hasattr(p, "pop_many"):
            p.pop_many(tx_hashes)  # type: ignore[attr-defined]
            return True
        # Try per-tx methods; if any fails, we keep going best-effort
        per_tx = None
        for name in ("remove", "pop", "discard"):
            if hasattr(p, name):
                per_tx = getattr(p, name)
                break
        if per_tx is not None:
            for h in tx_hashes:
                try:
                    per_tx(h)  # type: ignore[misc]
                except Exception:
                    pass
            return True
        return False


# -------------------------
# Core selection logic
# -------------------------

def select_for_block(
    pool: Any,
    cfg: DrainConfig,
    *,
    clock: Clock = _now,
) -> DrainResult:
    """
    Plan the set of ready transactions to include under budgets.

    Does NOT mutate the pool; use `drain_for_block` to also pop.
    """
    adapter = _PoolAdapter(pool, clock)
    stats = DrainStats()
    picked: List[Tuple[Any, Any]] = []

    gas_left = int(cfg.gas_limit)
    bytes_left = int(cfg.byte_limit)
    now_s = clock()

    smallest_seen_size = None
    smallest_seen_gas = None

    for tx, meta in adapter.iter_ready():
        stats.considered += 1
        if cfg.max_txs is not None and stats.selected_count >= cfg.max_txs:
            break

        size = _size_bytes(tx, meta)
        gas = _gas_cost(tx, meta)
        if smallest_seen_size is None or size < smallest_seen_size:
            smallest_seen_size = size
        if smallest_seen_gas is None or gas < smallest_seen_gas:
            smallest_seen_gas = gas

        # Fits budgets?
        if size <= bytes_left and gas <= gas_left:
            picked.append((tx, meta))
            stats.selected_bytes += size
            stats.selected_gas += gas
            stats.selected_count += 1
            bytes_left -= size
            gas_left -= gas
            continue

        # Skip if too big for remaining budgets
        stats.skipped_too_big += 1

        # Early-exit starvation heuristic:
        if cfg.early_exit_on_starvation and stats.considered > 32:
            # If remaining budgets are smaller than the *smallest seen* item,
            # further scanning is unlikely to change the outcome.
            if (smallest_seen_size is not None and bytes_left < smallest_seen_size) and \
               (smallest_seen_gas is not None and gas_left < smallest_seen_gas):
                break

    stop_reason = "budget_exhausted" if (gas_left <= 0 or bytes_left <= 0) else (
        "scanned_all" if stats.considered > 0 else "no_ready"
    )
    return DrainResult(picked=picked, stats=stats, stop_reason=stop_reason)


def drain_for_block(
    pool: Any,
    cfg: DrainConfig,
    *,
    clock: Clock = _now,
) -> DrainResult:
    """
    Select and POP ready transactions from the mempool according to budgets.

    If the pool exposes an API to remove the selected transactions, this
    function will call it. Otherwise the selection is returned without
    mutating the pool and `stop_reason` is set to "pool_no_pop_api".
    """
    plan = select_for_block(pool, cfg, clock=clock)
    if not plan.picked:
        return plan

    adapter = _PoolAdapter(pool, clock)
    hashes = [_tx_hash(meta, tx) for (tx, meta) in plan.picked]
    mutated = adapter.pop_many(hashes)
    if not mutated:
        # Pool doesn't provide a pop/remove API; return plan unchanged.
        return DrainResult(
            picked=plan.picked,
            stats=plan.stats,
            stop_reason="pool_no_pop_api",
        )
    return plan


# -------------------------
# Pretty-printers
# -------------------------

def format_selection(result: DrainResult) -> str:
    b = []
    b.append(f"picked={result.stats.selected_count} gas={result.stats.selected_gas} bytes={result.stats.selected_bytes} reason={result.stop_reason}")
    for tx, meta in result.picked:
        h = _tx_hash(meta, tx).hex()[:12]
        size = _size_bytes(tx, meta)
        gas = _gas_cost(tx, meta)
        b.append(f"  {h}.. size={size}B gas={gas}")
    return "\n".join(b)


__all__ = [
    "DrainConfig",
    "DrainStats",
    "DrainResult",
    "select_for_block",
    "drain_for_block",
    "format_selection",
]
