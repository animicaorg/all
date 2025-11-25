"""
Caps application for PoIES ψ (micro-nats) contributions.

This module is deliberately *pure*: given a batch of proof contributions
(pre-cap ψ per proof and its type), plus a loaded PoIES policy, it deterministically
clips them by:
  1) per-proof caps,
  2) per-type caps, and
  3) total-Γ (gamma) cap.

Escort/diversity constraints are enforced elsewhere (consensus.scorer/validator).
Here we only shape the vector of ψ so Σψ never exceeds configured limits while
preserving fairness via *proportional downscaling* with deterministic rounding.

Determinism notes
-----------------
- Inputs include a stable `proof_id` (e.g., nullifier or hash) used to
  tie-break rounding when distributing remainder micro-nats after a proportional
  downscale. We use lexicographic order of `proof_id` bytes as the final tie-breaker.
- All reductions are monotone (never increase ψ), integer-valued, and
  produce identical output across platforms.

Typical usage
-------------
from consensus.policy import load_poies_policy
from consensus.caps import Contribution, apply_all_caps

pol = load_poies_policy("spec/poies_policy.yaml")
c_in = [
  Contribution(proof_id=bytes.fromhex("01..."), proof_type=ProofType.AI, psi_micro=1_234_567),
  ...
]
c_out, stats = apply_all_caps(c_in, pol)
# Σψ(c_out) <= Γ, and per-type/per-proof limits respected.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, MutableSequence, Sequence, Tuple

from .types import ProofType, MicroNat, GammaMicro
from .policy import PoiesPolicy, TypeCap


# -----------------------------
# Data structures & statistics
# -----------------------------

@dataclass(frozen=True)
class Contribution:
    """A single proof's pre-cap ψ contribution."""
    proof_id: bytes         # deterministic tie-breaker (e.g., nullifier)
    proof_type: ProofType
    psi_micro: MicroNat     # pre-cap ψ (µ-nats); negative values are clamped to 0


@dataclass(frozen=True)
class CapStats:
    """Diagnostics for how much clipping happened at each stage (sums in µ-nats)."""
    sum_in: MicroNat
    sum_after_per_proof: MicroNat
    sum_after_per_type: MicroNat
    sum_after_gamma: MicroNat
    per_type_in: Dict[ProofType, MicroNat]
    per_type_after_per_proof: Dict[ProofType, MicroNat]
    per_type_after_per_type: Dict[ProofType, MicroNat]
    per_type_after_gamma: Dict[ProofType, MicroNat]


# -----------------------------
# Public API
# -----------------------------

def apply_all_caps(
    contributions: Sequence[Contribution],
    policy: PoiesPolicy,
) -> Tuple[List[Contribution], CapStats]:
    """
    Apply per-proof caps, per-type caps, then total Γ cap. Returns the new vector
    of contributions (same order as input) and summary statistics.
    """
    # Stage 0: sanitize negatives to zero (defensive)
    stage0 = [Contribution(c.proof_id, c.proof_type, max(int(c.psi_micro), 0)) for c in contributions]
    sum_in = _sum_psi(stage0)
    per_type_in = _sum_psi_by_type(stage0)

    # Stage 1: per-proof caps
    stage1 = _apply_per_proof_caps(stage0, policy)
    sum_after_pp = _sum_psi(stage1)
    per_type_after_pp = _sum_psi_by_type(stage1)

    # Stage 2: per-type caps (proportional downscale *within* each type)
    stage2 = _apply_per_type_caps(stage1, policy)
    sum_after_pt = _sum_psi(stage2)
    per_type_after_pt = _sum_psi_by_type(stage2)

    # Stage 3: total Γ cap (proportional over *all* types)
    stage3 = _apply_total_gamma_cap(stage2, policy)
    sum_after_g = _sum_psi(stage3)
    per_type_after_g = _sum_psi_by_type(stage3)

    stats = CapStats(
        sum_in=sum_in,
        sum_after_per_proof=sum_after_pp,
        sum_after_per_type=sum_after_pt,
        sum_after_gamma=sum_after_g,
        per_type_in=per_type_in,
        per_type_after_per_proof=per_type_after_pp,
        per_type_after_per_type=per_type_after_pt,
        per_type_after_gamma=per_type_after_g,
    )
    return stage3, stats


# -----------------------------
# Stage implementations
# -----------------------------

def _apply_per_proof_caps(
    items: Sequence[Contribution],
    policy: PoiesPolicy,
) -> List[Contribution]:
    """Clip each ψ to its type's per_proof_micro_max."""
    out: List[Contribution] = []
    # Build lookup once
    per_proof_max: Dict[ProofType, MicroNat] = {
        pt: policy.caps[pt].per_proof_micro_max for pt in ProofType
    }
    for c in items:
        cap = per_proof_max.get(c.proof_type, 0)
        new_psi = min(c.psi_micro, cap)
        out.append(Contribution(c.proof_id, c.proof_type, new_psi))
    return out


