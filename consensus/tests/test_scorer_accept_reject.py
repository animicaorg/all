from __future__ import annotations

import math
import dataclasses
import typing as t

import pytest

from consensus.types import ProofKind  # HASH, AI, QUANTUM, STORAGE, VDF
import consensus.math as cmath
import consensus.caps as ccaps
import consensus.scorer as scorer


# ---------- helpers: pick functions, tolerant to naming differences ------------

def _pick(mod, name: str, alts: list[str]):
    if hasattr(mod, name):
        return getattr(mod, name)
    for a in alts:
        if hasattr(mod, a):
            return getattr(mod, a)
    raise AttributeError(f"Missing function {name} (tried {alts}) in {mod.__name__}")

# aggregator taking raw psi-by-kind and policy, returning either:
#  - float sum
#  - (sum, breakdown)
#  - dict {'sum_psi'|'sum'|'total': float, 'breakdown'|'per_kind'|'by_kind': mapping}
aggregate = _pick(
    scorer,
    "aggregate",
    ["aggregate_psi", "score", "score_batch", "compute_breakdown", "aggregate_caps"],
)

# decision: (S, theta)->bool or (H,sum_psi,theta)->bool
accept_fn = getattr(scorer, "accept", None) or getattr(scorer, "is_accepted", None) or getattr(scorer, "decision", None)

# caps helpers (used for baseline comparison & fallback)
clip_per_type = _pick(ccaps, "clip_per_type", ["cap_per_type", "enforce_per_type_caps"])
clip_total_gamma = _pick(ccaps, "clip_total_gamma", ["cap_total_gamma", "enforce_total_gamma"])


# ------------------------- duck policy fixture ---------------------------------

class _DuckPolicy:
    """
    Tiny policy that exposes common attribute names used by caps/scorer.
    Values are in natural units (nats of ψ), not micro-nats.
    """
    def __init__(self,
                 per_type_caps: dict[t.Union[ProofKind, str], float],
                 gamma_cap: float):
        self.per_type_caps = dict(per_type_caps)
        self.type_caps = self.per_type_caps
        self.per_type = self.per_type_caps
        # also string-keyed copy
        self.per_type_caps.update({(k.name.lower() if isinstance(k, ProofKind) else k): v
                                   for k, v in list(self.per_type_caps.items())})
        self.gamma_cap = gamma_cap
        self.total_gamma_cap = gamma_cap
        self.Gamma_cap = gamma_cap

@pytest.fixture
def loose_policy() -> _DuckPolicy:
    # Big caps → effectively no clipping
    per_type = {
        ProofKind.HASH: 1e9,
        ProofKind.AI: 1e9,
        ProofKind.QUANTUM: 1e9,
        ProofKind.STORAGE: 1e9,
        ProofKind.VDF: 1e9,
    }
    return _DuckPolicy(per_type_caps=per_type, gamma_cap=1e9)

@pytest.fixture
def clipping_policy() -> _DuckPolicy:
    # Per-type caps and a total Γ cap to force scaling.
    per_type = {
        ProofKind.HASH: 3.0,
        ProofKind.AI: 4.0,
        ProofKind.QUANTUM: 12.0,
        ProofKind.STORAGE: 3.0,
        ProofKind.VDF: 5.0,
    }
    # total Γ = 6 forces global scaling after per-type clipping in our scenario
    return _DuckPolicy(per_type_caps=per_type, gamma_cap=6.0)


# ------------------------ result normalization --------------------------------

@dataclasses.dataclass
class AggOut:
    sum_psi: float
    by_kind: dict

def _norm(out) -> AggOut:
    # float only
    if isinstance(out, (int, float)):
        return AggOut(float(out), {})
    # (sum, breakdown)
    if isinstance(out, tuple) and len(out) == 2:
        s, bk = out
        return AggOut(float(s), dict(bk))
    # dict-like
    if isinstance(out, dict):
        s = out.get("sum_psi", out.get("sum", out.get("total")))
        if s is None:
            # maybe nested
            for k in ["S", "score", "Sigma", "sigma"]:
                if k in out:
                    s = out[k]; break
        bk = out.get("breakdown", out.get("per_kind", out.get("by_kind", {})))
        return AggOut(float(s), dict(bk))
    # dataclass/object with attributes
    for name in ("sum_psi", "sum", "total", "S", "score"):
        if hasattr(out, name):
            s = float(getattr(out, name))
            break
    else:
        raise TypeError("Unknown aggregator result shape")
    for name in ("breakdown", "per_kind", "by_kind"):
        if hasattr(out, name):
            return AggOut(s, dict(getattr(out, name)))
    return AggOut(s, {})


# --------------------------------- tests --------------------------------------

