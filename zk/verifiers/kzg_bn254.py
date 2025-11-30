"""
Animica zk.verifiers.kzg_bn254
==============================

Minimal KZG (Kate) polynomial commitment verification on BN254 (altbn128).

Verification equation (single opening):
    e(C - y * G1, G2) == e(π, s*G2 - x * G2)

Where:
- C ∈ G1 is the commitment to polynomial f
- x ∈ F_p is the evaluation point
- y = f(x) ∈ F_p is the claimed value
- π ∈ G1 is the KZG opening proof
- G1, G2 are canonical generators
- s*G2 is part of the public verifying key (toxic waste s unknown)

This module:
- Uses `py_ecc` as the primary backend (optimized_bn128 if present).
- Reuses the pairing wrapper from `.pairing_bn254`.
- Avoids any bytes/serialization logic; callers pass native py_ecc point types.
- Performs curve and shape validation unless explicitly disabled.

License: MIT
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Pairing helpers and generators from our wrapper
from .pairing_bn254 import (check_pairing_product, curve_order, g1_generator,
                            g2_generator, is_on_curve_g1, is_on_curve_g2, pair)

# ---- py_ecc group ops (backend-agnostic import) -----------------------------
try:  # Prefer optimized backend
    from py_ecc.optimized_bn128 import add as _add  # type: ignore
    from py_ecc.optimized_bn128 import b as _B
    from py_ecc.optimized_bn128 import b2 as _B2
    from py_ecc.optimized_bn128 import is_on_curve as _is_on_curve
    from py_ecc.optimized_bn128 import multiply as _mul
    from py_ecc.optimized_bn128 import neg as _neg

    _BACKEND = "py_ecc.optimized_bn128"
except Exception:  # pragma: no cover
    from py_ecc.bn128 import add as _add  # type: ignore
    from py_ecc.bn128 import b as _B
    from py_ecc.bn128 import b2 as _B2
    from py_ecc.bn128 import is_on_curve as _is_on_curve
    from py_ecc.bn128 import multiply as _mul
    from py_ecc.bn128 import neg as _neg

    _BACKEND = "py_ecc.bn128"


# ---- Types ------------------------------------------------------------------
G1Point = Any  # opaque py_ecc point
G2Point = Any  # opaque py_ecc point


@dataclass(frozen=True)
class VerifyingKey:
    """
    Verifying key for single-point KZG verification.

    Attributes
    ----------
    g1 : G1Point
        Canonical G1 generator.
    g2 : G2Point
        Canonical G2 generator.
    s_g2 : G2Point
        Trusted-setup element s * g2.
    """

    g1: G1Point
    g2: G2Point
    s_g2: G2Point


__all__ = [
    "VerifyingKey",
    "kzg_verify",
    "verify",  # alias
    "make_verifying_key",
    "is_on_curve_g1",
    "is_on_curve_g2",
]


# ---- Helpers ----------------------------------------------------------------


def _on_curve_g1(P: G1Point) -> bool:
    try:
        return bool(_is_on_curve(P, _B))
    except TypeError:  # older py_ecc signature
        return bool(_is_on_curve(P))


def _on_curve_g2(Q: G2Point) -> bool:
    try:
        return bool(_is_on_curve(Q, _B2))
    except TypeError:
        return bool(_is_on_curve(Q))


def _safe_scalar(z: int) -> int:
    """Reduce a Python int modulo the BN254 subgroup order."""
    q = curve_order()
    return int(z) % q


def make_verifying_key(s: int) -> VerifyingKey:
    """
    Build a verifying key from toxic-waste scalar `s` (for tests/dev only).

    In production, you load `s_g2` from a real trusted setup; the scalar `s`
    must *not* be known.
    """
    g1 = g1_generator()
    g2 = g2_generator()
    s_mod = _safe_scalar(s)
    s_g2 = _mul(g2, s_mod)
    return VerifyingKey(g1=g1, g2=g2, s_g2=s_g2)


# ---- Core verification -------------------------------------------------------


def kzg_verify(
    commitment: G1Point,
    x: int,
    y: int,
    proof: G1Point,
    vk: VerifyingKey,
    *,
    validate: bool = True,
) -> bool:
    """
    Verify a single KZG opening (commitment, point x, value y, proof π).

    Parameters
    ----------
    commitment : G1Point
        Commitment C to polynomial f (built with a matching SRS).
    x : int
        Evaluation point in the base field (reduced mod curve order internally).
    y : int
        Claimed value y = f(x) in the base field (reduced mod curve order internally).
    proof : G1Point
        Opening proof π in G1.
    vk : VerifyingKey
        Contains (g1, g2, s_g2).
    validate : bool
        If True (default), check that all points lie on the expected curves.

    Returns
    -------
    bool
        True if the pairing check passes.
    """
    # Optional input validation: ensure points appear well-formed and on-curve
    if validate:
        if not (_on_curve_g1(commitment) and is_on_curve_g1(commitment)):
            return False
        if not (_on_curve_g1(proof) and is_on_curve_g1(proof)):
            return False
        if not (_on_curve_g2(vk.g2) and is_on_curve_g2(vk.g2)):
            return False
        if not (_on_curve_g2(vk.s_g2) and is_on_curve_g2(vk.s_g2)):
            return False

    # Scalars modulo group order
    x_mod = _safe_scalar(x)
    y_mod = _safe_scalar(y)

    # Left: (C - y*G1, G2)
    C_minus_yG1 = _add(commitment, _neg(_mul(vk.g1, y_mod)))

    # Right: (π, s_g2 - x*G2)
    sG2_minus_xG2 = _add(vk.s_g2, _neg(_mul(vk.g2, x_mod)))

    # Check: e(C - yG1, G2) == e(π, sG2 - xG2)
    # Equivalent product check: e(C - yG1, G2) * e(-π, sG2 - xG2) == 1
    minus_proof = _neg(proof)
    return check_pairing_product([(C_minus_yG1, vk.g2), (minus_proof, sG2_minus_xG2)])


# Friendly alias
verify = kzg_verify


# ---- Self-test (manual) ------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    # Tiny sanity check for linear polynomial f(t) = a0 + a1 * t
    # Commitment C = a0*G1 + a1*(s*G1). Proof for opening at x is π = a1 * G1.
    from random import Random

    print(f"[kzg_bn254] backend={_BACKEND}")

    rnd = Random(1337)
    q = curve_order()
    a0 = rnd.randrange(1, q)
    a1 = rnd.randrange(1, q)
    s = rnd.randrange(2, q - 1)  # toxic waste (dev only)
    x = rnd.randrange(1, q)

    # Build verifying key from s (dev only)
    vk = make_verifying_key(s)

    # We'll also need s*G1 locally to create a commitment for the test.
    g1 = vk.g1
    s_g1 = _mul(g1, _safe_scalar(s))

    # Commit f(t) = a0 + a1*t  ->  C = a0*G1 + a1*(s*G1)
    C = _add(_mul(g1, a0), _mul(s_g1, a1))

    # Evaluate y = f(x)
    y = (a0 + a1 * x) % q

    # For a linear poly, quotient q(t) = a1 (constant), so π = a1 * G1
    pi = _mul(g1, a1)

    ok = kzg_verify(C, x, y, pi, vk)
    print("  verify(linear) ->", ok)
    assert ok, "KZG verify failed for linear self-test"

    # Negative test: flip y
    ok_bad = kzg_verify(C, x, (y + 1) % q, pi, vk)
    print("  verify(bad y)  ->", ok_bad)
    assert not ok_bad, "KZG verify unexpectedly succeeded with wrong y"
