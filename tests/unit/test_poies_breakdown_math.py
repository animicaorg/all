# SPDX-License-Identifier: Apache-2.0
"""
PoIES breakdown math: Γ (Gamma) and ψ (per-proof effective credits) aggregation.

This test suite provides a tiny, dependency-free reference of the PoIES math used
by dashboards and explorers. It validates:
  - ψ_t = min(raw_t, cap_t) clipping per proof type t
  - Γ = Σ_t w_t * ψ_t  (weights w_t >= 0; typically Σ w_t = 1)
  - Mix percentages m_t = ψ_t / Σ_u ψ_u (or 0 if Σ_u ψ_u == 0)
  - Invariants: 0 ≤ ψ_t ≤ cap_t, Γ ≤ Σ_t w_t*cap_t, and Σ_t m_t ≈ 1 when Σψ>0
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ------------------------------- Reference math -------------------------------

ProofType = str


@dataclass(frozen=True)
class PoiesParams:
    """Network configuration for PoIES aggregation."""

    weights: Mapping[ProofType, float]
    caps: Mapping[ProofType, float]

    def __post_init__(self):
        # Basic sanity of domains
        for k, v in self.weights.items():
            if v < 0:
                raise ValueError(f"weight[{k}] must be ≥ 0")
        for k, v in self.caps.items():
            if v < 0:
                raise ValueError(f"cap[{k}] must be ≥ 0")
        if set(self.weights) != set(self.caps):
            raise ValueError("weights and caps must have identical proof-type keys")


def compute_psi(
    raw: Mapping[ProofType, float], caps: Mapping[ProofType, float]
) -> Dict[ProofType, float]:
    """
    ψ clipping per proof type: ψ_t = min(max(raw_t, 0), cap_t).
    Negative inputs are treated as 0 (defensive).
    """
    psi: Dict[ProofType, float] = {}
    for t, cap in caps.items():
        r = float(raw.get(t, 0.0))
        if r < 0:
            r = 0.0
        psi[t] = r if r <= cap else cap
    return psi


def compute_gamma(
    psi: Mapping[ProofType, float], weights: Mapping[ProofType, float]
) -> float:
    """Γ = Σ_t w_t * ψ_t."""
    return sum(float(weights[t]) * float(psi.get(t, 0.0)) for t in weights.keys())


def compute_mix(psi: Mapping[ProofType, float]) -> Dict[ProofType, float]:
    """
    Mix percentages: m_t = ψ_t / Σ_u ψ_u (0 if Σψ == 0).
    Returns values in [0,1] that sum ~1 when Σψ>0.
    """
    total = sum(psi.values())
    if total <= 0:
        return {t: 0.0 for t in psi.keys()}
    return {t: (v / total) for t, v in psi.items()}


# Default tiny config used by deterministic tests
DEFAULT_TYPES = ("hashshare", "ai", "quantum", "storage", "vdf")
DEFAULT_PARAMS = PoiesParams(
    weights={
        "hashshare": 0.45,
        "ai": 0.20,
        "quantum": 0.20,
        "storage": 0.10,
        "vdf": 0.05,
    },
    caps={
        "hashshare": 1.00,
        "ai": 0.50,
        "quantum": 0.50,
        "storage": 0.30,
        "vdf": 0.20,
    },
)


# --------------------------------- Unit cases ---------------------------------


def test_gamma_and_mix_with_caps_and_overflow():
    """
    Synthetic raw contributions exceed several caps:
      raw = {hashshare:1.40, ai:0.60, quantum:0.25, storage:0.40, vdf:0.05}
      caps clip to ψ = {1.00, 0.50, 0.25, 0.30, 0.05}
    Expect:
      Γ = 0.45*1.00 + 0.20*0.50 + 0.20*0.25 + 0.10*0.30 + 0.05*0.05 = 0.655
      Σψ = 2.10; mixes are ψ/Σψ
    """
    raw = {"hashshare": 1.40, "ai": 0.60, "quantum": 0.25, "storage": 0.40, "vdf": 0.05}
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)
    assert psi == pytest.approx(
        {"hashshare": 1.00, "ai": 0.50, "quantum": 0.25, "storage": 0.30, "vdf": 0.05}
    )

    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    assert gamma == pytest.approx(0.655, rel=1e-9, abs=1e-12)

    mix = compute_mix(psi)
    total_mix = sum(mix.values())
    assert total_mix == pytest.approx(1.0, abs=1e-12)
    assert mix["hashshare"] == pytest.approx(1.00 / 2.10)
    assert mix["ai"] == pytest.approx(0.50 / 2.10)
    assert mix["quantum"] == pytest.approx(0.25 / 2.10)
    assert mix["storage"] == pytest.approx(0.30 / 2.10)
    assert mix["vdf"] == pytest.approx(0.05 / 2.10)


def test_under_saturation_no_caps_triggered():
    """
    All raw contributions under caps; Γ is just weighted sum of raw.
    """
    raw = {"hashshare": 0.50, "ai": 0.25, "quantum": 0.10, "storage": 0.10, "vdf": 0.10}
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)
    assert psi == pytest.approx(raw)

    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    expected = 0.45 * 0.50 + 0.20 * 0.25 + 0.20 * 0.10 + 0.10 * 0.10 + 0.05 * 0.10
    assert gamma == pytest.approx(expected, rel=1e-9)


def test_zero_contributions_all_zero():
    """
    If no contributions, Γ = 0 and mix is all zeros (avoid NaN).
    """
    raw = {t: 0.0 for t in DEFAULT_TYPES}
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)
    assert all(v == 0.0 for v in psi.values())

    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    assert gamma == 0.0

    mix = compute_mix(psi)
    assert all(v == 0.0 for v in mix.values())
    assert sum(mix.values()) == 0.0


def test_single_type_saturates_cap_others_zero():
    """
    Only one proof type contributes and hits cap; Γ equals that weight * cap.
    """
    raw = {"hashshare": 5.0}  # exceeds cap; others omitted → treated as 0
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)
    assert psi["hashshare"] == DEFAULT_PARAMS.caps["hashshare"]
    for t in DEFAULT_TYPES:
        assert psi.get(t, 0.0) <= DEFAULT_PARAMS.caps[t]

    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    assert gamma == pytest.approx(
        DEFAULT_PARAMS.weights["hashshare"] * DEFAULT_PARAMS.caps["hashshare"]
    )

    mix = compute_mix(psi)
    # Σψ equals hashshare cap; so its mix is 1.0 and others 0
    assert mix["hashshare"] == pytest.approx(1.0)
    for t in DEFAULT_TYPES:
        if t != "hashshare":
            assert mix[t] == 0.0


def test_invariants_upper_bound_and_nonnegativity():
    """
    Invariants hold for arbitrary (non-negative) raw inputs:
      - 0 ≤ ψ_t ≤ cap_t
      - Γ ≤ Σ_t w_t*cap_t
    """
    cap_bound = sum(
        DEFAULT_PARAMS.weights[t] * DEFAULT_PARAMS.caps[t] for t in DEFAULT_TYPES
    )

    raw = {t: 10.0 for t in DEFAULT_TYPES}  # well above caps
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)
    # Upper bounds
    for t in DEFAULT_TYPES:
        assert 0.0 <= psi[t] <= DEFAULT_PARAMS.caps[t]
    # Γ bound
    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    assert gamma <= cap_bound + 1e-12


# ------------------------------ Property tests --------------------------------

_float_nonneg = st.floats(
    min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False
)


@given(
    hashshare=_float_nonneg,
    ai=_float_nonneg,
    quantum=_float_nonneg,
    storage=_float_nonneg,
    vdf=_float_nonneg,
)
def test_properties_hold_under_random_inputs(hashshare, ai, quantum, storage, vdf):
    raw = {
        "hashshare": hashshare,
        "ai": ai,
        "quantum": quantum,
        "storage": storage,
        "vdf": vdf,
    }
    psi = compute_psi(raw, DEFAULT_PARAMS.caps)

    # Clip & nonnegativity
    for t in DEFAULT_TYPES:
        assert 0.0 <= psi[t] <= DEFAULT_PARAMS.caps[t]

    gamma = compute_gamma(psi, DEFAULT_PARAMS.weights)
    cap_bound = sum(
        DEFAULT_PARAMS.weights[t] * DEFAULT_PARAMS.caps[t] for t in DEFAULT_TYPES
    )
    assert 0.0 <= gamma <= cap_bound + 1e-12

    mix = compute_mix(psi)
    if sum(psi.values()) == 0.0:
        assert all(v == 0.0 for v in mix.values())
    else:
        assert sum(mix.values()) == pytest.approx(1.0, abs=1e-9)
        for v in mix.values():
            assert 0.0 <= v <= 1.0 + 1e-12
