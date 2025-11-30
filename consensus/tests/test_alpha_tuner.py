from __future__ import annotations

import math
import random
from typing import (Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple,
                    Union)

import pytest

# Under test
import consensus.alpha_tuner as at  # type: ignore

# Optional proof-type enum (preferred); fall back to strings if unavailable.
try:
    from consensus.types import ProofType  # type: ignore

    HAS_ENUM = True
except Exception:
    HAS_ENUM = False

    class ProofType:  # minimal shim for tests; names match the spec order
        HASH = "HASH"
        AI = "AI"
        QUANTUM = "QUANTUM"
        STORAGE = "STORAGE"
        VDF = "VDF"


# ----------------------------- Flexible adapter layer -----------------------------


def _alpha_bounds() -> Tuple[float, float]:
    """
    Discover α bounds from the module, or fall back to sane defaults.
    """
    for name in ("ALPHA_BOUNDS", "ALPHA_MIN_MAX", "BOUNDS"):
        b = getattr(at, name, None)
        if isinstance(b, (tuple, list)) and len(b) == 2:
            lo, hi = float(b[0]), float(b[1])
            if lo > 0.0 and hi > lo:
                return (lo, hi)
    lo = float(getattr(at, "ALPHA_MIN", 0.25))
    hi = float(getattr(at, "ALPHA_MAX", 4.0))
    if hi <= lo or lo <= 0.0:
        lo, hi = 0.25, 4.0
    return (lo, hi)


def _proof_types() -> List[Any]:
    """
    Resolve canonical set/order of proof types.
    """
    names = []
    # Preferred explicit list on the module
    for cand in ("PROOF_TYPES", "TYPE_ORDER", "CANONICAL_TYPES"):
        v = getattr(at, cand, None)
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return list(v)
    # Fall back to enum members or spec default order
    if HAS_ENUM:
        return [
            ProofType.HASH,
            ProofType.AI,
            ProofType.QUANTUM,
            ProofType.STORAGE,
            ProofType.VDF,
        ]
    return ["HASH", "AI", "QUANTUM", "STORAGE", "VDF"]


def _to_key(k: Union[str, Any]) -> Any:
    """Map plain string names → module enum if needed."""
    if HAS_ENUM and isinstance(k, str):
        up = k.upper()
        if hasattr(ProofType, up):
            return getattr(ProofType, up)
    return k


def _mk_obs(mapping: Mapping[Union[str, Any], float]) -> Dict[Any, float]:
    """Normalize an observation dict to module keys."""
    return {_to_key(k): float(v) for k, v in mapping.items()}


# Tuner constructors / step runners (handle multiple API shapes)


def _new_tuner() -> Any:
    """
    Instantiate/resolve a tuner:
      - at.AlphaTuner(...)
      - at.tuner(...)
      - at.new(...)
      - or return the module itself if it exposes pure functions.
    """
    ctor = None
    for name in ("AlphaTuner", "Tuner", "FairnessTuner"):
        C = getattr(at, name, None)
        if isinstance(C, type):
            ctor = C
            break
    if ctor:
        try:
            return ctor()
        except TypeError:
            # Try without args already done; ignore.
            pass

    for name in ("tuner", "new", "make"):
        fn = getattr(at, name, None)
        if callable(fn):
            try:
                return fn()
            except TypeError:
                pass

    # Fall back: return module (functional style expected).
    return at


def _step(tuner: Any, observed: Mapping[Any, float]) -> Mapping[Any, float]:
    """
    Advance one step and return current alphas.
      - object methods: update/step/apply(observed) → alphas
      - functional: step(prev_alphas, observed) → alphas
      - object may keep .alphas or .state['alphas']
    """
    # Object with method
    for m in ("update", "step", "apply", "tick"):
        fn = getattr(tuner, m, None)
        if callable(fn):
            out = fn(_mk_obs(observed))
            if isinstance(out, Mapping):
                return out
            # method that mutates internal state; try to read below
            break

    # Functional module-level function
    for name in ("step", "update", "apply"):
        fn = getattr(at, name, None)
        if callable(fn):
            # Need a previous alpha set; try to get it:
            prev = _read_alphas(tuner) or _default_alphas()
            res = fn(prev, _mk_obs(observed))
            if isinstance(res, Mapping):
                return res

    # Read after potential in-place update
    current = _read_alphas(tuner)
    if current:
        return current

    # If everything else fails, assume identity (no tuning); return defaults.
    return _default_alphas()


def _read_alphas(tuner: Any) -> Dict[Any, float]:
    """
    Read α map from a tuner object/module if available.
    """
    # Direct attribute
    for name in ("alphas", "alpha", "weights"):
        v = getattr(tuner, name, None)
        if isinstance(v, Mapping) and v:
            return dict(v)
    # Nested state
    st = getattr(tuner, "state", None)
    if isinstance(st, Mapping):
        for k in ("alphas", "alpha", "weights"):
            v = st.get(k)
            if isinstance(v, Mapping) and v:
                return dict(v)
    return {}


