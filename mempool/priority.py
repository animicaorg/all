"""
mempool.priority
================

Effective priority and replace-by-fee (RBF) policy helpers.

This module computes a dimensionless **priority score** used to order mempool
entries and provides deterministic checks for **RBF eligibility** for
same-sender/nonce replacements.

Inputs
------
- **tip**: the per-gas tip implied by the fee style (legacy or EIP-1559-like),
          evaluated at the current base fee.
- **size**: raw tx size in bytes (penalized to prefer denser blocks).
- **age**: time since first-seen (older txs receive a mild bonus).
- **RBF thresholds**: minimum absolute + relative effective-gas-price bumps.

The score is monotone in `tip` and `age`, anti-monotone in `size`.

All functions are framework-free and safe for mypy/pyright.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Tuple

# Local types
try:
    from mempool.types import EffectiveFee, PoolTx, TxMeta  # type: ignore
except Exception:  # pragma: no cover - allow isolated imports
    from dataclasses import dataclass as _dataclass

    @_dataclass
    class EffectiveFee:  # type: ignore
        mode: str
        gas_price_wei: Optional[int] = None
        max_fee_per_gas_wei: Optional[int] = None
        max_priority_fee_per_gas_wei: Optional[int] = None

        def effective_gas_price(self, base_fee_wei: Optional[int]) -> int:
            if self.mode == "legacy":
                return int(self.gas_price_wei or 0)
            base = int(base_fee_wei or 0)
            tip = int(self.max_priority_fee_per_gas_wei or 0)
            m = int(self.max_fee_per_gas_wei or 0)
            return min(m, base + tip)

        def tip_at(self, base_fee_wei: Optional[int]) -> int:
            if self.mode == "legacy":
                return int(self.gas_price_wei or 0)
            base = int(base_fee_wei or 0)
            m = int(self.max_fee_per_gas_wei or 0)
            t = int(self.max_priority_fee_per_gas_wei or 0)
            return max(0, min(t, m - base))

    @_dataclass
    class TxMeta:  # type: ignore
        sender: str
        nonce: int
        gas_limit: int
        size_bytes: int
        first_seen: float = time.time()
        last_seen: float = time.time()
        priority_score: float = 0.0

    @_dataclass
    class PoolTx:  # type: ignore
        tx_hash: str
        meta: TxMeta
        fee: EffectiveFee
        raw: bytes

        def rekey_for_base_fee(self, base_fee_wei: Optional[int]) -> None:
            pass


__all__ = [
    "PriorityPolicy",
    "RBFPolicy",
    "effective_priority",
    "compute_tip_and_effective_price",
    "admission_fee_ok",
    "should_replace",
    "refresh_pooltx_priority",
]


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PriorityPolicy:
    """
    Scoring knobs and minimum-fee gates for admission/ordering.

    tip_log_scale_wei:
        Scale for the logarithmic response to tips. Around this value, a
        doubling of tip yields a roughly additive constant to the score.
        Defaults to 1 gwei.
    tip_weight:
        Weight applied to the (log) tip component.
    age_halflife_s:
        Exponential saturation half-life (seconds) for the age bonus.
        Older txs converge to a capped bonus ~ tip_weight * (some constant).
    age_weight:
        Weight applied to the age component.
    size_weight:
        Weight applied to the size penalty (logarithmic in bytes).
    min_effective_gas_price_wei:
        Hard floor on effective gas price (Wei) for admission.
    min_tip_wei:
        Hard floor on tip (Wei) at the current base fee for admission.
    """
    tip_log_scale_wei: int = 1_000_000_000         # 1 gwei
    tip_weight: float = 1.0
    age_halflife_s: float = 120.0                  # ~2 minutes half-life
    age_weight: float = 0.35
    size_weight: float = 0.20
    min_effective_gas_price_wei: int = 0
    min_tip_wei: int = 0


@dataclass(frozen=True)
class RBFPolicy:
    """
    Replace-by-fee thresholds and constraints.

    rel_bump: required *relative* bump (e.g., 0.1 = +10%) in effective gas price.
    abs_bump_wei: required *absolute* bump (Wei) in effective gas price.
    require_gas_limit_ge: if True, replacement must have >= gas_limit.
    copy_restrictions:
        If True, replacement must not relax denylisted properties (not enforced here;
        reserved for higher-level checks like access lists, caps, etc.).
    """
    rel_bump: float = 0.10
    abs_bump_wei: int = 2_000_000_000  # 2 gwei
    require_gas_limit_ge: bool = True
    copy_restrictions: bool = False


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_tip_and_effective_price(
    fee: EffectiveFee,
    *,
    base_fee_wei: Optional[int],
) -> Tuple[int, int]:
    """
    Return (tip_wei, effective_price_wei) under the given base fee.
    """
    tip = int(max(0, fee.tip_at(base_fee_wei)))
    eff = int(max(0, fee.effective_gas_price(base_fee_wei)))
    return tip, eff


def admission_fee_ok(
    fee: EffectiveFee,
    *,
    base_fee_wei: Optional[int],
    policy: PriorityPolicy,
) -> bool:
    """
    Gate for mempool admission based on fee floors.
    """
    tip, eff = compute_tip_and_effective_price(fee, base_fee_wei=base_fee_wei)
    if eff < int(policy.min_effective_gas_price_wei):
        return False
    if tip < int(policy.min_tip_wei):
        return False
    return True


def _age_bonus(age_s: float, halflife_s: float) -> float:
    """
    Smooth, capped age bonus in [0, 1):  1 - exp2(-age/halflife)
    Using exp2 for numerical stability in some runtimes; equivalent to exp with ln2 factor.
    """
    if age_s <= 0:
        return 0.0
    # 1 - 2^(-age/halflife)  (half-life means bonus reaches 1/2 at age==halflife)
    return 1.0 - 2.0 ** (-(age_s / max(1e-9, halflife_s)))


def _tip_term(tip_wei: int, scale_wei: int) -> float:
    """
    Logarithmic response to tips:
        log(1 + tip/scale)
    """
    t = max(0, int(tip_wei))
    s = max(1, int(scale_wei))
    return math.log1p(t / s)


def _size_penalty(size_bytes: int) -> float:
    """
    Mildly logarithmic size penalty so that extremely large transactions are
    disadvantaged without starving moderately sized ones:
        log2(1 + size_bytes)
    """
    b = max(0, int(size_bytes))
    return math.log2(1.0 + b)


def effective_priority(
    *,
    fee: EffectiveFee,
    meta: TxMeta,
    base_fee_wei: Optional[int],
    policy: Optional[PriorityPolicy] = None,
    now: Optional[float] = None,
) -> float:
    """
    Compute a dimensionless priority score given fee/meta and current base fee.

    Score = tip_weight * log(1 + tip / tip_log_scale)
          + age_weight * (1 - 2^(-age/halflife))
          - size_weight * log2(1 + size_bytes)

    The constants are calibrated for a wide dynamic range of tips and byte sizes,
    and to avoid extreme sensitivity to small fee fluctuations.
    """
    P = policy or PriorityPolicy()
    tip_wei, _eff = compute_tip_and_effective_price(fee, base_fee_wei=base_fee_wei)

    # Components
    tip_component = P.tip_weight * _tip_term(tip_wei, P.tip_log_scale_wei)
    age_s = max(0.0, (now if now is not None else time.time()) - float(meta.first_seen))
    age_component = P.age_weight * _age_bonus(age_s, P.age_halflife_s)
    size_component = P.size_weight * _size_penalty(int(meta.size_bytes))

    score = tip_component + age_component - size_component
    return float(score)


# ---------------------------------------------------------------------------
# RBF (Replace-By-Fee) checks
# ---------------------------------------------------------------------------

def should_replace(
    *,
    existing: PoolTx,
    candidate_fee: EffectiveFee,
    candidate_gas_limit: int,
    base_fee_wei: Optional[int],
    rbf: Optional[RBFPolicy] = None,
) -> Tuple[bool, str]:
    """
    Decide if a candidate tx (same sender+nonce) is eligible to replace `existing`.

    Conditions:
      - Effective gas price bump must satisfy BOTH:
            new >= old * (1 + rel_bump)
            new >= old + abs_bump_wei
      - (optional) gas_limit condition: candidate_gas_limit >= existing.gas_limit

    Returns (allowed, reason).
    """
    R = rbf or RBFPolicy()

    old_tip, old_eff = compute_tip_and_effective_price(existing.fee, base_fee_wei=base_fee_wei)
    new_tip, new_eff = compute_tip_and_effective_price(candidate_fee, base_fee_wei=base_fee_wei)

    if R.require_gas_limit_ge and int(candidate_gas_limit) < int(existing.gas_limit):
        return (False, "gas_limit_too_low")

    # Required thresholds
    rel_threshold = math.ceil(old_eff * (1.0 + float(R.rel_bump)))
    abs_threshold = old_eff + int(R.abs_bump_wei)
    required = max(rel_threshold, abs_threshold)

    if new_eff < required:
        return (False, f"fee_bump_insufficient: need>={required}, got={new_eff}")

    # Optional sanity: do not permit tip decreases if the effective price equality is just met
    if new_tip < old_tip:
        # Allow only if effective price is comfortably above threshold (e.g., 5% headroom)
        if new_eff < int(required * 1.05):
            return (False, "tip_reduced_without_headroom")

    return (True, "ok")


# ---------------------------------------------------------------------------
# Helpers for pool integration
# ---------------------------------------------------------------------------

def refresh_pooltx_priority(
    entry: PoolTx,
    *,
    base_fee_wei: Optional[int],
    policy: Optional[PriorityPolicy] = None,
    now: Optional[float] = None
) -> float:
    """
    Recompute and store the priority score on a PoolTx (and rekey ordering
    if the Pool implementation honors `entry.meta.priority_score`).

    Returns the updated score.
    """
    P = policy or PriorityPolicy()
    score = effective_priority(
        fee=entry.fee,
        meta=entry.meta,
        base_fee_wei=base_fee_wei,
        policy=P,
        now=now,
    )
    entry.meta.priority_score = float(score)
    # If PoolTx implements rekeying, update its sort index for the new base fee.
    try:
        entry.rekey_for_base_fee(base_fee_wei)
    except Exception:
        pass
    return score
