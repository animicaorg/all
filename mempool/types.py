"""
mempool.types
=============

Lightweight, typed containers used by mempool components.

- EffectiveFee:  encapsulates legacy (gasPrice) and EIP-1559-style (maxFeePerGas,
                 maxPriorityFeePerGas) fee parameters and provides helpers to
                 compute the effective price at a given base fee.
- TxMeta:        derived / runtime metadata about a transaction (sender, nonce,
                 sizes, timestamps, scoring knobs).
- PoolTx:        the actual mempool entry (Tx + raw bytes + metadata + cached fee).
- PoolStats:     aggregate statistics snapshot.

These types are intentionally **framework-free** (no pydantic dependency) and
safe for mypy/pyright. Convert to JSON via `.to_dict()` where needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Literal
import time

try:
    # Core transaction type (defined in core/types/tx.py)
    from core.types.tx import Tx  # type: ignore
except Exception:  # pragma: no cover - allow tools to import without core present
    # Minimal fallback stub to keep type-checkers happy in isolation.
    @dataclass
    class Tx:  # type: ignore
        kind: str
        sender: str
        nonce: int
        gas_limit: int
        # Optional fee fields to mirror EIP-1559 style naming, if present.
        gas_price: Optional[int] = None
        max_fee_per_gas: Optional[int] = None
        max_priority_fee_per_gas: Optional[int] = None

__all__ = [
    "EffectiveFee",
    "TxMeta",
    "PoolTx",
    "PoolStats",
]

Wei = int
Address = str
UnixTime = float
TxHash = str


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EffectiveFee:
    """
    Canonicalized fee parameters for admission/scoring.

    Supports either:
      - LEGACY:       gasPrice
      - EIP1559-like: maxFeePerGas, maxPriorityFeePerGas

    The chain may or may not implement dynamic base fee. We keep the structure
    general so the mempool can make choices even on pre-EIP1559 style rules.

    Invariants:
      * All numeric fields are non-negative integers (Wei).
      * If `mode == "legacy"`, only `gas_price_wei` is used.
      * If `mode == "eip1559"`, both `max_fee_per_gas_wei` and
        `max_priority_fee_per_gas_wei` are set.
    """
    mode: Literal["legacy", "eip1559"]
    gas_price_wei: Optional[Wei] = None
    max_fee_per_gas_wei: Optional[Wei] = None
    max_priority_fee_per_gas_wei: Optional[Wei] = None

    @staticmethod
    def from_tx(tx: Tx) -> "EffectiveFee":
        """
        Construct from a transaction object that may carry either legacy or
        EIP-1559-style fee fields.
        """
        # Prefer explicit EIP-1559-style fields if both are present.
        if getattr(tx, "max_fee_per_gas", None) is not None or getattr(tx, "max_priority_fee_per_gas", None) is not None:
            max_fee = int(getattr(tx, "max_fee_per_gas", 0) or 0)
            max_tip = int(getattr(tx, "max_priority_fee_per_gas", 0) or 0)
            return EffectiveFee(
                mode="eip1559",
                max_fee_per_gas_wei=max_fee,
                max_priority_fee_per_gas_wei=max_tip,
            )
        # Fallback to legacy gasPrice
        gp = int(getattr(tx, "gas_price", 0) or 0)
        return EffectiveFee(mode="legacy", gas_price_wei=gp)

    @staticmethod
    def from_legacy(gas_price_wei: Wei) -> "EffectiveFee":
        return EffectiveFee(mode="legacy", gas_price_wei=int(gas_price_wei))

    @staticmethod
    def from_eip1559(max_fee_per_gas_wei: Wei, max_priority_fee_per_gas_wei: Wei) -> "EffectiveFee":
        return EffectiveFee(
            mode="eip1559",
            max_fee_per_gas_wei=int(max_fee_per_gas_wei),
            max_priority_fee_per_gas_wei=int(max_priority_fee_per_gas_wei),
        )

    def effective_gas_price(self, base_fee_wei: Optional[Wei]) -> Wei:
        """
        Compute the effective gas price (Wei) given an optional base fee.

        - LEGACY: returns `gas_price_wei` as-is.
        - EIP1559: min(maxFeePerGas, baseFee + maxPriorityFee). If base_fee is
                   None (no dynamic fee), treat base_fee=0.
        """
        if self.mode == "legacy":
            return int(self.gas_price_wei or 0)

        base = int(base_fee_wei or 0)
        tip = int(self.max_priority_fee_per_gas_wei or 0)
        max_fee = int(self.max_fee_per_gas_wei or 0)
        # value the sender is actually willing to pay per gas at this base fee
        want = base + tip
        return min(max_fee, want)

    def tip_at(self, base_fee_wei: Optional[Wei]) -> Wei:
        """
        Compute the **tip** portion (Wei per gas) at the given base fee.
        - LEGACY: tip == gasPrice (since there's no base fee component).
        - EIP1559: min(maxPriorityFeePerGas, maxFeePerGas - baseFee) clipped at 0.
        """
        if self.mode == "legacy":
            return int(self.gas_price_wei or 0)

        base = int(base_fee_wei or 0)
        max_fee = int(self.max_fee_per_gas_wei or 0)
        max_tip = int(self.max_priority_fee_per_gas_wei or 0)
        return max(0, min(max_tip, max_fee - base))

    def to_dict(self) -> Dict[str, Any]:
        if self.mode == "legacy":
            return {"mode": "legacy", "gasPrice": int(self.gas_price_wei or 0)}
        return {
            "mode": "eip1559",
            "maxFeePerGas": int(self.max_fee_per_gas_wei or 0),
            "maxPriorityFeePerGas": int(self.max_priority_fee_per_gas_wei or 0),
        }


# ---------------------------------------------------------------------------
# Transaction metadata & pool entry
# ---------------------------------------------------------------------------

@dataclass
class TxMeta:
    """
    Derived transaction metadata maintained by the mempool.

    Fields
    ------
    sender : Address
        The canonical sender address (post verification).
    nonce : int
        Sender nonce.
    gas_limit : int
        Declared gas limit.
    size_bytes : int
        Size of the raw RLP/CBOR-encoded transaction as admitted.
    first_seen : UnixTime
        Monotonic timestamp when the tx was first seen.
    last_seen : UnixTime
        Last time we re-observed this transaction (e.g., rebroadcast).
    expires_at : Optional[UnixTime]
        TTL cutoff for eviction, if configured.
    local : bool
        Submitted locally (trusted) vs gossiped.
    pinned : bool
        Pinned transactions bypass certain evictions (e.g., dev tools).
    priority_score : float
        Dynamic score used by eviction/scheduling (higher is better).
    """
    sender: Address
    nonce: int
    gas_limit: int
    size_bytes: int
    first_seen: UnixTime = field(default_factory=time.time)
    last_seen: UnixTime = field(default_factory=time.time)
    expires_at: Optional[UnixTime] = None
    local: bool = False
    pinned: bool = False
    priority_score: float = 0.0

    def touch(self) -> None:
        """Update last_seen to now."""
        self.last_seen = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "nonce": int(self.nonce),
            "gas_limit": int(self.gas_limit),
            "size_bytes": int(self.size_bytes),
            "first_seen": float(self.first_seen),
            "last_seen": float(self.last_seen),
            "expires_at": float(self.expires_at) if self.expires_at is not None else None,
            "local": bool(self.local),
            "pinned": bool(self.pinned),
            "priority_score": float(self.priority_score),
        }


@dataclass(order=True)
class PoolTx:
    """
    Mempool entry: the raw tx + parsed Tx + metadata + fee view.

    Ordering
    --------
    Instances are orderable for heap/index usage. We sort by a composite key:

        (-priority_score, first_seen, gas_price_hint)

    where `gas_price_hint` is the effective price at an optional base fee cached
    by the pool (0 if unknown). Update `sort_index` via `rekey_for_base_fee()`
    when base fee moves.
    """
    # Sorting key (not part of the public API)
    sort_index: Tuple[float, float, int] = field(init=False, repr=False, compare=True)

    # Public fields
    tx: Tx = field(compare=False)
    tx_hash: TxHash = field(compare=False)
    raw: bytes = field(repr=False, compare=False)
    meta: TxMeta = field(compare=False)
    fee: EffectiveFee = field(compare=False)

    # Cached effective price hint (at the last pool-known base fee), for ordering.
    _cached_effective_price_wei: int = field(default=0, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Initialize ordering key
        self.sort_index = (-float(self.meta.priority_score), float(self.meta.first_seen), int(self._cached_effective_price_wei))

    @property
    def sender(self) -> Address:
        return self.meta.sender

    @property
    def nonce(self) -> int:
        return self.meta.nonce

    @property
    def size_bytes(self) -> int:
        return self.meta.size_bytes

    @property
    def gas_limit(self) -> int:
        return self.meta.gas_limit

    def effective_gas_price(self, base_fee_wei: Optional[Wei]) -> Wei:
        """Compute effective gas price on demand (does not update ordering key)."""
        return self.fee.effective_gas_price(base_fee_wei)

    def tip_at(self, base_fee_wei: Optional[Wei]) -> Wei:
        """Compute the tip component at the given base fee."""
        return self.fee.tip_at(base_fee_wei)

    def rekey_for_base_fee(self, base_fee_wei: Optional[Wei]) -> None:
        """
        Update cached effective price & ordering key to reflect a new base fee.
        """
        self._cached_effective_price_wei = int(self.fee.effective_gas_price(base_fee_wei))
        self.sort_index = (-float(self.meta.priority_score), float(self.meta.first_seen), int(self._cached_effective_price_wei))

    def bump_priority(self, delta: float) -> None:
        """Increase dynamic priority score and refresh ordering key."""
        self.meta.priority_score += float(delta)
        self.sort_index = (-float(self.meta.priority_score), float(self.meta.first_seen), int(self._cached_effective_price_wei))

    def to_dict(self, *, include_raw: bool = False, base_fee_wei: Optional[Wei] = None) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "hash": self.tx_hash,
            "sender": self.meta.sender,
            "nonce": int(self.meta.nonce),
            "gas_limit": int(self.meta.gas_limit),
            "size_bytes": int(self.meta.size_bytes),
            "fee": self.fee.to_dict(),
            "effectiveGasPrice": int(self.fee.effective_gas_price(base_fee_wei)),
            "meta": self.meta.to_dict(),
        }
        if include_raw:
            d["raw"] = self.raw.hex()
        return d


# ---------------------------------------------------------------------------
# Pool statistics snapshot
# ---------------------------------------------------------------------------

@dataclass
class PoolStats:
    """
    Aggregate mempool statistics (cheap to compute; suitable for RPC exposure).

    Fields
    ------
    total_txs : int
        Number of transactions currently in the pool.
    total_bytes : int
        Sum of raw sizes for all txs.
    total_gas : int
        Sum of gas limits for all txs (upper bound, not gas used).
    min_gas_price_wei : Optional[int]
        Minimum *effective* gas price across the pool at the supplied base fee.
    max_gas_price_wei : Optional[int]
        Maximum *effective* gas price across the pool at the supplied base fee.
    oldest_first_seen : Optional[UnixTime]
        Oldest first_seen timestamp among entries.
    newest_first_seen : Optional[UnixTime]
        Newest first_seen timestamp among entries.
    """
    total_txs: int
    total_bytes: int
    total_gas: int
    min_gas_price_wei: Optional[Wei] = None
    max_gas_price_wei: Optional[Wei] = None
    oldest_first_seen: Optional[UnixTime] = None
    newest_first_seen: Optional[UnixTime] = None

    @staticmethod
    def empty() -> "PoolStats":
        return PoolStats(
            total_txs=0,
            total_bytes=0,
            total_gas=0,
            min_gas_price_wei=None,
            max_gas_price_wei=None,
            oldest_first_seen=None,
            newest_first_seen=None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "totalTxs": int(self.total_txs),
            "totalBytes": int(self.total_bytes),
            "totalGas": int(self.total_gas),
            "minGasPriceWei": int(self.min_gas_price_wei) if self.min_gas_price_wei is not None else None,
            "maxGasPriceWei": int(self.max_gas_price_wei) if self.max_gas_price_wei is not None else None,
            "oldestFirstSeen": float(self.oldest_first_seen) if self.oldest_first_seen is not None else None,
            "newestFirstSeen": float(self.newest_first_seen) if self.newest_first_seen is not None else None,
        }
