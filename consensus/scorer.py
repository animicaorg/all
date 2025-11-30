"""
PoIES Scorer
============

Maps proof *metrics* → ψ (micro-nats), applies caps (per-proof, per-type, Γ),
and evaluates the acceptance predicate:

    S = base_entropy_micro + Σψ_capped  >=  Θ_micro

- `base_entropy_micro` is typically H(u) from the hash u-draw (expressed in µ-nats).
- ψ contributions are non-negative and integer-valued (µ-nats).
- Proportional downscaling w/ deterministic rounding is handled by consensus.caps.

This module is intentionally *pure* and dependency-light. It does not import
from `proofs/…`. Callers provide a list of `(proof_id, proof_type, metrics)`
where `metrics` is a `Mapping[str, Any]` with conventional keys documented in
the per-type default hooks below.

Key types:
- ProofType: enum defined in consensus.types
- MicroNat, GammaMicro: typed aliases (ints) in consensus.types

Typical usage
-------------
from consensus.policy import load_poies_policy
from consensus.scorer import aggregate_and_accept, default_score_hooks
from consensus.types import ProofType

policy = load_poies_policy("spec/poies_policy.yaml")
proofs = [
    # HashShare
    {"proof_id": b"\x01"*32, "proof_type": ProofType.HASH, "metrics": {"d_ratio": 0.20}},
    # AI proof
    {"proof_id": b"\x02"*32, "proof_type": ProofType.AI, "metrics":
        {"ai_units": 12.0, "qos": 0.93, "traps_ratio": 0.87, "redundancy": 1.2}},
    # Quantum proof
    {"proof_id": b"\x03"*32, "proof_type": ProofType.QUANTUM, "metrics":
        {"quantum_units": 3.1, "traps_ratio": 0.82, "qos": 0.90}},
]
theta = 2_000_000  # µ-nats
base_entropy = 500_000  # e.g., H(u) component

result = aggregate_and_accept(
    proofs, policy, theta_micro=theta, base_entropy_micro=base_entropy,
    hooks=default_score_hooks(policy),
)

print(result.accepted, result.score_micro, result.breakdown["per_type_after_gamma"])

Determinism & Rounding
----------------------
- All ψ are computed as non-negative floats then converted to µ-nats via
  `round(max(0, x) * 1e6)`.
- Caps use deterministic proportional downscale with lexicographic tie-breaking
  on `proof_id` (see consensus.caps).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import (Any, Callable, Dict, Iterable, List, Mapping,
                    MutableMapping, Optional, Sequence, Tuple)

from .caps import Contribution, apply_all_caps, clip_per_type, clip_total_gamma
from .policy import PoiesPolicy
from .types import MicroNat, ProofType

# ---------------------------------------------------------------------------
# Hook interface & defaults
# ---------------------------------------------------------------------------

# A scoring hook maps a metrics dict → ψ_micro (int µ-nats, non-negative).
ScoreHook = Callable[[Mapping[str, Any], PoiesPolicy], MicroNat]


@dataclass(frozen=True)
class AggregateResult:
    sum_psi: float
    breakdown: Mapping[Any, float]


def _to_micro(x: float) -> MicroNat:
    """Convert a non-negative real 'nats' weight to integer micro-nats deterministically."""
    if not math.isfinite(x) or x <= 0.0:
        return 0
    # Round to nearest integer micro-nat (ties to away-from-zero via round in py3 == bankers? We
    # prefer conventional round; downstream caps keep determinism). Use +1e-12 to avoid
    # pathological float errors near .5 boundaries.
    return int(round(x * 1_000_000 + 1e-12))


# ---------------------------------------------------------------------------
# Lightweight aggregate helper (float domain; test-only)
# ---------------------------------------------------------------------------


def aggregate(psi_by_kind: Mapping[Any, float], policy: Any) -> AggregateResult:
    """Aggregate ψ by proof kind applying per-type and Γ caps (float domain)."""

    clipped = clip_per_type(psi_by_kind, policy)
    total = sum(clipped.values())
    scale = clip_total_gamma(total, policy)
    scaled = {k: v * scale for k, v in clipped.items()}
    return AggregateResult(sum_psi=sum(scaled.values()), breakdown=scaled)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def default_score_hooks(policy: PoiesPolicy) -> Dict[ProofType, ScoreHook]:
    """
    Build default per-type scoring hooks. Each hook reads from policy.weights[<type>] if present.
    Weight keys are optional; sensible defaults are used if absent.

    Expected metrics per type
    -------------------------
    HASH:
      - d_ratio: float ≥ 0    (share difficulty ratio vs target; e.g., 0.2 means ~20% of block)
    AI:
      - ai_units: float ≥ 0   (normalized compute units)
      - qos: [0,1]            (delivery quality / SLO success)
      - traps_ratio: [0,1]    (trap success rate)
      - redundancy: ≥ 1       (effective replicate count; ≥1)
    QUANTUM:
      - quantum_units: float ≥ 0
        OR supply: depth, width, shots (fallback formula if quantum_units missing)
      - traps_ratio: [0,1]
      - qos: [0,1]
    STORAGE:
      - size_gib: float ≥ 0
      - availability: [0,1]   (uptime/availability in window)
      - heartbeat_ok: bool    (base gate)
      - retrieval_bonus: [0,1] (optional)
    VDF:
      - seconds: float ≥ 0    (or 'iterations' combined with a scale)
      - iterations: int ≥ 0   (optional)
    """
    W = getattr(policy, "weights", {})  # may be a dict-like keyed by ProofType or str

    def _W(pt: ProofType, key: str, default: float) -> float:
        src = W.get(pt, {}) if isinstance(W, dict) else {}
        val = src.get(key, default) if isinstance(src, dict) else default
        try:
            return float(val)
        except Exception:
            return default

    # HASH — gently increasing function of share difficulty ratio
    def score_hash(metrics: Mapping[str, Any], _policy: PoiesPolicy) -> MicroNat:
        d_ratio = max(0.0, float(metrics.get("d_ratio", 0.0)))
        # weight * ln(1 + d_ratio)
        k = _W(ProofType.HASH, "k_ln", 0.25)
        return _to_micro(k * math.log1p(d_ratio))

    # AI — multiplicative of units, QoS, trap success, with redundancy penalty
    def score_ai(metrics: Mapping[str, Any], _policy: PoiesPolicy) -> MicroNat:
        units = max(0.0, float(metrics.get("ai_units", 0.0)))
        qos = _clamp01(float(metrics.get("qos", 1.0)))
        traps = _clamp01(float(metrics.get("traps_ratio", 1.0)))
        redundancy = max(1.0, float(metrics.get("redundancy", 1.0)))
        # Trap quality ramp between t_min and t_target
        t_min = _W(ProofType.AI, "t_min", 0.6)
        t_tar = _W(ProofType.AI, "t_target", 0.85)
        if t_tar <= t_min:
            t_tar = max(t_min + 1e-6, 0.999999)
        if traps <= t_min:
            q_traps = 0.0
        elif traps >= t_tar:
            q_traps = 1.0
        else:
            q_traps = (traps - t_min) / (t_tar - t_min)
        # Redundancy penalty exponent
        rho = _W(ProofType.AI, "redundancy_exp", 1.0)
        k_units = _W(ProofType.AI, "k_units", 1.0)
        score = k_units * units * qos * q_traps / (redundancy**rho)
        return _to_micro(score)

    # QUANTUM — similar form, with optional unit synthesis from depth×width×log1p(shots)
    def score_quantum(metrics: Mapping[str, Any], _policy: PoiesPolicy) -> MicroNat:
        units = metrics.get("quantum_units", None)
        if units is None:
            depth = max(0.0, float(metrics.get("depth", 0.0)))
            width = max(0.0, float(metrics.get("width", 0.0)))
            shots = max(0.0, float(metrics.get("shots", 0.0)))
            units = depth * width * math.log1p(shots)
        units = max(0.0, float(units))
        qos = _clamp01(float(metrics.get("qos", 1.0)))
        traps = _clamp01(float(metrics.get("traps_ratio", 1.0)))
        # Trap ramp (separate knobs)
        t_min = _W(ProofType.QUANTUM, "t_min", 0.65)
        t_tar = _W(ProofType.QUANTUM, "t_target", 0.9)
        if t_tar <= t_min:
            t_tar = max(t_min + 1e-6, 0.999999)
        if traps <= t_min:
            q_traps = 0.0
        elif traps >= t_tar:
            q_traps = 1.0
        else:
            q_traps = (traps - t_min) / (t_tar - t_min)
        k_units = _W(ProofType.QUANTUM, "k_units", 1.5)
        score = k_units * units * qos * q_traps
        return _to_micro(score)

    # STORAGE — proportional to committed size and availability, gated by heartbeat
    def score_storage(metrics: Mapping[str, Any], _policy: PoiesPolicy) -> MicroNat:
        hb = bool(metrics.get("heartbeat_ok", False))
        if not hb:
            return 0
        size_gib = max(0.0, float(metrics.get("size_gib", 0.0)))
        avail = _clamp01(float(metrics.get("availability", 0.0)))
        retrieval_bonus = _clamp01(float(metrics.get("retrieval_bonus", 0.0)))
        k_size = _W(
            ProofType.STORAGE, "k_size", 0.02
        )  # µ-nats per GiB @ full availability (pre-micro scale)
        # Small convexity in availability to reward near-perfect uptime
        alpha = _W(ProofType.STORAGE, "availability_exp", 1.2)
        score = k_size * size_gib * (avail**alpha) * (1.0 + 0.25 * retrieval_bonus)
        return _to_micro(score)

    # VDF — proportional to verified time/iterations
    def score_vdf(metrics: Mapping[str, Any], _policy: PoiesPolicy) -> MicroNat:
        seconds = metrics.get("seconds", None)
        if seconds is None:
            iters = max(0.0, float(metrics.get("iterations", 0.0)))
            iters_scale = _W(ProofType.VDF, "iters_to_seconds", 1e-9)
            seconds = iters * iters_scale
        seconds = max(0.0, float(seconds))
        k_sec = _W(ProofType.VDF, "k_seconds", 0.05)
        # Diminishing returns: log1p seconds
        score = k_sec * math.log1p(seconds)
        return _to_micro(score)

    return {
        ProofType.HASH: score_hash,
        ProofType.AI: score_ai,
        ProofType.QUANTUM: score_quantum,
        ProofType.STORAGE: score_storage,
        ProofType.VDF: score_vdf,
    }


# ---------------------------------------------------------------------------
# Scoring, aggregation, acceptance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofInput:
    proof_id: bytes
    proof_type: ProofType
    metrics: Mapping[str, Any]


@dataclass(frozen=True)
class PerProofOut:
    proof_id: bytes
    proof_type: ProofType
    psi_raw_micro: MicroNat
    psi_capped_micro: MicroNat


@dataclass(frozen=True)
class ScoreOutcome:
    accepted: bool
    score_micro: MicroNat  # S = base_entropy + Σψ_capped
    theta_micro: MicroNat
    base_entropy_micro: MicroNat
    per_proof: List[PerProofOut]
    breakdown: Dict[str, Any]  # sums by stage, per-type tables, caps diagnostics


def score_vector(
    proofs: Sequence[ProofInput],
    policy: PoiesPolicy,
    hooks: Optional[Mapping[ProofType, ScoreHook]] = None,
) -> Tuple[List[Contribution], Dict[str, Any]]:
    """
    Compute ψ_raw per proof (µ-nats) and return as `Contribution` vector for cap processing.
    Also returns a small diagnostics dict containing per-type raw sums.
    """
    if hooks is None:
        hooks = default_score_hooks(policy)

    contributions: List[Contribution] = []
    per_proof_raw: List[Tuple[bytes, ProofType, MicroNat]] = []
    for p in proofs:
        hook = hooks.get(p.proof_type)
        if hook is None:
            psi = 0
        else:
            psi = max(0, int(hook(p.metrics, policy)))
        contributions.append(Contribution(p.proof_id, p.proof_type, psi))
        per_proof_raw.append((p.proof_id, p.proof_type, psi))

    # Raw sums
    per_type_raw: Dict[ProofType, MicroNat] = {pt: 0 for pt in ProofType}
    raw_total = 0
    for _, pt, psi in per_proof_raw:
        per_type_raw[pt] += psi
        raw_total += psi

    diag = {
        "sum_raw": raw_total,
        "per_type_raw": {pt.name: per_type_raw[pt] for pt in ProofType},
    }
    return contributions, diag


def aggregate_and_accept(
    proofs: Sequence[Mapping[str, Any]],
    policy: PoiesPolicy,
    *,
    theta_micro: MicroNat,
    base_entropy_micro: MicroNat = 0,
    hooks: Optional[Mapping[ProofType, ScoreHook]] = None,
) -> ScoreOutcome:
    """
    High-level entry: compute ψ, apply caps, sum, and compare against Θ.

    Parameters
    ----------
    proofs : sequence of dicts with keys:
        - proof_id: bytes
        - proof_type: ProofType
        - metrics: Mapping[str, Any]
    policy : PoiesPolicy
    theta_micro : int
        Current target threshold (µ-nats).
    base_entropy_micro : int
        Base entropy contribution, typically H(u) (µ-nats).
    hooks : optional per-type ScoreHook override

    Returns
    -------
    ScoreOutcome with detailed per-stage breakdown.
    """
    # Normalize inputs to ProofInput
    norm: List[ProofInput] = []
    for item in proofs:
        pid = item["proof_id"]
        ptype = item["proof_type"]
        metrics = item.get("metrics", {})
        if not isinstance(pid, (bytes, bytearray)):
            raise TypeError("proof_id must be bytes")
        if not isinstance(metrics, Mapping):
            raise TypeError("metrics must be Mapping[str, Any]")
        norm.append(ProofInput(proof_id=bytes(pid), proof_type=ptype, metrics=metrics))

    # Score → contributions
    contribs, diag = score_vector(norm, policy, hooks=hooks)

    # Apply caps (per-proof, per-type, Γ)
    capped, cap_stats = apply_all_caps(contribs, policy)

    # Build per-proof outputs aligned with input order
    per_proof_out: List[PerProofOut] = []
    for before, after in zip(contribs, capped):
        per_proof_out.append(
            PerProofOut(
                proof_id=before.proof_id,
                proof_type=before.proof_type,
                psi_raw_micro=before.psi_micro,
                psi_capped_micro=after.psi_micro,
            )
        )

    # Sums and acceptance
    sum_capped = sum(c.psi_micro for c in capped)
    S = int(base_entropy_micro) + int(sum_capped)
    accepted = S >= int(theta_micro)

    # Per-type tables for each stage
    breakdown = {
        "theta_micro": int(theta_micro),
        "base_entropy_micro": int(base_entropy_micro),
        "sum_raw": int(diag["sum_raw"]),
        "sum_after_per_proof": int(cap_stats.sum_after_per_proof),
        "sum_after_per_type": int(cap_stats.sum_after_per_type),
        "sum_after_gamma": int(cap_stats.sum_after_gamma),
        "per_type_raw": diag["per_type_raw"],
        "per_type_after_per_proof": {
            k.name if hasattr(k, "name") else str(k): v
            for k, v in cap_stats.per_type_after_per_proof.items()
        },
        "per_type_after_per_type": {
            k.name if hasattr(k, "name") else str(k): v
            for k, v in cap_stats.per_type_after_per_type.items()
        },
        "per_type_after_gamma": {
            k.name if hasattr(k, "name") else str(k): v
            for k, v in cap_stats.per_type_after_gamma.items()
        },
        "gamma_cap_micro": int(getattr(policy, "gamma_cap", 0)),
        "distance_micro": int(S - int(theta_micro)),
    }

    return ScoreOutcome(
        accepted=accepted,
        score_micro=S,
        theta_micro=int(theta_micro),
        base_entropy_micro=int(base_entropy_micro),
        per_proof=per_proof_out,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Convenience: sum ψ only (no acceptance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SumOutcome:
    sum_raw_micro: MicroNat
    sum_after_per_proof_micro: MicroNat
    sum_after_per_type_micro: MicroNat
    sum_after_gamma_micro: MicroNat
    per_type_raw: Dict[str, MicroNat]
    per_type_after_gamma: Dict[str, MicroNat]
    per_proof: List[PerProofOut]


def sum_psi(
    proofs: Sequence[Mapping[str, Any]],
    policy: PoiesPolicy,
    *,
    hooks: Optional[Mapping[ProofType, ScoreHook]] = None,
) -> SumOutcome:
    contribs, diag = score_vector(
        [
            ProofInput(bytes(p["proof_id"]), p["proof_type"], p.get("metrics", {}))
            for p in proofs
        ],
        policy,
        hooks=hooks,
    )
    capped, cap_stats = apply_all_caps(contribs, policy)
    per_proof_out = [
        PerProofOut(c0.proof_id, c0.proof_type, c0.psi_micro, c1.psi_micro)
        for (c0, c1) in zip(contribs, capped)
    ]
    return SumOutcome(
        sum_raw_micro=int(diag["sum_raw"]),
        sum_after_per_proof_micro=int(cap_stats.sum_after_per_proof),
        sum_after_per_type_micro=int(cap_stats.sum_after_per_type),
        sum_after_gamma_micro=int(cap_stats.sum_after_gamma),
        per_type_raw=dict(diag["per_type_raw"]),
        per_type_after_gamma={
            k.name if hasattr(k, "name") else str(k): v
            for k, v in cap_stats.per_type_after_gamma.items()
        },
        per_proof=per_proof_out,
    )


# ---------------------------------------------------------------------------
# Self-test (optional)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from .types import ProofType

    class _Pol:
        def __init__(self):
            # Mimic the policy fields used by caps & weights
            from collections import namedtuple

            Cap = namedtuple("Cap", "per_type_micro per_proof_micro_max")
            self.caps = {
                ProofType.HASH: Cap(5_000_000, 3_000_000),
                ProofType.AI: Cap(7_000_000, 5_000_000),
                ProofType.QUANTUM: Cap(7_000_000, 5_000_000),
                ProofType.STORAGE: Cap(6_000_000, 4_000_000),
                ProofType.VDF: Cap(6_000_000, 4_000_000),
            }
            self.gamma_cap = 12_000_000
            self.weights = {
                ProofType.AI: {"k_units": 1.2},
                ProofType.QUANTUM: {"k_units": 1.8},
            }

    policy = _Pol()
    hooks = default_score_hooks(policy)
    theta = 6_000_000
    base = 500_000

    proofs = [
        {
            "proof_id": b"\x01" * 32,
            "proof_type": ProofType.HASH,
            "metrics": {"d_ratio": 0.3},
        },
        {
            "proof_id": b"\x02" * 32,
            "proof_type": ProofType.AI,
            "metrics": {
                "ai_units": 3.0,
                "qos": 0.9,
                "traps_ratio": 0.88,
                "redundancy": 1.0,
            },
        },
        {
            "proof_id": b"\x03" * 32,
            "proof_type": ProofType.QUANTUM,
            "metrics": {"quantum_units": 1.0, "traps_ratio": 0.83, "qos": 0.95},
        },
    ]

    out = aggregate_and_accept(
        proofs, policy, theta_micro=theta, base_entropy_micro=base, hooks=hooks
    )
    print("ACCEPTED:", out.accepted, "S=", out.score_micro, "Θ=", out.theta_micro)
    print("per_type_after_gamma:", out.breakdown["per_type_after_gamma"])
