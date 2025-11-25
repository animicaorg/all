"""
Animica zk.verifiers.pairing_bn254
==================================

Thin, production-ready BN254 (altbn128) Ate pairing wrapper.

- Primary backend: `py_ecc` (uses the optimized bn128 path when available)
- Optional native hook: if `animica_native.bn254` is importable, you can opt-in
  to it via `use_native=True` in the API calls (no hard dependency).

Public API
----------
- pair(P: G1Point, Q: G2Point, *, use_native: bool = False) -> GTElement
- product_of_pairings(pairs: Iterable[tuple[G1Point, G2Point]], *, use_native: bool = False) -> GTElement
- check_pairing_product(pairs: Iterable[tuple[G1Point, G2Point]], *, use_native: bool = False) -> bool
- is_on_curve_g1(P), is_on_curve_g2(Q)
- normalize_g1(P) / normalize_g2(Q)  (to affine)
- g1_generator(), g2_generator(), curve_order()

Notes
-----
- Point ordering follows the common convention e(P, Q) with P in G1, Q in G2.
  The underlying `py_ecc` pairing call expects (Q, P); this wrapper handles it.
- All operations are *deterministic* and pure Python unless a native adapter is provided.
- This module intentionally avoids serialization concerns; higher layers (SDK / RPC)
  should handle bytes ↔ field-element conversions consistently.

Safety
------
- We validate (shape + on-curve) before pairing unless one of the points is the
  point-at-infinity (which pair to the identity in GT).
- Any failure in the native path falls back to the Python backend.

License: MIT (matches repository policy)
"""

from __future__ import annotations

from typing import Any, Iterable, Tuple, Optional

# -------------------------
# Backend selection (py_ecc)
# -------------------------

# Try the optimized backend first; fall back to reference if needed.
# Both expose compatible symbols for our usage.
try:  # Optimized, faster if present
    from py_ecc.optimized_bn128 import (  # type: ignore
        FQ, FQ2, FQ12, add, b as _B, b2 as _B2, curve_order as _Q, field_modulus as _P,
        is_on_curve as _is_on_curve, pairing as _pairing, normalize as _normalize,
        G1 as _G1, G2 as _G2,
    )
    _BACKEND_NAME = "py_ecc.optimized_bn128"
except Exception:  # pragma: no cover
    from py_ecc.bn128 import (  # type: ignore
        FQ, FQ2, FQ12, add, b as _B, b2 as _B2, curve_order as _Q, field_modulus as _P,
        is_on_curve as _is_on_curve, pairing as _pairing, normalize as _normalize,
        G1 as _G1, G2 as _G2,
    )
    _BACKEND_NAME = "py_ecc.bn128"

# Optional native hook (no hard dependency). If present, we will try to use it
# when the caller sets use_native=True. Any exception -> graceful Python fallback.
try:  # pragma: no cover - optional
    from animica_native import bn254 as _native  # hypothetical accelerated adapter
    _NATIVE_OK = True
except Exception:  # pragma: no cover
    _native = None  # type: ignore
    _NATIVE_OK = False


# -------------------------
# Types & helpers
# -------------------------

# We do not strictly type the point internals (they differ by backend).
# Treat them as opaque tuples that py_ecc understands.
G1Point = Any
G2Point = Any
GTElement = FQ12

__all__ = [
    "pair",
    "product_of_pairings",
    "check_pairing_product",
    "is_on_curve_g1",
    "is_on_curve_g2",
    "normalize_g1",
    "normalize_g2",
    "g1_generator",
    "g2_generator",
    "curve_order",
    "field_modulus",
    "BACKEND_NAME",
    "NATIVE_AVAILABLE",
]

BACKEND_NAME: str = _BACKEND_NAME
NATIVE_AVAILABLE: bool = _NATIVE_OK


def curve_order() -> int:
    """Return the BN254 subgroup order q."""
    return int(_Q)


def field_modulus() -> int:
    """Return the base field modulus p."""
    return int(_P)


def g1_generator() -> G1Point:
    """Return the canonical G1 generator (Jacobian or projective per backend)."""
    return _G1


def g2_generator() -> G2Point:
    """Return the canonical G2 generator (Jacobian or projective per backend)."""
    return _G2


def _is_inf(P: Any) -> bool:
    """
    Heuristic point-at-infinity check compatible with py_ecc G1/G2 reps.

    py_ecc represents infinity as `None` in affine, or as a projective
    tuple with a zero Z. We handle the common cases; for any unknown
    shape we conservatively return False and let the backend raise on use.
    """
    if P is None:
        return True
    if isinstance(P, (tuple, list)) and len(P) == 3:
        # Projective/Jacobian: (x, y, z) with z == 0 => infinity
        z = P[2]
        try:
            # FQ/FQ2 both support `.n` (for FQ) or `.coeffs` (for FQ2). We only see FQ here.
            if hasattr(z, "n"):
                return int(z.n) == 0
            return False
        except Exception:
            return False
    return False


def is_on_curve_g1(P: G1Point) -> bool:
    """Return True if P is on G1 or is the point at infinity."""
    try:
        return _is_inf(P) or bool(_is_on_curve(P, _B))
    except TypeError:
        # Older py_ecc variants: is_on_curve(P) without 'b' param
        return _is_inf(P) or bool(_is_on_curve(P))


def is_on_curve_g2(Q: G2Point) -> bool:
    """Return True if Q is on G2 or is the point at infinity."""
    try:
        return _is_inf(Q) or bool(_is_on_curve(Q, _B2))
    except TypeError:
        return _is_inf(Q) or bool(_is_on_curve(Q))


