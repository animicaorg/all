# -*- coding: utf-8 -*-
"""
contracts.stdlib.math.safe_uint
===============================

Saturating and checked unsigned-integer helpers for Animica Python contracts.

Goals
-----
- Provide **U256**-oriented arithmetic that never uses Python floats.
- Two styles of safety:
  1) **Checked**: revert on overflow/underflow/div-by-zero.
  2) **Saturating**: clamp to bounds (flooring on subtract).
- Deterministic behavior with explicit, stable error bytes.

This module builds on `contracts.stdlib.math` primitives and exposes a
clear surface for contracts that want "fail-fast" arithmetic.

Conventions
-----------
- "checked" variants revert via `stdlib.abi.revert(b"...")` on errors.
- "sat" (saturating) variants never revert due to range; they clamp.
- All operations are **integer-only**.
- Functions validate argument domains (0..U256_MAX) unless named "unchecked_*".
"""

from __future__ import annotations

from typing import Final, Optional, Tuple

# Local import of base math primitives (kept narrow & explicit)
from . import (  # type: ignore
    U256_MAX,
    U128_MAX,
    require_u256,
    require_divisor,
    u256_add_sat as _u256_add_sat,
    u256_sub_floor as _u256_sub_floor,
    u256_mul_sat as _u256_mul_sat,
    u256_mul_div_down as _u256_mul_div_down,
    u256_mul_div_up as _u256_mul_div_up,
    apply_bps,
    apply_bps_up,
)

try:
    # Resolved lazily inside helpers to play nice with the VM import guard.
    from stdlib import abi as _abi_mod  # type: ignore
except Exception:
    _abi_mod = None  # resolved lazily


# ---------------------------------------------------------------------------
# ABI / Revert plumbing
# ---------------------------------------------------------------------------

def _abi():
    global _abi_mod
    if _abi_mod is None:
        from stdlib import abi as _abi_mod  # type: ignore
    return _abi_mod


def _revert(msg: bytes) -> None:
    _abi().revert(msg)


# Canonical error tags (short, stable)
ERR_OOB: Final[bytes] = b"UINT:OOB"          # input/result outside [0, U256_MAX]
ERR_OVER: Final[bytes] = b"UINT:OVERFLOW"
ERR_UNDER: Final[bytes] = b"UINT:UNDERFLOW"
ERR_DIV0: Final[bytes] = b"UINT:DIV0"
ERR_POW_OVER: Final[bytes] = b"UINT:POW_OVERFLOW"


# ---------------------------------------------------------------------------
# Internal guards
# ---------------------------------------------------------------------------

def _assert_u256(x: int) -> None:
    """Revert if x not in [0, U256_MAX]."""
    if x < 0 or x > U256_MAX:
        _revert(ERR_OOB)


# ---------------------------------------------------------------------------
# Saturating (never revert due to range)
# ---------------------------------------------------------------------------

def u256_add_sat(x: int, y: int) -> int:
    """Saturating add: min(x+y, U256_MAX)."""
    require_u256(x, y)
    return _u256_add_sat(x, y)


def u256_sub_floor(x: int, y: int) -> int:
    """Saturating subtract: returns 0 when y > x."""
    require_u256(x, y)
    return _u256_sub_floor(x, y)


def u256_mul_sat(x: int, y: int) -> int:
    """Saturating multiply: min(x*y, U256_MAX)."""
    require_u256(x, y)
    return _u256_mul_sat(x, y)


def u256_mul_div_down_sat(x: int, y: int, d: int) -> int:
    """
    Floor((x*y)/d) with divisor check.
    Range will always fit U256 for valid inputs; no extra clamp needed.
    """
    require_u256(x, y)
    require_divisor(d)
    return _u256_mul_div_down(x, y, d)


