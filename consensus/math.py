"""
Deterministic numerics for consensus:

- H(u) = -ln(u) for u ∈ (0, 1], returned in micro-nats (µ-nats) as an integer.
- Fixed-point conversions between Decimal and µ-nats.
- Safe helpers: clamp, log, log1p, and exact u-from-hash mapping.

We avoid platform-dependent libm by using `decimal` with a fixed precision and
rounding. All conversions to µ-nats use ROUND_HALF_EVEN for unbiased rounding.

Reference:
  - ψ (Psi) and Θ (ThetaMicro) are µ-nats (1e-6 natural-log units).
  - u is derived from a uniform draw over 256-bit space: u = (n+1)/2^256.
"""

from __future__ import annotations

from decimal import Decimal, getcontext, localcontext, ROUND_HALF_EVEN, InvalidOperation
from dataclasses import dataclass
import math
from typing import Tuple

from .types import MicroNat, Psi, ThetaMicro, GammaMicro

# -------------------------
# Decimal precision & scales
# -------------------------

# µ-nats scale (1e6)
MICRO_SCALE: int = 1_000_000
_MICRO_D = Decimal(MICRO_SCALE)

# We compute ln() with ample precision to make rounding to µ-nats stable.
# 80 digits is plenty (far above what µ-nats needs) and deterministic.
DEC_PRECISION: int = 80
DEC_ROUNDING = ROUND_HALF_EVEN


def _dec_ctx():
    """Return a local decimal context with fixed precision & rounding."""
    ctx = getcontext().copy()
    ctx.prec = DEC_PRECISION
    ctx.rounding = DEC_ROUNDING
    # Guardrails: no traps change (default traps keep InvalidOperation, etc.)
    return ctx


# -------------------------
# Helpers
# -------------------------

def clamp(value: int, lo: int, hi: int) -> int:
    """Clamp integer `value` to [lo, hi]."""
    return hi if value > hi else lo if value < lo else value


def clamp_mn(x: int, lo: int, hi: int) -> int:
    """Clamp a µ-nats integer to [lo, hi]."""
    return clamp(x, lo, hi)


def micronats_from_decimal(d: Decimal) -> int:
    """
    Convert a non-negative Decimal (natural-log units) to µ-nats (int) with unbiased rounding.
    """
    if d < 0:
        raise ValueError("micronats_from_decimal expects non-negative Decimal")
    with localcontext(_dec_ctx()):
        mn = (d * _MICRO_D).to_integral_value(rounding=DEC_ROUNDING)
    return int(mn)


def decimal_from_micronats(mn: int) -> Decimal:
    """
    Convert µ-nats (int) back to Decimal natural-log units.
    """
    if mn < 0:
        raise ValueError("decimal_from_micronats expects non-negative µ-nats")
    with localcontext(_dec_ctx()):
        return Decimal(mn) / _MICRO_D


# -------------------------
# Log functions (Decimal)
# -------------------------

def ln(x: Decimal) -> Decimal:
    """
    Deterministic natural logarithm using Decimal.

    Uses context.ln() when available (CPython provides it); otherwise falls back
    to a Newton method on exp() that converges quadratically.

    Domain: x > 0.
    """
    if x <= 0:
        raise ValueError("ln domain error: x must be > 0")
    with localcontext(_dec_ctx()) as ctx:
        # Prefer the built-in Decimal ln if present.
        try:
            # type: ignore[attr-defined] - ln is available on Context in CPython.
            return ctx.ln(x)  # noqa
        except (AttributeError, InvalidOperation):
            # Fallback: Newton on f(y) = exp(y) - x.
            # Start from y0 = log2(x) * ln(2) approx via scaling.
            # A simple initial guess y0 = 0 works but converges slower.
            y = Decimal(0)
            # Iterate a fixed number of steps deterministically.
            for _ in range(40):
                ey = ctx.exp(y)
                # y_{k+1} = y - (exp(y) - x) / exp(y) = y - 1 + x/exp(y)
                y = y - (ey - x) / ey
            return +y  # unary plus applies context rounding


def log1p(x: Decimal) -> Decimal:
    """
    Deterministic ln(1+x) with good behavior near x=0.
    Uses ln(1+x) directly; callers should ensure 1+x > 0.
    """
    with localcontext(_dec_ctx()):
        one = Decimal(1)
        return ln(one + x)


# -------------------------
# H(u) = -ln(u) in µ-nats
# -------------------------