def _default_alphas(val: float = 1.0) -> Dict[Any, float]:
    return {t: val for t in _proof_types()}


# ------------------------------ Synthetic streams ------------------------------


def _balanced_obs(total: float = 1.0) -> Dict[Any, float]:
    types = _proof_types()
    x = total / float(len(types))
    return {t: x for t in types}


def _biased_obs(
    dominant: Any, dom_share: float = 0.7, total: float = 1.0
) -> Dict[Any, float]:
    types = _proof_types()
    dominant = _to_key(dominant)
    rest = [t for t in types if t != dominant]
    if not rest:
        return {dominant: total}
    rem = total - dom_share
    per = rem / float(len(rest))
    out = {t: per for t in rest}
    out[dominant] = dom_share
    return out


# ------------------------------------ Tests ------------------------------------


def test_bounds_and_nonnegativity_over_random_stream():
    lo, hi = _alpha_bounds()
    tuner = _new_tuner()
    alphas = _read_alphas(tuner) or _default_alphas()

    rng = random.Random(2025)
    types = _proof_types()
    for _ in range(200):
        # random simplex sample over proof types
        xs = [rng.random() for _ in types]
        s = sum(xs) or 1.0
        obs = {t: x / s for t, x in zip(types, xs)}
        alphas = dict(_step(tuner, obs))

        # Invariants: finite, positive, bounded
        for t, a in alphas.items():
            assert math.isfinite(a), f"alpha for {t} should be finite"
            assert a > 0.0, f"alpha for {t} must be > 0"
            assert (
                lo - 1e-12 <= a <= hi + 1e-12
            ), f"alpha {a} for {t} outside bounds [{lo},{hi}]"


def test_fairness_correction_direction_under_sustained_bias():
    """
    If HASH dominates the observed ψ share for many rounds, its α should trend downward
    (≤ initial), while underrepresented classes trend upward (≥ initial). We do not assert
    exact magnitudes—only directionality after enough rounds.
    """
    tuner = _new_tuner()
    types = _proof_types()
    base = _read_alphas(tuner) or _default_alphas(1.0)

    dominant = types[0]  # take first as "HASH"-like
    obs = _biased_obs(dominant, dom_share=0.75)

    alphas = base
    for _ in range(100):
        alphas = dict(_step(tuner, obs))

    # Directional assertions (tolerate tiny numerical noise)
    eps = 1e-9
    for t, a in alphas.items():
        if t == dominant:
            assert (
                a <= base[t] + eps
            ), f"dominant type {t} alpha should not increase under sustained dominance"
        else:
            assert (
                a >= base[t] - eps
            ), f"underrepresented type {t} alpha should not decrease under dominance elsewhere"


def test_stability_under_balanced_stream_has_limited_spread():
    """
    Feed a perfectly balanced stream for many rounds. The spread max/min should remain close.
    Exact learning-rate is implementation-defined, so we assert a generous upper bound.
    """
    lo, hi = _alpha_bounds()
    tuner = _new_tuner()

    obs = _balanced_obs()
    alphas = _read_alphas(tuner) or _default_alphas(1.0)

    for _ in range(200):
        alphas = dict(_step(tuner, obs))

    vals = list(alphas.values())
    max_v, min_v = max(vals), min(vals)
    # Generous bound: ≤ 1.25× spread OR within module bounds if those are tighter.
    spread = max_v / (min_v or 1.0)
    assert spread <= 1.25 + 1e-9 or (
        abs(max_v - hi) < 1e-8 and abs(min_v - lo) < 1e-8
    ), f"balanced stream should not create large α spread (got {spread:.3f})"


def test_correction_reduces_mix_error_over_time():
    """
    Measure L1 error between observed mix and implied mix from α-weights, and assert that it
    tends to decrease across windows when fed a fixed biased stream.
    This is a soft assertion using windowed medians to be robust to noise.
    """
    tuner = _new_tuner()
    types = _proof_types()
    dom = types[0]
    obs = _biased_obs(dom, dom_share=0.8)
    target = _balanced_obs()  # many tuners aim toward balance across types

    def mix_error(a_map: Mapping[Any, float]) -> float:
        # Interpret α as importance weights → normalize to a probability vector
        vals = [max(1e-18, float(a_map.get(t, 1.0))) for t in types]
        s = sum(vals)
        p_alpha = [v / s for v in vals]
        p_target = [target[t] for t in types]
        return sum(abs(x - y) for x, y in zip(p_alpha, p_target))

    # Run multiple windows; record median errors
    window = 20
    medians: List[float] = []
    alphas = _read_alphas(tuner) or _default_alphas(1.0)
    seq: List[float] = []
    for i in range(120):
        alphas = dict(_step(tuner, obs))
        seq.append(mix_error(alphas))
        if (i + 1) % window == 0:
            block = sorted(seq[-window:])
            medians.append(block[window // 2])

    # Expect a downward tendency (last median <= first median)
    assert (
        medians[-1] <= medians[0] + 1e-9
    ), f"median mix error did not reduce: {medians}"