def u256_mul_div_up_sat(x: int, y: int, d: int) -> int:
    """Ceil((x*y)/d) with divisor check. Saturates if theoretical overflow occurs."""
    require_u256(x, y)
    require_divisor(d)
    # _u256_mul_div_up already checks and reverts on OOB; to make this saturating,
    # compute manually and clamp.
    q = -((-(x * y)) // d)  # ceil division for integers
    return q if 0 <= q <= U256_MAX else U256_MAX


# ---------------------------------------------------------------------------
# Checked (fail-fast on errors)
# ---------------------------------------------------------------------------

def u256_add(x: int, y: int) -> int:
    """Checked add: revert on overflow."""
    require_u256(x, y)
    s = x + y
    if s > U256_MAX:
        _revert(ERR_OVER)
    return s


def u256_sub(x: int, y: int) -> int:
    """Checked sub: revert on underflow (y > x)."""
    require_u256(x, y)
    if y > x:
        _revert(ERR_UNDER)
    return x - y


def u256_mul(x: int, y: int) -> int:
    """Checked multiply: revert on overflow."""
    require_u256(x, y)
    p = x * y
    if p > U256_MAX:
        _revert(ERR_OVER)
    return p


def u256_div(x: int, y: int) -> int:
    """Checked divide (floor): revert on div-by-zero."""
    require_u256(x, y)
    if y == 0:
        _revert(ERR_DIV0)
    return x // y


def u256_mul_div_down(x: int, y: int, d: int) -> int:
    """
    Checked floor((x*y)/d): checks inputs and output domain; revert on div-by-zero or OOB.
    Delegates to base helper which enforces range.
    """
    require_u256(x, y)
    return _u256_mul_div_down(x, y, d)


def u256_mul_div_up(x: int, y: int, d: int) -> int:
    """
    Checked ceil((x*y)/d): checks inputs and output domain; revert on div-by-zero or OOB.
    Delegates to base helper which enforces range.
    """
    require_u256(x, y)
    return _u256_mul_div_up(x, y, d)


def u256_pow(base: int, exp: int) -> int:
    """
    Checked exponentiation by squaring in U256.
    Revert if the result would exceed U256_MAX at any step.

    Gas note: O(log exp) multiplies.
    """
    require_u256(base)
    if exp < 0:
        _revert(ERR_OOB)
    # Fast paths
    if exp == 0:
        return 1
    if base == 0:
        return 0

    result = 1
    b = base
    e = exp
    while e > 0:
        if (e & 1) == 1:
            # result *= b (checked)
            tmp = result * b
            if tmp > U256_MAX:
                _revert(ERR_POW_OVER)
            result = tmp
        e >>= 1
        if e:
            # b *= b (checked, unless last iteration consumed)
            tmp2 = b * b
            if tmp2 > U256_MAX:
                _revert(ERR_POW_OVER)
            b = tmp2
    return result


# ---------------------------------------------------------------------------
# Fee helpers (checked)
# ---------------------------------------------------------------------------

def u256_add_fee_bps(amount: int, bps_fee: int) -> Tuple[int, int]:
    """
    Compute (total, fee) where:
      fee   = floor(amount * bps / 10_000)
      total = amount + fee
    Reverts on overflow.
    """
    require_u256(amount)
    fee = apply_bps(amount, bps_fee)  # applies domain checks internally
    total = amount + fee
    if total > U256_MAX:
        _revert(ERR_OVER)
    return total, fee


def u256_add_fee_bps_up(amount: int, bps_fee: int) -> Tuple[int, int]:
    """
    Like u256_add_fee_bps but CEIL the fee computation.
    """
    require_u256(amount)
    fee = apply_bps_up(amount, bps_fee)
    total = amount + fee
    if total > U256_MAX:
        _revert(ERR_OVER)
    return total, fee


# ---------------------------------------------------------------------------
# "try_*" convenience (no revert; return Optional[int])
# ---------------------------------------------------------------------------

def try_add_u256(x: int, y: int) -> Optional[int]:
    """Return x+y or None on overflow/OOB."""
    if x < 0 or y < 0 or x > U256_MAX or y > U256_MAX:
        return None
    s = x + y
    return s if s <= U256_MAX else None


def try_sub_u256(x: int, y: int) -> Optional[int]:
    """Return x-y or None on underflow/OOB."""
    if x < 0 or y < 0 or x > U256_MAX or y > U256_MAX:
        return None
    return x - y if x >= y else None


def try_mul_u256(x: int, y: int) -> Optional[int]:
    """Return x*y or None on overflow/OOB."""
    if x < 0 or y < 0 or x > U256_MAX or y > U256_MAX:
        return None
    p = x * y
    return p if p <= U256_MAX else None


def try_div_u256(x: int, y: int) -> Optional[int]:
    """Return x//y or None on div-by-zero/OOB."""
    if x < 0 or y < 0 or x > U256_MAX or y > U256_MAX or y == 0:
        return None
    return x // y


# ---------------------------------------------------------------------------
# U128 helpers (occasionally useful for tighter caps)
# ---------------------------------------------------------------------------

def u128_add(x: int, y: int) -> int:
    """Checked add in U128, revert on overflow/underflow."""
    if x < 0 or y < 0 or x > U128_MAX or y > U128_MAX:
        _revert(ERR_OOB)
    s = x + y
    if s > U128_MAX:
        _revert(ERR_OVER)
    return s


def u128_sub(x: int, y: int) -> int:
    if x < 0 or y < 0 or x > U128_MAX or y > U128_MAX:
        _revert(ERR_OOB)
    if y > x:
        _revert(ERR_UNDER)
    return x - y


def u128_mul(x: int, y: int) -> int:
    if x < 0 or y < 0 or x > U128_MAX or y > U128_MAX:
        _revert(ERR_OOB)
    p = x * y
    if p > U128_MAX:
        _revert(ERR_OVER)
    return p


def u128_div(x: int, y: int) -> int:
    if x < 0 or y < 0 or x > U128_MAX or y > U128_MAX:
        _revert(ERR_OOB)
    if y == 0:
        _revert(ERR_DIV0)
    return x // y


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    # errors
    "ERR_OOB", "ERR_OVER", "ERR_UNDER", "ERR_DIV0", "ERR_POW_OVER",
    # saturating U256
    "u256_add_sat", "u256_sub_floor", "u256_mul_sat",
    "u256_mul_div_down_sat", "u256_mul_div_up_sat",
    # checked U256
    "u256_add", "u256_sub", "u256_mul", "u256_div",
    "u256_mul_div_down", "u256_mul_div_up", "u256_pow",
    # fees
    "u256_add_fee_bps", "u256_add_fee_bps_up",
    # try_* (optional)
    "try_add_u256", "try_sub_u256", "try_mul_u256", "try_div_u256",
    # U128 helpers
    "u128_add", "u128_sub", "u128_mul", "u128_div",
]