def H_u_decimal(u: Decimal) -> int:
    """
    Compute H(u) = -ln(u) in µ-nats as an integer, for u ∈ (0, 1].

    Clamps u to [umin, 1] to avoid ln(0). `umin` is 1 / 2^256, matching the
    uniform draw mapping used on-chain.
    """
    with localcontext(_dec_ctx()):
        if u > 1:
            u = Decimal(1)
        # Minimal positive representable u from a 256-bit draw: (1)/2^256
        umin = Decimal(1) / Decimal(1 << 256)
        if u <= 0:
            u = umin
        if u < umin:
            u = umin
        h = -ln(u)  # natural log
        return micronats_from_decimal(h)


def H_from_hash256(hash_bytes: bytes) -> int:
    """
    Map a 32-byte big-endian hash to a uniform u ∈ (0,1], then return H(u) in µ-nats.

    Mapping (exact, bias-free):
      - Interpret n = int(hash, big-endian) in [0, 2^256 - 1].
      - u = (n + 1) / 2^256  ∈ (0, 1].
    """
    if len(hash_bytes) != 32:
        raise ValueError("hash must be exactly 32 bytes")
    n = int.from_bytes(hash_bytes, "big")
    denom = Decimal(1 << 256)
    with localcontext(_dec_ctx()):
        u = (Decimal(n) + 1) / denom
        return H_u_decimal(u)


def H_from_qbits(n: int, bits: int) -> int:
    """
    Generalized mapping from an integer draw to u ∈ (0,1]:
      u = (n + 1) / 2^bits, where n ∈ [0, 2^bits - 1].
    Then returns H(u) in µ-nats.
    """
    if bits <= 0:
        raise ValueError("bits must be positive")
    maxn = (1 << bits) - 1
    if not (0 <= n <= maxn):
        raise ValueError(f"n must be in [0, 2^{bits}-1]")
    with localcontext(_dec_ctx()):
        u = (Decimal(n) + 1) / Decimal(1 << bits)
        return H_u_decimal(u)


# -------------------------
# Typed wrappers & utilities
# -------------------------

def psi_from_hash(hash_bytes: bytes) -> Psi:
    """Convenience: H(u) as Psi from a 32-byte hash."""
    return Psi(H_from_hash256(hash_bytes))


def theta_from_micronats(x: int) -> ThetaMicro:
    """Build a ThetaMicro from raw µ-nats (guards non-negativity)."""
    if x < 0:
        raise ValueError("ThetaMicro cannot be negative")
    return ThetaMicro(x)


def gamma_from_micronats(x: int) -> GammaMicro:
    """Build a GammaMicro (block Γ cap) from raw µ-nats (guards non-negativity)."""
    if x < 0:
        raise ValueError("GammaMicro cannot be negative")
    return GammaMicro(x)


# -------------------------
# Lightweight float helpers (legacy test shim)
# -------------------------

def H(u: float) -> float:
    """
    Return H(u) = -ln(u) in natural units as a float.

    This is a simple, non-decimal wrapper used by lightweight tests. The
    consensus path continues to use the deterministic Decimal-based helpers
    above for micronat calculations.
    """

    if not (0.0 < u <= 1.0) or not math.isfinite(u):
        raise ValueError("u must lie in (0, 1] and be finite")
    return -math.log(u)


def to_munats(x: float) -> int:
    """Convert a float (nats) to µ-nats as an int with round-half-even semantics."""

    if not math.isfinite(x):
        raise ValueError("input must be finite")
    with localcontext(_dec_ctx()):
        return int((Decimal(x) * _MICRO_D).to_integral_value(rounding=DEC_ROUNDING))


def from_munats(x: int) -> float:
    """Convert an integer µ-nats value back to a float in natural units."""

    with localcontext(_dec_ctx()):
        return float(Decimal(x) / _MICRO_D)


def add_micronats(a: int, b: int) -> int:
    """Safe add in µ-nats (saturates at Python int max only in extreme cases)."""
    return a + b


def sub_micronats(a: int, b: int) -> int:
    """Safe subtract in µ-nats (floors at 0)."""
    c = a - b
    return c if c >= 0 else 0


# -------------------------
# Sanity self-test (optional)
# -------------------------

if __name__ == "__main__":  # simple local checks; not run in unit CI
    import os
    # Determinism check: identical input → identical µ-nats
    h = os.urandom(32)
    v1 = H_from_hash256(h)
    v2 = H_from_hash256(h)
    assert v1 == v2, "H(u) must be deterministic"

    # Monotonicity: larger u ⇒ smaller H(u)
    from copy import deepcopy
    with localcontext(_dec_ctx()):
        u_small = (Decimal(int.from_bytes(b"\x00"*31 + b"\x01", "big")) + 1) / Decimal(1 << 256)
        u_big   = (Decimal(int.from_bytes(b"\xff"*32, "big")) + 1) / Decimal(1 << 256)
        assert H_u_decimal(u_small) > H_u_decimal(u_big)
    print("OK: basic invariants hold")
