from __future__ import annotations

import math
import types
import pytest

from consensus.types import ProofKind  # expected enum: HASH, AI, QUANTUM, STORAGE, VDF
import consensus.caps as caps


# ---- Helpers to make a duck-typed policy for caps ----------------------------

class _DuckPolicy:
    """
    A tiny policy object that exposes multiple likely attribute names so the caps
    module can read whichever it expects. Keys can be either ProofKind or str.
    """
    def __init__(self,
                 per_proof_caps: dict,
                 per_type_caps: dict,
                 gamma_cap: float):
        # Common/expected names:
        self.per_proof_caps = per_proof_caps
        self.per_type_caps = per_type_caps
        self.gamma_cap = gamma_cap
        # Alternate spellings some implementations might use:
        self.per_proof = per_proof_caps
        self.proof_caps = per_proof_caps
        self.type_caps = per_type_caps
        self.total_gamma_cap = gamma_cap
        self.Gamma_cap = gamma_cap


def _k(kind: ProofKind) -> tuple[ProofKind, str]:
    """Return both enum and string key for robustness."""
    return kind, kind.name.lower()


# ---- Basic fixtures ----------------------------------------------------------

@pytest.fixture
def sample_policy() -> _DuckPolicy:
    per_proof = {
        ProofKind.HASH: 2.0,
        ProofKind.AI: 5.0,
        ProofKind.QUANTUM: 7.5,
        ProofKind.STORAGE: 1.0,
        ProofKind.VDF: 3.0,
    }
    # also allow string keys (some loaders normalize to strings)
    per_proof.update({k.name.lower(): v for k, v in per_proof.items()})

    per_type = {
        ProofKind.HASH: 6.0,
        ProofKind.AI: 10.0,
        ProofKind.QUANTUM: 12.0,
        ProofKind.STORAGE: 3.0,
        ProofKind.VDF: 5.0,
    }
    per_type.update({k.name.lower(): v for k, v in per_type.items()})

    gamma = 20.0
    return _DuckPolicy(per_proof_caps=per_proof, per_type_caps=per_type, gamma_cap=gamma)


# ---- Resolve function names if they differ slightly --------------------------

def _pick_fn(name: str, alts: list[str]):
    if hasattr(caps, name):
        return getattr(caps, name)
    for a in alts:
        if hasattr(caps, a):
            return getattr(caps, a)
    raise AttributeError(f"caps module missing expected '{name}' (tried {alts})")


clip_per_proof = _pick_fn("clip_per_proof", ["cap_per_proof", "per_proof_clip"])
clip_per_type = _pick_fn("clip_per_type", ["cap_per_type", "per_type_clip", "enforce_per_type_caps"])
clip_total_gamma = _pick_fn("clip_total_gamma", ["cap_total_gamma", "total_gamma_clip", "enforce_total_gamma"])
# optional high-level vector applier; tests are tolerant if not present
_apply_caps_vector = getattr(caps, "apply_caps_vector", None)


# ---- Tests: per-proof clipping -----------------------------------------------

def test_per_proof_clip_bounds(sample_policy: _DuckPolicy):
    # Over cap → clipped
    assert clip_per_proof(3.5, ProofKind.HASH, sample_policy) == pytest.approx(2.0)
    # Under cap → unchanged
    assert clip_per_proof(1.25, ProofKind.HASH, sample_policy) == pytest.approx(1.25)
    # AI cap path
    assert clip_per_proof(9.0, ProofKind.AI, sample_policy) == pytest.approx(5.0)
    # Quantum cap path
    assert clip_per_proof(8.0, ProofKind.QUANTUM, sample_policy) == pytest.approx(7.5)


def test_per_proof_clip_never_increases(sample_policy: _DuckPolicy):
    values = [0.0, 0.5, 2.0, 9.0]
    for v in values:
        clipped = clip_per_proof(v, ProofKind.AI, sample_policy)
        assert clipped <= v + 1e-15  # never increases
        assert 0.0 <= clipped <= sample_policy.per_proof_caps[ProofKind.AI]


# ---- Tests: per-type clipping ------------------------------------------------

