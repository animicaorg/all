from __future__ import annotations

import math
from typing import Any, Dict

import pytest

from consensus.tests import load_policy_example

# ---- small helpers -----------------------------------------------------------

def _dig(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """
    Try a sequence of alternative nested key paths (dot-separated) and return the first found.
    Example: _dig(p, "gamma.total", "Gamma.total", "total_gamma")
    """
    for path in keys:
        cur: Any = d
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            elif isinstance(cur, dict) and part.lower() in {k.lower(): k for k in cur}.keys():
                # case-insensitive match
                # remap to actual key with matching lower() to avoid KeyError
                lower_map = {k.lower(): k for k in cur}
                cur = cur[lower_map[part.lower()]]
            else:
                ok = False
                break
        if ok:
            return cur
    return default


def _as_float(x: Any) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            pass
    raise AssertionError(f"Expected numeric, got {type(x).__name__}: {x!r}")


# ---- tests -------------------------------------------------------------------

def test_policy_basic_keys():
    p = load_policy_example()  # YAML → dict
    assert isinstance(p, dict), "policy fixture should parse to a dict"

    total_gamma = _dig(p, "gamma.total", "Gamma.total", "total_gamma", "caps.gamma_total")
    assert total_gamma is not None, "total Γ (gamma) cap must be present"
    assert _as_float(total_gamma) > 0.0, "total Γ must be positive"

    escort_q = _dig(p, "escort.q", "fairness.escort_q", "fairness.q")
    assert escort_q is not None, "escort q must be present"
    q = _as_float(escort_q)
    assert 0.0 < q <= 1.0, "escort q should be in (0, 1]"

    # Must have caps per proof *type*
    per_type = _dig(p, "caps.per_type", "per_type_caps", "caps.types")
    assert isinstance(per_type, dict), "per-type caps must be a mapping"

def test_required_proof_types_present():
    p = load_policy_example()
    per_type = _dig(p, "caps.per_type", "per_type_caps", "caps.types")
    assert isinstance(per_type, dict)

    keys = {k.lower() for k in per_type.keys()}
    # required proof types (as per PoIES design): hash, ai, quantum, storage, vdf (bonus)
    for required in ("hash", "ai", "quantum", "storage"):
        assert required in keys, f"missing per-type cap for {required}"
    # vdf is optional/bonus but recommended
    assert "vdf" in keys or "vdf" not in keys, "noop assertion to document optional VDF cap"

def test_caps_are_positive_and_reasonable():
    p = load_policy_example()
    total_gamma = _as_float(_dig(p, "gamma.total", "Gamma.total", "total_gamma", "caps.gamma_total"))
    per_type = _dig(p, "caps.per_type", "per_type_caps", "caps.types")
    assert isinstance(per_type, dict)

    caps = {k.lower(): _as_float(v) for k, v in per_type.items()}

    # All per-type caps must be non-negative and not absurdly larger than total Γ
    for k, v in caps.items():
        assert v >= 0.0, f"cap for {k} must be ≥ 0"
        assert v <= max(total_gamma, 1.0) * 1.01, f"cap for {k} should not exceed total Γ"

    # Sum of per-type caps should not wildly exceed total Γ (allow a small slack for config)
    total_per_type = sum(caps.values())
    assert total_per_type <= total_gamma * 1.10 + 1e-9, (
        f"sum of per-type caps ({total_per_type}) should be ≤ ~total Γ ({total_gamma})"
    )

def test_escort_parameters_sound():
    p = load_policy_example()
    escort_q = _as_float(_dig(p, "escort.q", "fairness.escort_q", "fairness.q"))
    # If other escort parameters exist, sanity check them too
    escort_window = _dig(p, "escort.window", "fairness.window", "escort_window")
    if escort_window is not None:
        w = int(escort_window)
        assert w >= 1, "escort window must be at least 1 block"
        # If q < 1, window should not be trivially tiny (heuristic)
        if escort_q < 1.0:
            assert w >= 4, "with q<1, escort window should be at least a few blocks"

def test_weights_if_present_are_probabilities():
    p = load_policy_example()
    weights = _dig(p, "weights", "psi.weights", "caps.weights")
    if weights is None:
        pytest.skip("weights not present in example policy; this is allowed")
    assert isinstance(weights, dict), "weights must be a mapping if present"
    s = 0.0
    for k, v in weights.items():
        val = _as_float(v)
        assert 0.0 <= val <= 1.0, f"weight for {k} must be in [0,1]"
        s += val
    # Allow small numeric slack
    assert math.isclose(s, 1.0, rel_tol=1e-6, abs_tol=1e-6), f"weights should sum to 1, got {s}"

