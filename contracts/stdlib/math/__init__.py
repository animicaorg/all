# -*- coding: utf-8 -*-
"""
contracts.stdlib.math
=====================

Deterministic, integer-only math helpers for Animica Python contracts.

Why this exists
---------------
Smart contracts must avoid Python floats (platform-/lib-dependent and
non-deterministic). This module offers **integer** primitives, fixed-point
helpers, and common fee/ratio utilities that:
- Never use floating point
- Are explicit about **rounding**
- Provide **saturating** and **checked** variants
- Fit typical contract needs (fees, EMAs, percentage math, bounds, sqrt)

Conventions
-----------
- All functions are **pure** and deterministic (aside from calling `abi.revert`).
- Rounding modes are explicit (DOWN = floor, UP = ceil, HALF_UP, HALF_EVEN).
- U256/S256 helpers mimic common on-chain numeric envelopes.
- PPM (parts-per-million) and BPS (basis points, 1e4) are provided.
- Fixed-point "wad" scale (1e18) is included for high-precision ratios.

**Never** import the Python `math` module except for `isqrt` (which is exact for
integers). No floats are used anywhere.

Examples
--------
    from stdlib.math import apply_bps, mul_div_down, wad_mul, ema_ppm

    # 25 bps fee on 1000 units
    fee = apply_bps(1000, 25)  # 2 (floor)

    # (a * b) / d with floor rounding, no overflow surprises
    q = mul_div_down(123456789, 987654321, 10**9)

    # high precision: (x * y) / 1e18
    z = wad_mul(10**21, 15 * 10**18)  # exact integer math

    # EMA with alpha = 200,000 ppm (0.2)
    v_next = ema_ppm(prev=1_000_000, sample=2_000_000, alpha_ppm=200_000)

"""

from __future__ import annotations

from typing import Final, Optional, Tuple

try:
    # Only imported lazily via helper below inside functions,
    # to play nice with the VM import guard.
    from stdlib import abi as _abi_mod  # type: ignore
except Exception:
    _abi_mod = None  # resolved lazily


# ---------------------------------------------------------------------------
# Lazy ABI accessor & revert helper
# ---------------------------------------------------------------------------


def _abi():
    global _abi_mod
    if _abi_mod is None:
        from stdlib import abi as _abi_mod  # type: ignore
    return _abi_mod


def _revert(msg: bytes) -> None:
    _abi().revert(msg)


# ---------------------------------------------------------------------------
# Numeric envelopes & constants
# ---------------------------------------------------------------------------

U256_MAX: Final[int] = (1 << 256) - 1
U128_MAX: Final[int] = (1 << 128) - 1
I256_MIN: Final[int] = -(1 << 255)
I256_MAX: Final[int] = (1 << 255) - 1

BPS_DEN: Final[int] = 10_000  # basis points denominator
PPM_DEN: Final[int] = 1_000_000  # parts per million
WAD: Final[int] = 10**18  # 1e18 fixed-point
RAY: Final[int] = 10**27  # 1e27 (optional higher precision)

# Rounding modes
ROUND_DOWN: Final[int] = 0
ROUND_UP: Final[int] = 1
ROUND_HALF_UP: Final[int] = 2
ROUND_HALF_EVEN: Final[int] = 3


# ---------------------------------------------------------------------------
# Guard & clamp helpers
# ---------------------------------------------------------------------------


def clamp(x: int, lo: int, hi: int) -> int:
    """Return x clamped into [lo, hi]."""
    if lo > hi:
        _revert(b"MATH:BAD_CLAMP_RANGE")
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def require_nonneg(*xs: int) -> None:
    """Revert if any provided integer is negative."""
    for n in xs:
        if n < 0:
            _revert(b"MATH:NEGATIVE")


def require_u256(*xs: int) -> None:
    """Revert if any value is outside [0, U256_MAX]."""
    for n in xs:
        if n < 0 or n > U256_MAX:
            _revert(b"MATH:U256_OOB")


def require_divisor(d: int) -> None:
    if d == 0:
        _revert(b"MATH:DIV_BY_ZERO")


# ---------------------------------------------------------------------------
# Rounding primitives
# ---------------------------------------------------------------------------


