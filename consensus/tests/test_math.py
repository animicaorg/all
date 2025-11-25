from __future__ import annotations

import math
import pytest

# Expect these to exist per consensus/math.py
from consensus.math import H, to_munats, from_munats


# ---- H(u) = -ln(u) basics ----------------------------------------------------

def test_H_at_one_is_zero():
    assert H(1.0) == pytest.approx(0.0, abs=1e-15)

def test_H_positive_on_0_1():
    for u in (0.9, 0.5, 0.1, 1e-6, 1e-12):
        val = H(u)
        assert val > 0.0
        # sanity: close to -ln(u)
        assert val == pytest.approx(-math.log(u), rel=1e-12, abs=1e-12)

def test_H_domain_errors():
    # Values outside (0, 1] must be rejected
    for bad in (-1.0, -0.0, 0.0, 1.000000001):
        with pytest.raises(Exception):
            _ = H(bad)

def test_H_monotonicity_strict_decreasing_in_u():
    """
    For 0 < u1 < u2 <= 1, H(u1) > H(u2).
    We check several pairs, including extreme small u.
    """
    us = [1.0, 0.9, 0.5, 0.2, 0.05, 1e-6, 1e-12]
    # H should strictly increase as u decreases
    hs = [H(u) for u in us]
    for i in range(len(us) - 1):
        assert us[i] > us[i + 1] or us[i] == 1.0  # order check (descending u aside from first)
        assert hs[i] < hs[i + 1] or us[i] == us[i + 1]  # strict unless equal u


# ---- µ-nats conversions ------------------------------------------------------

def test_to_from_munats_roundtrip_small_values():
    vals = [0.0, 1e-6, 0.123456, 1.0, 3.141592, 20.5, -3.25]
    for v in vals:
        mu = to_munats(v)
        back = from_munats(mu)
        # from_munats has 1e-6 granularity, so allow 0.5 µ-nat tolerance
        assert back == pytest.approx(v, abs=0.5e-6)

def test_to_munats_is_integer_scaled():
    # 1.234567 nats => 1_234_567 µ-nats
    assert to_munats(1.234567) == 1_234_567
    # negatives handled symmetrically
    assert to_munats(-1.234567) == -1_234_567

def test_from_munats_scaling_and_sign():
    assert from_munats(1_000_000) == pytest.approx(1.0, abs=1e-12)
    assert from_munats(-2_500_000) == pytest.approx(-2.5, abs=1e-12)

def test_munats_monotone_mapping():
    # If x < y then to_munats(x) <= to_munats(y) (allow equality due to quantization)
    pairs = [(-2.0, -1.0), (-0.1, 0.0), (0.0, 0.0000004), (0.5, 0.5000004), (1.0, 1.5)]
    for x, y in pairs:
        assert x < y
        assert to_munats(x) <= to_munats(y)


# ---- Edge/stability checks ---------------------------------------------------

def test_H_numeric_stability_very_small_u():
    u = 1e-12
    expected = -math.log(u)
    got = H(u)
    # relative agreement within tight tolerance; absolute bounds sane
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert 20.0 < got < 40.0  # rough magnitude sanity

def test_H_rejects_nan_inf():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(Exception):
            _ = H(bad)