def _apply_per_type_caps(
    items: Sequence[Contribution],
    policy: PoiesPolicy,
) -> List[Contribution]:
    """
    For each type independently, if Σψ_type > cap_type, scale down that type's
    vector proportionally with deterministic rounding.
    """
    by_idx: List[Tuple[int, Contribution]] = list(enumerate(items))
    # Group indexes by type for in-place rewrite later
    idxs_by_type: Dict[ProofType, List[int]] = {pt: [] for pt in ProofType}
    for i, c in by_idx:
        idxs_by_type[c.proof_type].append(i)

    out = list(items)
    for pt, idxs in idxs_by_type.items():
        if not idxs:
            continue
        cap: MicroNat = policy.caps[pt].per_type_micro
        cur_sum = sum(out[i].psi_micro for i in idxs)
        if cur_sum <= cap:
            continue
        # Need to scale down contributions at these positions
        vec = [out[i].psi_micro for i in idxs]
        ids = [out[i].proof_id for i in idxs]
        scaled = _proportional_downscale(vec, cap, ids)
        # Write back
        for j, i in enumerate(idxs):
            c = out[i]
            out[i] = Contribution(c.proof_id, c.proof_type, scaled[j])
    return out


def _apply_total_gamma_cap(
    items: Sequence[Contribution],
    policy: PoiesPolicy,
) -> List[Contribution]:
    """
    If Σψ_all > Γ, scale the *entire* vector proportionally with deterministic rounding.
    """
    total: MicroNat = sum(c.psi_micro for c in items)
    gamma: GammaMicro = policy.gamma_cap
    if total <= gamma:
        return list(items)
    vec = [c.psi_micro for c in items]
    ids = [c.proof_id for c in items]
    scaled = _proportional_downscale(vec, int(gamma), ids)
    out: List[Contribution] = []
    for c, new_psi in zip(items, scaled):
        out.append(Contribution(c.proof_id, c.proof_type, new_psi))
    return out


# -----------------------------
# Helpers: sums & scaling
# -----------------------------

def _sum_psi(items: Sequence[Contribution]) -> MicroNat:
    return sum(c.psi_micro for c in items)


def _sum_psi_by_type(items: Sequence[Contribution]) -> Dict[ProofType, MicroNat]:
    acc: Dict[ProofType, MicroNat] = {pt: 0 for pt in ProofType}
    for c in items:
        acc[c.proof_type] += c.psi_micro
    return acc


def _proportional_downscale(
    values: Sequence[int],
    target_sum: int,
    ids_for_tiebreak: Sequence[bytes],
) -> List[int]:
    """
    Deterministically scale `values` so that the new integer vector sums to
    exactly `target_sum`, preserving proportions as closely as possible.

    Algorithm:
      - If sum(values) == 0: return all zeros.
      - Compute scale = target_sum / sum(values).
      - Base = floor(v_i * scale) for each i.
      - Distribute the remaining (target_sum - Σ base) ones to the entries with the
        largest fractional remainders; tie-break lexicographically by ids_for_tiebreak,
        then by original index to maintain determinism.

    Preconditions:
      - target_sum >= 0
      - len(values) == len(ids_for_tiebreak)
    """
    n = len(values)
    assert n == len(ids_for_tiebreak), "values and ids must be same length"
    if target_sum <= 0:
        return [0] * n
    total = sum(values)
    if total <= 0:
        return [0] * n
    # Fast path: already exact
    if total == target_sum:
        return list(values)
    scale = target_sum / total

    bases: List[int] = []
    fracs: List[Tuple[float, bytes, int]] = []  # (frac_part, id, original_index)
    for i, (v, pid) in enumerate(zip(values, ids_for_tiebreak)):
        if v <= 0:
            bases.append(0)
            fracs.append((0.0, pid, i))
            continue
        scaled = v * scale
        base = int(scaled)  # floor
        bases.append(base)
        fracs.append((scaled - base, pid, i))

    remaining = target_sum - sum(bases)
    if remaining <= 0:
        # Already exact or over-rounded; clip (shouldn't be >0 due to floor)
        return bases

    # Sort descending by fractional remainder; tie-break by id (lexicographic), then index
    fracs_sorted = sorted(
        fracs,
        key=lambda t: (t[0], t[1], -t[2]),  # larger frac first; for equal frac, smaller id first; then earlier index wins after reversing sign
        reverse=True,
    )

    out = bases[:]
    # Assign +1 to top `remaining` entries
    for k in range(remaining):
        _, _, idx = fracs_sorted[k]
        out[idx] += 1
    return out


# -----------------------------
# Tiny self-test (optional)
# -----------------------------

if __name__ == "__main__":
    # Minimal smoke check using a fake policy-like object.
    from .types import ProofType
    from .policy import Weights  # not used here, just to ensure import path is valid

    class _Caps:
        def __init__(self, per_type, per_proof):
            self.per_type_micro = per_type
            self.per_proof_micro_max = per_proof

    class _Pol:
        def __init__(self):
            self.caps = {
                ProofType.HASH: _Caps(5, 3),
                ProofType.AI: _Caps(5, 4),
                ProofType.QUANTUM: _Caps(5, 4),
                ProofType.STORAGE: _Caps(5, 4),
                ProofType.VDF: _Caps(5, 4),
            }
            self.gamma_cap = 8

    pol = _Pol()
    vec = [
        Contribution(b"\x00\x01", ProofType.HASH, 4),  # per-proof capped to 3
        Contribution(b"\x00\x02", ProofType.HASH, 4),  # per-proof capped to 3 → per-type cap 5 will downscale [3,3] → [3,2]
        Contribution(b"\x00\x03", ProofType.AI, 6),    # per-proof cap 4
    ]
    out, st = apply_all_caps(vec, pol)  # total will be 3+2+4 = 9 → Γ=8 downscale a bit
    print("in :", [c.psi_micro for c in vec], "sum", st.sum_in)
    print("out:", [c.psi_micro for c in out], "sum", st.sum_after_gamma)