def div_round(n: int, d: int, mode: int = ROUND_DOWN) -> int:
    """
    Integer division with explicit rounding mode.
    - ROUND_DOWN: floor division
    - ROUND_UP: ceil division
    - ROUND_HALF_UP: round halves up (banking-unaware)
    - ROUND_HALF_EVEN: "banker's rounding" on halves
    """
    require_divisor(d)
    if mode == ROUND_DOWN:
        return n // d
    if mode == ROUND_UP:
        # ceil for possibly negative numerators too
        return -((-n) // d)
    # Half-aware: work with remainder magnitude
    q, r = divmod(n, d)
    # Normalize to positive remainder critera; keep sign via abs
    # We consider "half" when |2*r| == |d|
    two_r = r * 2
    abs_two_r = two_r if two_r >= 0 else -two_r
    abs_d = d if d >= 0 else -d
    if abs_two_r < abs_d:
        return q
    if abs_two_r > abs_d:
        return q + (1 if (n ^ d) >= 0 else -1)  # same sign → +1 else -1
    # exactly half
    if mode == ROUND_HALF_UP:
        return q + (1 if (n ^ d) >= 0 else -1)
    # HALF_EVEN: bump only if q is odd in the direction of sign
    if (q & 1) != 0:
        return q + (1 if (n ^ d) >= 0 else -1)
    return q


def mul_div_down(a: int, b: int, d: int) -> int:
    """floor((a * b) / d) with div-by-zero check."""
    require_divisor(d)
    return (a * b) // d


def mul_div_up(a: int, b: int, d: int) -> int:
    """ceil((a * b) / d) with div-by-zero check."""
    require_divisor(d)
    prod = a * b
    return -((-prod) // d)


def average_floor(a: int, b: int) -> int:
    """Floor average without overflow tricks (Python ints are unbounded)."""
    return (a + b) // 2


# ---------------------------------------------------------------------------
# Saturating U256 arithmetic
# ---------------------------------------------------------------------------


def u256_add_sat(x: int, y: int) -> int:
    """Saturating add in [0, U256_MAX]."""
    require_u256(x, y)
    s = x + y
    return s if s <= U256_MAX else U256_MAX


def u256_sub_floor(x: int, y: int) -> int:
    """Saturating subtract: returns 0 when y > x."""
    require_u256(x, y)
    return x - y if x >= y else 0


def u256_mul_sat(x: int, y: int) -> int:
    """Saturating multiply in [0, U256_MAX]."""
    require_u256(x, y)
    p = x * y
    return p if p <= U256_MAX else U256_MAX


def u256_mul_div_down(x: int, y: int, d: int) -> int:
    """(x*y)//d with guards for domain; no overflow risk in Python, but range-checked I/O."""
    require_u256(x, y)
    require_divisor(d)
    q = (x * y) // d
    if q < 0 or q > U256_MAX:
        _revert(b"MATH:U256_OOB")
    return q


def u256_mul_div_up(x: int, y: int, d: int) -> int:
    """ceil((x*y)/d) with guards."""
    require_u256(x, y)
    require_divisor(d)
    q = mul_div_up(x, y, d)
    if q < 0 or q > U256_MAX:
        _revert(b"MATH:U256_OOB")
    return q


# ---------------------------------------------------------------------------
# Fixed-point helpers (wad/ray & generic scale)
# ---------------------------------------------------------------------------


def fp_mul(x: int, y: int, scale: int) -> int:
    """Return floor((x * y) / scale)."""
    require_nonneg(scale)
    return mul_div_down(x, y, scale if scale != 0 else _revert(b"MATH:DIV_BY_ZERO"))  # type: ignore[return-value]


def fp_div(x: int, y: int, scale: int) -> int:
    """Return floor((x * scale) / y)."""
    require_divisor(y)
    require_nonneg(scale)
    return mul_div_down(x, scale, y)


def wad_mul(x: int, y: int) -> int:
    """Floor (x * y / 1e18)."""
    return mul_div_down(x, y, WAD)


def wad_div(x: int, y: int) -> int:
    """Floor (x * 1e18 / y)."""
    return mul_div_down(x, WAD, y)


def wad_mul_up(x: int, y: int) -> int:
    """Ceil (x * y / 1e18)."""
    return mul_div_up(x, y, WAD)


def wad_div_up(x: int, y: int) -> int:
    """Ceil (x * 1e18 / y)."""
    return mul_div_up(x, WAD, y)


# ---------------------------------------------------------------------------
# Percentages & fees (BPS / PPM)
# ---------------------------------------------------------------------------


def check_bps(bps: int) -> None:
    if bps < 0 or bps > BPS_DEN:
        _revert(b"MATH:BPS_OOB")


def check_ppm(ppm: int) -> None:
    if ppm < 0 or ppm > PPM_DEN:
        _revert(b"MATH:PPM_OOB")


def apply_bps(amount: int, bps: int) -> int:
    """Return floor(amount * bps / 10_000)."""
    require_nonneg(amount)
    check_bps(bps)
    return mul_div_down(amount, bps, BPS_DEN)


def apply_bps_up(amount: int, bps: int) -> int:
    """Return ceil(amount * bps / 10_000)."""
    require_nonneg(amount)
    check_bps(bps)
    return mul_div_up(amount, bps, BPS_DEN)


def apply_ppm(amount: int, ppm: int) -> int:
    """Return floor(amount * ppm / 1_000_000)."""
    require_nonneg(amount)
    check_ppm(ppm)
    return mul_div_down(amount, ppm, PPM_DEN)


def fee_split(amount: int, bps_fee: int) -> Tuple[int, int]:
    """
    Split amount into (fee, remainder) with floor rounding to fee.
    Guaranteed: fee + remainder == amount.
    """
    fee = apply_bps(amount, bps_fee)
    return fee, amount - fee


# ---------------------------------------------------------------------------
# Means, roots, distance, ratios
# ---------------------------------------------------------------------------


def isqrt(n: int) -> int:
    """Integer floor square root (exact & deterministic)."""
    if n < 0:
        _revert(b"MATH:NEGATIVE_SQRT")
    # Using math.isqrt is ok (exact for ints)
    import math  # local import; we only use isqrt

    return math.isqrt(n)


def ratio_bps(numer: int, denom: int) -> int:
    """
    Return floor(numer/denom * 10_000) as BPS (0..10_000).
    """
    if denom <= 0:
        _revert(b"MATH:BAD_DENOM")
    r = mul_div_down(numer, BPS_DEN, denom)
    if r < 0:
        return 0
    if r > BPS_DEN:
        return BPS_DEN
    return r


def ratio_ppm(numer: int, denom: int) -> int:
    """
    Return floor(numer/denom * 1_000_000) as PPM (0..1_000_000).
    """
    if denom <= 0:
        _revert(b"MATH:BAD_DENOM")
    r = mul_div_down(numer, PPM_DEN, denom)
    if r < 0:
        return 0
    if r > PPM_DEN:
        return PPM_DEN
    return r


# ---------------------------------------------------------------------------
# EMA (Exponential Moving Average) in integer PPM
# ---------------------------------------------------------------------------


def ema_ppm(prev: int, sample: int, alpha_ppm: int) -> int:
    """
    Integer EMA update with alpha in PPM (0..1_000_000).

        new = floor(prev*(1 - a) + sample*a)
            = floor((prev*(PPM_DEN - a) + sample*a) / PPM_DEN)

    Properties:
    - Monotone in sample and alpha.
    - For alpha=0 → prev; alpha=PPM_DEN → sample.
    """
    check_ppm(alpha_ppm)
    a = alpha_ppm
    return (prev * (PPM_DEN - a) + sample * a) // PPM_DEN


# ---------------------------------------------------------------------------
# Signed helpers (clamp & saturating add/sub)
# ---------------------------------------------------------------------------


def s256_add_sat(x: int, y: int) -> int:
    """Saturating add in signed 256-bit range."""
    s = x + y
    if s < I256_MIN:
        return I256_MIN
    if s > I256_MAX:
        return I256_MAX
    return s


def s256_sub_sat(x: int, y: int) -> int:
    """Saturating subtract in signed 256-bit range."""
    s = x - y
    if s < I256_MIN:
        return I256_MIN
    if s > I256_MAX:
        return I256_MAX
    return s


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    # envelopes
    "U256_MAX",
    "U128_MAX",
    "I256_MIN",
    "I256_MAX",
    "BPS_DEN",
    "PPM_DEN",
    "WAD",
    "RAY",
    # rounding
    "ROUND_DOWN",
    "ROUND_UP",
    "ROUND_HALF_UP",
    "ROUND_HALF_EVEN",
    "div_round",
    "mul_div_down",
    "mul_div_up",
    "average_floor",
    # guards
    "clamp",
    "require_nonneg",
    "require_u256",
    "require_divisor",
    # u256 ops
    "u256_add_sat",
    "u256_sub_floor",
    "u256_mul_sat",
    "u256_mul_div_down",
    "u256_mul_div_up",
    # fixed-point
    "fp_mul",
    "fp_div",
    "wad_mul",
    "wad_div",
    "wad_mul_up",
    "wad_div_up",
    # percentages
    "check_bps",
    "check_ppm",
    "apply_bps",
    "apply_bps_up",
    "apply_ppm",
    "fee_split",
    # ratios & roots
    "isqrt",
    "ratio_bps",
    "ratio_ppm",
    # ema
    "ema_ppm",
    # signed
    "s256_add_sat",
    "s256_sub_sat",
]