def normalize_g1(P: G1Point) -> Optional[Tuple[int, int]]:
    """
    Normalize a G1 point to affine (x, y) over FQ, returning integers.
    Returns None for the point at infinity.
    """
    if _is_inf(P):
        return None
    ax, ay = _normalize(P)  # FQ elements
    return int(ax.n), int(ay.n)


def normalize_g2(Q: G2Point) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Normalize a G2 point to affine ((x_c0, x_c1), (y_c0, y_c1)) in FQ2 with integer limbs.
    Returns None for the point at infinity.
    """
    if _is_inf(Q):
        return None
    ax, ay = _normalize(Q)  # FQ2 elements
    # py_ecc FQ2 holds coeffs as [c0, c1] with value = c0 + c1 * i
    xc0, xc1 = (int(ax.coeffs[0].n), int(ax.coeffs[1].n))
    yc0, yc1 = (int(ay.coeffs[0].n), int(ay.coeffs[1].n))
    return (xc0, xc1), (yc0, yc1)


# -------------------------
# Pairing
# -------------------------

def pair(P: G1Point, Q: G2Point, *, use_native: bool = False, validate: bool = True) -> GTElement:
    """
    Compute the Ate pairing e(P, Q) on BN254.

    Parameters
    ----------
    P : G1Point
        Point in G1 (opaque point type understood by py_ecc).
    Q : G2Point
        Point in G2 (opaque point type understood by py_ecc).
    use_native : bool
        If True and a native adapter is available, attempt to use it.
        Any error falls back to the Python backend.
    validate : bool
        If True, checks that inputs are on-curve (or infinity) before pairing.

    Returns
    -------
    GTElement
        An element of FQ12 (GT). Equality to FQ12.one() can be used for checks.

    Raises
    ------
    ValueError
        If inputs are not on the curve and validate=True.
    """
    if validate:
        if not is_on_curve_g1(P):
            raise ValueError("G1 point is not on curve")
        if not is_on_curve_g2(Q):
            raise ValueError("G2 point is not on curve")

    # Pairings involving infinity return the identity in GT.
    if _is_inf(P) or _is_inf(Q):
        return FQ12.one()

    # Try native (optional) — exact API contract of the native adapter is project-defined.
    if use_native and _NATIVE_OK:  # pragma: no cover - native path optional
        try:
            # Expect the native adapter to accept affine ints; normalize first.
            P_aff = normalize_g1(P)
            Q_aff = normalize_g2(Q)
            if P_aff is None or Q_aff is None:
                return FQ12.one()
            (px, py) = P_aff
            (qx, qx1), (qy, qy1) = Q_aff
            gt = _native.pair_affine(px, py, qx, qx1, qy, qy1)  # project-specific
            # Expect gt to be a 12-tuple of FQ limbs or a backend-native FQ12; if tuple, rebuild:
            if isinstance(gt, tuple):
                # Reconstruct FQ12 from 12 limbs c0..c11 = (a + b*w + ...)
                coeffs = [FQ(int(c)) for c in gt]  # simplistic — your native may return FQ elements
                return FQ12(coeffs)  # type: ignore
            return gt  # already a backend element
        except Exception:
            # Fall back to Python backend if the native path hiccups
            pass

    # py_ecc pairing expects (Q, P)
    return _pairing(Q, P)


def product_of_pairings(
    pairs: Iterable[Tuple[G1Point, G2Point]], *, use_native: bool = False, validate: bool = True
) -> GTElement:
    """
    Compute ∏ e(P_i, Q_i) over an iterable of (P_i, Q_i).

    Returns an FQ12 element (GT). To check if the product is the identity, compare with FQ12.one().
    """
    acc = FQ12.one()
    for P, Q in pairs:
        acc *= pair(P, Q, use_native=use_native, validate=validate)
    return acc


def check_pairing_product(
    pairs: Iterable[Tuple[G1Point, G2Point]], *, use_native: bool = False, validate: bool = True
) -> bool:
    """
    Return True iff ∏ e(P_i, Q_i) == 1 in GT.

    Common use: signature or SNARK verification equations expressed as pairing products.
    """
    return product_of_pairings(pairs, use_native=use_native, validate=validate) == FQ12.one()


# -------------------------
# Simple self-test (manual)
# -------------------------

if __name__ == "__main__":  # pragma: no cover
    print(f"[pairing_bn254] Backend: {BACKEND_NAME} (native_available={NATIVE_AVAILABLE})")
    P = g1_generator()
    Q = g2_generator()

    # e(P, Q) ** curve_order == 1
    gt = pair(P, Q)
    ok1 = (gt ** curve_order()) == FQ12.one()

    # Bilinearity sanity: e(2P, Q) == e(P, Q) ** 2
    P2 = add(P, P)
    gt2 = pair(P2, Q)
    ok2 = gt2 == (gt * gt)

    # Product check: e(P, Q) * e(-P, Q) == 1
    # Construct -P as (x, -y, z) via addition with inverse (py_ecc has `add(P, -P)` trick)
    negP = (P[0], FQ(-P[1].n), P[2]) if isinstance(P, (tuple, list)) and len(P) == 3 else None
    if negP is None:
        # fallback via multiplying by curve_order-1
        from py_ecc.optimized_bn128 import multiply as _mul  # safe import if available
        negP = _mul(P, curve_order() - 1)  # type: ignore

    ok3 = check_pairing_product([(P, Q), (negP, Q)])

    print("  order check:", ok1)
    print("  bilinear   :", ok2)
    print("  product=1  :", ok3)
