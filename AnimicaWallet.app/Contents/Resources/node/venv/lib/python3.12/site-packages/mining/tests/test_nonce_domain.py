import binascii
import importlib
import math
import os
import types

import pytest

# Import the module under test
nd = importlib.import_module("mining.nonce_domain")


def _get_callable(mod: types.ModuleType, candidates):
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


# Candidate function names used across our drafts:
H_func = _get_callable(nd, ("H_from_u", "H", "neg_log_u", "minus_log_u"))
bytes_to_u = _get_callable(
    nd,
    (
        "bytes_to_uniform_u",
        "bytes_to_u",
        "digest_to_u",
        "hash_to_uniform_u",
        "uniform_from_bytes",
    ),
)
# Optional higher-level u-draw helpers (header/mix/nonce bound)
u_from_fields = _get_callable(
    nd,
    (
        "u_from_header_nonce",
        "u_from_nonce",
        "u_from_fields",
        "draw_u",
    ),
)


@pytest.mark.parametrize(
    "u",
    [1e-12, 1e-6, 1e-3, 0.1, 0.25, 0.5, 0.75, 0.9, 1 - 2**-40],
)
def test_neg_log_matches_math(u):
    if H_func is None:
        pytest.skip("H(u) helper not found in mining.nonce_domain")
    H = H_func(u)
    assert math.isfinite(H)
    assert H >= 0.0
    # Compare to -ln(u) within a tiny tolerance; allow a hair more near 1.0
    assert math.isclose(H, -math.log(u), rel_tol=1e-12, abs_tol=1e-12)


def _expected_bytes_to_u(b: bytes) -> float:
    """Reference mapping: big-endian integer scaled to (0,1), avoiding exact 0."""
    nbits = 8 * len(b)
    if nbits == 0:
        raise ValueError("empty input")
    N = int.from_bytes(b, "big")
    # Avoid exact 0 → raise tiny epsilon; clamp just below 1.0
    if N == 0:
        N = 1
    denom = 1 << nbits
    u = N / denom
    # Guard against rounding to exactly 1.0
    return min(max(u, 2.0 / denom), 1.0 - 1.0 / denom)


@pytest.mark.parametrize(
    "hexbytes",
    [
        "00" * 32,
        "ff" * 32,
        "0123456789abcdef" * 4,
        "c3d2e1f0" * 8,
    ],
)
def test_bytes_to_u_interval_and_order(hexbytes):
    if bytes_to_u is None:
        pytest.skip("bytes→u helper not found in mining.nonce_domain")
    b = binascii.unhexlify(hexbytes)
    u = bytes_to_u(b)
    assert 0.0 < u < 1.0, "u must lie in the open interval (0,1)"
    # Deterministic and equals reference within tight tolerance
    ref = _expected_bytes_to_u(b)
    assert math.isclose(u, ref, rel_tol=0, abs_tol=2**-200)


def test_bytes_to_u_determinism_distinct_inputs():
    if bytes_to_u is None:
        pytest.skip("bytes→u helper not found in mining.nonce_domain")
    b0 = b"\x00" * 32
    b1 = b"\xff" * 32
    b2 = binascii.unhexlify("0123456789abcdef" * 4)
    u0a = bytes_to_u(b0)
    u0b = bytes_to_u(b0)
    assert u0a == u0b, "same bytes must map to the same u"
    u1 = bytes_to_u(b1)
    u2 = bytes_to_u(b2)
    # Not a strict property, but with our distinct inputs we expect distinct us
    assert len({u0a, u1, u2}) >= 2


def test_monotonicity_of_H():
    if H_func is None:
        pytest.skip("H(u) helper not found in mining.nonce_domain")
    # If u1 < u2 then H(u1) > H(u2)
    u_small = 1e-9
    u_big = 0.9
    H_small = H_func(u_small)
    H_big = H_func(u_big)
    assert H_small > H_big
    # And H(1 - eps) ~ eps for tiny eps
    eps = 2**-40
    near_one = 1.0 - eps
    assert math.isclose(H_func(near_one), -math.log(near_one), rel_tol=0, abs_tol=1e-10)


def test_u_from_fields_optional_paths():
    """
    If the module exposes a higher-level draw function that binds header/mix/nonce,
    sanity-check determinism and sensitivity to nonce.
    """
    if u_from_fields is None:
        pytest.skip("no header/mix/nonce u-draw helper exported")

    header_hash = binascii.unhexlify("ab" * 32)
    mix_seed = binascii.unhexlify("cd" * 32)

    u0 = u_from_fields(header_hash, 0, mix_seed)  # type: ignore[arg-type]
    u0_again = u_from_fields(header_hash, 0, mix_seed)  # type: ignore[arg-type]
    u1 = u_from_fields(header_hash, 1, mix_seed)  # type: ignore[arg-type]

    assert 0.0 < u0 < 1.0
    assert u0 == u0_again, "same inputs must be deterministic"
    assert u0 != u1, "changing nonce should change the draw (overwhelmingly likely)"
