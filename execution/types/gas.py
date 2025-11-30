"""
execution.types.gas — Gas, GasPrice, and arithmetic helpers.

Animica treats gas *quantities* and *prices* as non-negative integers. Python's
ints are unbounded, so we implement explicit caps and "saturating" math where
appropriate to avoid accidental wraparounds or silent negativity.

Exports
-------
* Types: `Gas`, `GasPrice`
* Constants: `U256_MAX`, `GAS_MAX`, `GAS_PRICE_MAX`
* Constructors: `to_gas()`, `to_gas_price()`
* Arithmetic:
    - `safe_add(a, b, cap=...)`           → raises OverflowError on cap breach
    - `safe_sub(a, b)`                    → raises ValueError if result < 0
    - `saturating_add(a, b, cap=...)`     → clamps at `cap`
    - `saturating_sub(a, b)`              → clamps at 0
    - `mul_price_gas(price, gas, *, saturating=True, cap=...)`
* Utils: `clamp(n, lo, hi)`, `is_u256(n)`

Notes
-----
* Default arithmetic cap is `U256_MAX` to align with common 256-bit ledger
  encodings. You may choose a smaller application-level cap in callers.
"""

from __future__ import annotations

from typing import NewType

# ------------------------------- constants -----------------------------------

U256_MAX: int = (1 << 256) - 1
"""Maximum 256-bit unsigned integer."""

GAS_MAX: int = U256_MAX
GAS_PRICE_MAX: int = U256_MAX


# --------------------------------- types -------------------------------------

Gas = NewType("Gas", int)
GasPrice = NewType("GasPrice", int)


# --------------------------------- utils -------------------------------------


def is_u256(n: int) -> bool:
    """Return True iff 0 <= n <= U256_MAX."""
    return isinstance(n, int) and 0 <= n <= U256_MAX


def clamp(n: int, lo: int, hi: int) -> int:
    """Clamp integer `n` to the inclusive range [lo, hi]."""
    if lo > hi:
        raise ValueError("clamp: lo must be <= hi")
    return hi if n > hi else lo if n < lo else n


def _ensure_nonneg_int(n: int) -> int:
    if not isinstance(n, int):
        raise TypeError(f"expected int, got {type(n).__name__}")
    if n < 0:
        raise ValueError("value must be non-negative")
    return n


# ----------------------------- constructors ----------------------------------


def to_gas(n: int) -> Gas:
    """Validate and coerce an int into `Gas` (0 <= n <= GAS_MAX)."""
    n = _ensure_nonneg_int(n)
    if n > GAS_MAX:
        raise OverflowError(f"gas exceeds GAS_MAX ({GAS_MAX})")
    return Gas(n)


def to_gas_price(n: int) -> GasPrice:
    """Validate and coerce an int into `GasPrice` (0 <= n <= GAS_PRICE_MAX)."""
    n = _ensure_nonneg_int(n)
    if n > GAS_PRICE_MAX:
        raise OverflowError(f"gas price exceeds GAS_PRICE_MAX ({GAS_PRICE_MAX})")
    return GasPrice(n)


# ------------------------------ arithmetic -----------------------------------


def safe_add(a: int, b: int, *, cap: int = U256_MAX) -> int:
    """
    Checked addition. Raises OverflowError if result exceeds `cap`.
    """
    _ensure_nonneg_int(a)
    _ensure_nonneg_int(b)
    s = a + b
    if s > cap:
        raise OverflowError(f"addition overflow: {a} + {b} > cap {cap}")
    return s


def safe_sub(a: int, b: int) -> int:
    """
    Checked subtraction. Raises ValueError if result would be negative.
    """
    _ensure_nonneg_int(a)
    _ensure_nonneg_int(b)
    if b > a:
        raise ValueError(f"subtraction underflow: {a} - {b} < 0")
    return a - b


def saturating_add(a: int, b: int, *, cap: int = U256_MAX) -> int:
    """
    Saturating addition. Returns min(a + b, cap).
    """
    _ensure_nonneg_int(a)
    _ensure_nonneg_int(b)
    s = a + b
    return cap if s > cap else s


def saturating_sub(a: int, b: int) -> int:
    """
    Saturating subtraction. Returns max(a - b, 0).
    """
    _ensure_nonneg_int(a)
    _ensure_nonneg_int(b)
    return a - b if a >= b else 0


def mul_price_gas(
    price: GasPrice | int,
    gas: Gas | int,
    *,
    saturating: bool = True,
    cap: int = U256_MAX,
) -> int:
    """
    Multiply `price * gas` to compute a fee amount.

    By default uses saturating multiplication at `cap`. If `saturating=False`,
    raises OverflowError on cap breach.
    """
    p = to_gas_price(int(price))
    g = to_gas(int(gas))
    product = int(p) * int(g)
    if product > cap:
        if saturating:
            return cap
        raise OverflowError(f"multiplication overflow: {int(p)} * {int(g)} > cap {cap}")
    return product


__all__ = [
    "Gas",
    "GasPrice",
    "U256_MAX",
    "GAS_MAX",
    "GAS_PRICE_MAX",
    "to_gas",
    "to_gas_price",
    "safe_add",
    "safe_sub",
    "saturating_add",
    "saturating_sub",
    "mul_price_gas",
    "clamp",
    "is_u256",
]
