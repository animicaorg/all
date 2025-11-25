"""
Share receipts (micro-target accounting & Merkle aggregation)
=============================================================

This module turns verified proof metrics into *share receipts* and aggregates
them into a canonical Merkle root that is included in the block header.

Goals
-----
- Deterministically convert fractional contribution signals (e.g. HashShare
  difficulty ratio, AI/Quantum units) into integral *micro-shares*.
- Provide a stable, domain-separated leaf encoding for Merkle inclusion.
- Keep ordering rules simple and canonical so all honest nodes compute the same
  root given the same set of receipts.

Model
-----
A *ShareReceipt* captures the atomized contribution of a *single* proof instance.
Each receipt carries:
  • type_id       — proof kind (hash/ai/quantum/storage/vdf) from consensus.types
  • nullifier     — 32B domain-separated unique id (prevents reuse within window)
  • micro_units   — non-negative integer micro-shares (unitless, policy-chosen)
  • meta_flags    — small bitfield reserved for future toggles (e.g. bonus flags)

Leaf encoding (v1)
------------------
leaf = sha3_256(
    b"SR\x01"                              # domain tag (ShareReceipt v1)
  | u8(type_id)
  | u8(meta_flags)
  | u64be(micro_units)
  | nullifier(32 bytes)
)

Canonical Merkle
----------------
We use the canonical binary Merkle from core.utils.merkle. Leaves are *sorted*
by (type_id asc, nullifier lexicographically) before hashing to ensure that
ordering differences across gossip/build paths do not change the root.

Micro-share accounting
----------------------
For fractional inputs x >= 0 (e.g. difficulty ratio, or a weighted unit value),
we first compute floor(x). If x has a fractional part, we apply *stochastic
rounding* using a deterministic PRF keyed by a per-block *rounding_seed*
(mixSeed) and the receipt identity (type_id|nullifier):

  rnd = H(seed | type_id | nullifier) interpreted as U[0,1)
  micro = floor(x) + 1 if rnd < frac(x) else floor(x)

This yields expectation-preserving rounding while remaining deterministic.

Integration points
------------------
- consensus/difficulty.py chooses the *scaling* that maps metrics → fractional x
- consensus/validator.py constructs ShareAggregator for a candidate block,
  adds receipts as proofs are selected, and writes the returned Merkle root to
  the header.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple, Optional

try:
    # Prefer canonical hash & merkle utilities from core
    from core.utils.hash import sha3_256
    from core.utils.merkle import merkle_root as _merkle_root
except Exception:  # pragma: no cover - fallback for isolated unit tests
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()

    def _merkle_root(leaves: Iterable[bytes]) -> bytes:
        # Simple binary Merkle with hash(empty)=zeros, 1-leaf = hash(leaf)
        vs = list(leaves)
        if not vs:
            return b"\x00" * 32
        level = [sha3_256(x) for x in vs]
        if not level:
            return b"\x00" * 32
        while len(level) > 1:
            nxt: List[bytes] = []
            it = iter(level)
            for a in it:
                b = next(it, a)  # duplicate last if odd
                nxt.append(sha3_256(a + b))
            level = nxt
        return level[0]

from enum import IntEnum

# Keep in sync with consensus/types.ProofType, but allow independent unit testing.
try:
    from consensus.types import ProofType  # type: ignore
except Exception:  # pragma: no cover
    class ProofType(IntEnum):
        HASH = 0
        AI = 1
        QUANTUM = 2
        STORAGE = 3
        VDF = 4


# ----------------------------- Encoding helpers ------------------------------

def _u8(x: int) -> bytes:
    if not (0 <= x <= 255):
        raise ValueError("u8 out of range")
    return x.to_bytes(1, "big")

def _u64be(x: int) -> bytes:
    if x < 0:
        raise ValueError("u64be expects non-negative")
    return x.to_bytes(8, "big", signed=False)


LEAF_DOMAIN = b"SR\x01"  # ShareReceipt v1


@dataclass(frozen=True)
class ShareReceipt:
    """Compact record for contribution of one proof instance."""
    type_id: ProofType
    nullifier: bytes          # 32 bytes (sha3-256 from proofs/nullifiers.py)
    micro_units: int          # integral micro-shares (>= 0)
    meta_flags: int = 0       # reserved: u8 bitfield

    def __post_init__(self) -> None:
        if len(self.nullifier) != 32:
            raise ValueError("nullifier must be 32 bytes")
        if self.micro_units < 0:
            raise ValueError("micro_units must be >= 0")
        if not (0 <= self.meta_flags <= 0xFF):
            raise ValueError("meta_flags must fit in u8")

    def leaf_bytes(self) -> bytes:
        """Return the domain-separated preimage for the leaf hash."""
        return b"".join((
            LEAF_DOMAIN,
            _u8(int(self.type_id)),
            _u8(self.meta_flags),
            _u64be(self.micro_units),
            self.nullifier,
        ))

    def leaf_hash(self) -> bytes:
        """Return the leaf hash H(leaf_bytes)."""
        return sha3_256(self.leaf_bytes())


# --------------------------- Stochastic rounding -----------------------------

def _prf01(seed: bytes, type_id: int, nullifier: bytes) -> float:
    """
    PRF(seed, type_id, nullifier) -> U[0,1)
    Use SHA3-256 and map first 8 bytes to a fraction in [0,1).
    """
    h = sha3_256(seed + _u8(type_id) + nullifier)
    word = int.from_bytes(h[:8], "big", signed=False)
    return (word & ((1 << 64) - 1)) / float(1 << 64)


def stochastic_round(x: float, seed: bytes, type_id: int, nullifier: bytes) -> int:
    """
    Deterministic, expectation-preserving rounding for any x >= 0.
    """
    if x <= 0.0:
        return 0
    base = int(x)  # floor
    frac = x - float(base)
    if frac <= 0.0:
        return base
    return base + (1 if _prf01(seed, type_id, nullifier) < frac else 0)


# ----------------------------- Aggregation API -------------------------------

@dataclass(frozen=True)
class AggregationStats:
    count: int
    total_micro_units: int
    types_breakdown: Tuple[int, int, int, int, int]  # HASH, AI, QUANTUM, STORAGE, VDF (length = max type index + 1)


class ShareAggregator:
    """
    Collect ShareReceipts, canonicalize order, and compute the Merkle root.

    Usage:
        agg = ShareAggregator(rounding_seed=mix_seed)
        # HashShare example (difficulty ratio r mapped by policy to x):
        agg.add_fractional(ProofType.HASH, nullifier, x=r_scaled)
        # AI/Quantum examples (units already integral):
        agg.add_integral(ProofType.AI, nullifier, units)
        root, stats = agg.finalize()
    """

    __slots__ = ("_seed", "_receipts", "_totals")

    def __init__(self, rounding_seed: bytes):
        if len(rounding_seed) == 0:
            raise ValueError("rounding_seed must be non-empty")
        self._seed = rounding_seed
        self._receipts: List[ShareReceipt] = []
        # type-indexed totals for quick stats; we assume at most 5 types (0..4)
        self._totals = [0, 0, 0, 0, 0]

    # --- admission ---

    def add_fractional(self, type_id: ProofType, nullifier: bytes, x: float, meta_flags: int = 0) -> ShareReceipt:
        """
        Add a contribution from a fractional signal (x >= 0). Uses stochastic rounding.
        """
        micro = stochastic_round(float(x), self._seed, int(type_id), nullifier)
        return self.add_integral(type_id, nullifier, micro, meta_flags=meta_flags)

    def add_integral(self, type_id: ProofType, nullifier: bytes, units: int, meta_flags: int = 0) -> ShareReceipt:
        """
        Add an already-integral count of micro-units (>= 0).
        """
        r = ShareReceipt(type_id=type_id, nullifier=bytes(nullifier), micro_units=int(units), meta_flags=meta_flags)
        self._receipts.append(r)
        idx = int(type_id)
        if 0 <= idx < len(self._totals):
            self._totals[idx] += r.micro_units
        return r

    # --- finalize ---

    def _sorted_receipts(self) -> List[ShareReceipt]:
        """
        Canonical ordering: (type_id asc, nullifier lexicographically asc).
        """
        return sorted(self._receipts, key=lambda r: (int(r.type_id), r.nullifier))

    def merkle_leaves(self) -> List[bytes]:
        """
        Return canonical, ordered leaf preimages (not hashed yet) for debugging / tracing.
        """
        return [r.leaf_bytes() for r in self._sorted_receipts()]

    def merkle_root(self) -> bytes:
        """
        Return the canonical Merkle root over leaf *hashes*.
        Empty set is the 32-byte zero.
        """
        leaves = [r.leaf_hash() for r in self._sorted_receipts()]
        return _merkle_root(leaves)

    def finalize(self) -> Tuple[bytes, AggregationStats]:
        """
        Compute the Merkle root and a small stats summary.
        """
        root = self.merkle_root()
        stats = AggregationStats(
            count=len(self._receipts),
            total_micro_units=sum(self._totals),
            types_breakdown=tuple(self._totals),  # type: ignore[arg-type]
        )
        return root, stats


# ------------------------------- Convenience ---------------------------------

def receipts_root_from_fractionals(
    rounding_seed: bytes,
    items: Iterable[Tuple[ProofType, bytes, float]],
) -> bytes:
    """
    Convenience helper: build a root from a list of (type_id, nullifier, x)
    where x is a fractional contribution (mapped by policy).
    """
    agg = ShareAggregator(rounding_seed)
    for t, n, x in items:
        agg.add_fractional(t, n, x)
    return agg.merkle_root()


def receipts_root_from_integrals(
    items: Iterable[Tuple[ProofType, bytes, int]],
) -> bytes:
    """
    Convenience helper for already-integral micro-units.
    """
    # Use a fixed seed to preserve leaf encoding determinism (seed is not used).
    agg = ShareAggregator(rounding_seed=b"\x01")
    for t, n, u in items:
        agg.add_integral(t, n, u)
    return agg.merkle_root()


__all__ = [
    "ShareReceipt",
    "ShareAggregator",
    "AggregationStats",
    "stochastic_round",
    "receipts_root_from_fractionals",
    "receipts_root_from_integrals",
]