def test_per_type_clip_individual_caps(sample_policy: _DuckPolicy):
    sums = {
        ProofKind.HASH: 7.2,      # cap = 6.0
        ProofKind.AI: 3.0,        # below cap
        ProofKind.QUANTUM: 20.0,  # cap = 12.0
        ProofKind.STORAGE: 3.0,   # at cap
        ProofKind.VDF: 10.0,      # cap = 5.0
    }
    clipped = clip_per_type(sums, sample_policy)
    assert clipped[ProofKind.HASH] == pytest.approx(6.0)
    assert clipped[ProofKind.AI] == pytest.approx(3.0)
    assert clipped[ProofKind.QUANTUM] == pytest.approx(12.0)
    assert clipped[ProofKind.STORAGE] == pytest.approx(3.0)
    assert clipped[ProofKind.VDF] == pytest.approx(5.0)

    # No value should increase
    for k, v in sums.items():
        assert clipped[k] <= v + 1e-15


def test_per_type_clip_accepts_string_keys(sample_policy: _DuckPolicy):
    sums = {
        "hash": 9.0,       # -> 6.0
        "ai": 11.0,        # -> 10.0
        "quantum": 8.0,    # -> 8.0 (under 12.0)
        "storage": 9.0,    # -> 3.0
        "vdf": 1.0,        # -> 1.0
    }
    clipped = clip_per_type(sums, sample_policy)
    assert clipped["hash"] == pytest.approx(6.0)
    assert clipped["ai"] == pytest.approx(10.0)
    assert clipped["quantum"] == pytest.approx(8.0)
    assert clipped["storage"] == pytest.approx(3.0)
    assert clipped["vdf"] == pytest.approx(1.0)


# ---- Tests: total Γ clipping (global cap) ------------------------------------

def test_total_gamma_clip_scales_when_needed(sample_policy: _DuckPolicy):
    # Construct a post-type-clip vector that exceeds Γ
    vec = {
        ProofKind.HASH: 6.0,
        ProofKind.AI: 10.0,
        ProofKind.QUANTUM: 12.0,
        ProofKind.STORAGE: 3.0,
        ProofKind.VDF: 5.0,
    }
    total = sum(vec.values())  # 36.0
    assert total > sample_policy.gamma_cap

    s = clip_total_gamma(total, sample_policy)
    assert 0.0 < s < 1.0
    scaled_total = sum(v * s for v in vec.values())
    assert scaled_total == pytest.approx(sample_policy.gamma_cap, rel=1e-12, abs=1e-12)

def test_total_gamma_clip_noop_when_under_cap(sample_policy: _DuckPolicy):
    total = 9.999
    s = clip_total_gamma(total, sample_policy)
    assert s == pytest.approx(1.0)


# ---- Composition / high-level vector applier --------------------------------

@pytest.mark.skipif(_apply_caps_vector is None, reason="apply_caps_vector not exported")
def test_apply_caps_vector_matches_manual_recipe(sample_policy: _DuckPolicy):
    """
    Manual recipe:
      1) clip each per-type bucket by its per-type cap
      2) if Σ > Γ, multiply ALL buckets by s = Γ / Σ
    """
    raw = {
        ProofKind.HASH: 8.0,
        ProofKind.AI: 11.0,
        ProofKind.QUANTUM: 14.0,
        ProofKind.STORAGE: 9.0,
        ProofKind.VDF: 6.5,
    }
    # Step 1: per-type caps
    manual = clip_per_type(raw, sample_policy)
    # Step 2: global Γ scaling
    s = clip_total_gamma(sum(manual.values()), sample_policy)
    manual = {k: v * s for k, v in manual.items()}

    got = _apply_caps_vector(raw, sample_policy)
    # Compare bucket-wise
    for k in manual.keys():
        assert got[k] == pytest.approx(manual[k], rel=1e-12, abs=1e-12)
    # And totals match Γ within tolerance
    assert sum(got.values()) <= sample_policy.gamma_cap + 1e-12
    if sum(got.values()) > 0:
        # if scaling occurred, sum should be ~Γ
        assert sum(got.values()) == pytest.approx(
            min(sample_policy.gamma_cap, sum(clip_per_type(raw, sample_policy).values())),
            rel=1e-12, abs=1e-12
        )