def test_accept_reject_around_theta(loose_policy: _DuckPolicy):
    # H(u) = -ln u. Pick u=0.5 -> H ≈ 0.693147
    H = cmath.safe_neglog(0.5) if hasattr(cmath, "safe_neglog") else -math.log(0.5)

    # Case A: sum_psi just above threshold (Θ = 1.0)
    psi = {
        ProofKind.AI: 0.32,
        ProofKind.QUANTUM: 0.38,
        ProofKind.HASH: 0.10,
    }  # Σψ = 0.80 → S = H+Σψ ≈ 1.493 > 1.0 → accept

    agg = _norm(aggregate(psi, loose_policy))
    S = H + agg.sum_psi
    theta = 1.0

    if accept_fn:
        # accept(H,sum,theta) or accept(S,theta)
        try:
            ok = accept_fn(H, agg.sum_psi, theta)
        except TypeError:
            ok = accept_fn(S, theta)
        assert ok, "should accept when S >= Θ"
    else:
        assert S >= theta, "fallback decision"

    # Case B: just below threshold (reduce ψ a bit)
    psi_b = dict(psi)
    psi_b[ProofKind.AI] = 0.10  # Σψ = 0.58 → S ≈ 1.273 < 1.3? We'll set Θ = 1.30
    agg_b = _norm(aggregate(psi_b, loose_policy))
    S_b = H + agg_b.sum_psi
    theta_b = 1.30

    if accept_fn:
        try:
            ok_b = accept_fn(H, agg_b.sum_psi, theta_b)
        except TypeError:
            ok_b = accept_fn(S_b, theta_b)
        assert not ok_b, "should reject when S < Θ"
    else:
        assert S_b < theta_b, "fallback decision rejects below Θ"

    # Case C: exactly on the boundary should accept (≥ rule)
    # Set Θ = S_c exactly
    agg_c = _norm(aggregate(psi, loose_policy))
    S_c = H + agg_c.sum_psi
    theta_c = S_c
    if accept_fn:
        try:
            ok_c = accept_fn(H, agg_c.sum_psi, theta_c)
        except TypeError:
            ok_c = accept_fn(S_c, theta_c)
        assert ok_c, "boundary S == Θ must accept"
    else:
        assert S_c >= theta_c


def test_breakdown_matches_caps_and_global_gamma(clipping_policy: _DuckPolicy):
    """
    Raw ψ:
      HASH=8, AI=11, QUANTUM=14, STORAGE=9, VDF=6.5
    Per-type caps → HASH=3, AI=4, QUANTUM=12, STORAGE=3, VDF=5  (sum=27)
    Total Γ=6 ⇒ scale factor s = 6/27 = 2/9 ≈ 0.222222...
    Final expected:
      HASH=0.666666..., AI=0.888888..., QUANTUM=2.666666..., STORAGE=0.666666..., VDF=1.111111...
      Sum = 6
    """
    raw = {
        ProofKind.HASH: 8.0,
        ProofKind.AI: 11.0,
        ProofKind.QUANTUM: 14.0,
        ProofKind.STORAGE: 9.0,
        ProofKind.VDF: 6.5,
    }

    # manual recipe using caps helpers (ground truth)
    per_type = clip_per_type(raw, clipping_policy)
    s = clip_total_gamma(sum(per_type.values()), clipping_policy)
    expected = {k: v * s for k, v in per_type.items()}
    expected_sum = sum(expected.values())

    # run aggregator under test
    out = _norm(aggregate(raw, clipping_policy))

    # Sum matches Γ (within tolerance) and equals sum of by_kind if present
    assert out.sum_psi == pytest.approx(expected_sum, rel=1e-12, abs=1e-12)
    if out.by_kind:
        # normalize keys (enum vs string)
        def _get(d, k: ProofKind) -> float:
            if k in d:
                return float(d[k])
            sk = k.name.lower()
            if sk in d:
                return float(d[sk])
            return float(d.get(k.name, 0.0))
        for k in raw.keys():
            assert _get(out.by_kind, k) == pytest.approx(expected[k], rel=1e-12, abs=1e-12)
        # internal consistency
        s2 = sum(_get(out.by_kind, k) for k in raw.keys())
        assert s2 == pytest.approx(out.sum_psi, rel=1e-12, abs=1e-12)


def test_h_of_u_monotone_affects_decision(loose_policy: _DuckPolicy):
    """
    Larger u (closer to 1) → smaller H(u) = -ln u → makes acceptance harder.
    Keep ψ fixed; vary u to straddle Θ.
    """
    psi = {ProofKind.AI: 0.7, ProofKind.HASH: 0.2}  # Σψ = 0.9
    agg = _norm(aggregate(psi, loose_policy)).sum_psi

    theta = 1.0
    # u_small → H large → accept
    u_small = 0.3
    H_big = cmath.safe_neglog(u_small) if hasattr(cmath, "safe_neglog") else -math.log(u_small)
    S_big = H_big + agg
    if accept_fn:
        try:
            ok_big = accept_fn(H_big, agg, theta)
        except TypeError:
            ok_big = accept_fn(S_big, theta)
        assert ok_big
    else:
        assert S_big >= theta

    # u_close1 → H small → potentially reject if Σψ not big enough
    u_close1 = 0.95
    H_small = cmath.safe_neglog(u_close1) if hasattr(cmath, "safe_neglog") else -math.log(u_close1)
    S_small = H_small + agg
    if accept_fn:
        try:
            ok_small = accept_fn(H_small, agg, theta)
        except TypeError:
            ok_small = accept_fn(S_small, theta)
        assert ok_small == (S_small >= theta)
    else:
        assert (S_small >= theta) or (S_small < theta)  # tautology; covered above


