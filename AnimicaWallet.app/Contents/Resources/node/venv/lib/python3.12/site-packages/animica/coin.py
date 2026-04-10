"""Animica coin units and formatting helpers.

This module centralizes the ANM denomination so all tooling uses the same
9-decimal scaling:

    1 ANM = 1_000_000_000 base units

Use :data:`COIN_UNIT` when converting between human amounts and raw integers,
and :func:`format_amount` when rendering a user-facing string.
"""

from __future__ import annotations

from decimal import Decimal, getcontext

COIN_SYMBOL = "ANM"
COIN_DECIMALS = 9
COIN_UNIT = 10**COIN_DECIMALS  # 1 ANM = 1_000_000_000 units
UNIT_LABEL = "units"

# High precision to safely convert float/str inputs without rounding issues
getcontext().prec = 40


def to_base_units(amount: float | str | Decimal) -> int:
    """Convert a human ANM amount to base units (integer).

    Accepts floats, strings, or Decimal inputs. Strings are recommended to
    avoid floating-point surprises for large or precise values.
    """

    dec_amount = Decimal(str(amount))
    return int(dec_amount * COIN_UNIT)


def from_base_units(raw: int | str) -> Decimal:
    """Convert raw base units back to a Decimal ANM amount."""

    return Decimal(int(raw)) / COIN_UNIT


def format_amount(raw: int) -> str:
    """Format a raw integer amount as a human string with explicit base-unit note."""

    whole = from_base_units(raw)
    return (
        f"{whole:.9f} {COIN_SYMBOL} ({int(raw):,} base units; "
        f"1 {COIN_SYMBOL} = {COIN_UNIT:,} base units)"
    )

