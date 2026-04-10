from __future__ import annotations

"""
Pricing: convert job "units" -> base reward.

This module provides deterministic, policy-driven functions that price AI and
Quantum jobs into integer token amounts. It is pure/side-effect free and does
not read environment variables at import time (callers may construct schedules
from config however they like).

Design notes
-----------
- Integer native unit: all amounts are returned as integers in the chain's
  base unit (e.g., "awei" if 1e-9 coin). Do the display conversion elsewhere.
- Deterministic math: use Decimal internally for surge/quality multipliers,
  then clamp and round according to the schedule.
- Policy bounds: per-kind per-unit price, optional min/max per job, and a
  global hard cap guard (defense in depth).
- Extensible: callers can carry additional metadata in **kwargs; unknown keys
  are ignored (stable API surface).

Example
-------
>>> schedule = PricingSchedule(ai_per_unit=120_000, quantum_per_unit=900_000)
>>> price_ai_units(10, schedule)
1200000
>>> price_quantum_units(3, schedule, surge=1.2)
3240000
"""


from dataclasses import dataclass, replace
from decimal import Decimal, getcontext
from typing import Final, Optional

from ..errors import AICFError

# 34 digits is plenty for any realistic token supply math while remaining fast.
getcontext().prec = 34


Amount = int  # Token amount in the chain's smallest unit (integer)


class PricingError(AICFError):
    """Raised when pricing inputs are invalid or exceed policy."""


@dataclass(frozen=True)
class PricingSchedule:
    """
    Per-kind pricing schedule.

    Attributes
    ----------
    ai_per_unit:
        Price of one AI unit (integer, base token unit).
    quantum_per_unit:
        Price of one Quantum unit (integer, base token unit).
    min_reward:
        Optional minimum reward applied per job when units > 0.
    max_reward:
        Optional maximum reward applied per job (after surge/quality, before rounding).
    rounding:
        Rounding mode applied to the final Decimal before int conversion.
        - 'floor'   : truncate toward zero
        - 'ceil'    : round up if there is any fractional part
        - 'nearest' : bankers rounding (0.5 -> even)
    hard_cap:
        Defensive absolute cap; if non-None and computed reward exceeds it, a
        PricingError is raised. Use to prevent misconfiguration windfalls.
    """

    ai_per_unit: Amount
    quantum_per_unit: Amount
    min_reward: Optional[Amount] = None
    max_reward: Optional[Amount] = None
    rounding: Literal["floor", "ceil", "nearest"] = "floor"
    hard_cap: Optional[Amount] = None

    def with_overrides(
        self,
        *,
        ai_per_unit: Optional[Amount] = None,
        quantum_per_unit: Optional[Amount] = None,
        min_reward: Optional[Amount] = None,
        max_reward: Optional[Amount] = None,
        rounding: Optional[Literal["floor", "ceil", "nearest"]] = None,
        hard_cap: Optional[Amount] = None,
    ) -> "PricingSchedule":
        """Create a tweaked copy without mutating the original."""
        return replace(
            self,
            ai_per_unit=self.ai_per_unit if ai_per_unit is None else ai_per_unit,
            quantum_per_unit=(
                self.quantum_per_unit if quantum_per_unit is None else quantum_per_unit
            ),
            min_reward=self.min_reward if min_reward is None else min_reward,
            max_reward=self.max_reward if max_reward is None else max_reward,
            rounding=self.rounding if rounding is None else rounding,
            hard_cap=self.hard_cap if hard_cap is None else hard_cap,
        )


# Sensible conservative defaults for devnet/testing.
DEFAULT_SCHEDULE: Final[PricingSchedule] = PricingSchedule(
    ai_per_unit=100_000,  # 1e5 base units per AI unit
    quantum_per_unit=800_000,  # 8e5 base units per Quantum unit
    min_reward=0,
    max_reward=None,
    rounding="floor",
    hard_cap=None,
)


def _normalize_units(units: int, *, kind: str) -> int:
    if not isinstance(units, int):
        raise PricingError(f"{kind} units must be an integer, got {type(units)!r}")
    if units < 0:
        raise PricingError(f"{kind} units must be non-negative, got {units}")
    return units


def _normalize_factor(x: float | Decimal, *, name: str) -> Decimal:
    try:
        d = Decimal(x)
    except Exception as e:  # pragma: no cover - defensive
        raise PricingError(f"Invalid {name} factor: {x!r}") from e
    if d.is_nan() or d <= 0:
        raise PricingError(f"{name} factor must be > 0, got {x!r}")
    # Clamp to a reasonable range to prevent accidental explosions.
    if d > Decimal("10"):  # 10x surge/quality cap by default
        d = Decimal("10")
    return d


def _round_amount(d: Decimal, mode: Literal["floor", "ceil", "nearest"]) -> Amount:
    if mode == "floor":
        return int(d.to_integral_value(rounding="ROUND_DOWN"))
    if mode == "ceil":
        return int(d.to_integral_value(rounding="ROUND_UP"))
    # nearest-even
    return int(d.to_integral_value(rounding="ROUND_HALF_EVEN"))


def _apply_bounds(raw: Amount, schedule: PricingSchedule, units: int) -> Amount:
    if units == 0:
        return 0
    amt = raw
    if schedule.min_reward is not None and amt < schedule.min_reward:
        amt = schedule.min_reward
    if schedule.max_reward is not None and amt > schedule.max_reward:
        amt = schedule.max_reward
    if schedule.hard_cap is not None and amt > schedule.hard_cap:
        raise PricingError(
            f"Computed reward {raw} exceeds hard cap {schedule.hard_cap}"
        )
    return amt


def price_ai_units(
    units: int,
    schedule: PricingSchedule = DEFAULT_SCHEDULE,
    *,
    surge: float | Decimal = 1.0,
    quality: float | Decimal = 1.0,
) -> Amount:
    """
    Price an AI job.

    Parameters
    ----------
    units : int
        Measured AI units for the job (policy-defined).
    schedule : PricingSchedule
        Pricing parameters.
    surge : float|Decimal
        Optional surge multiplier (e.g., demand-driven). Clamped to (0, 10].
    quality : float|Decimal
        Optional quality factor in (0, 10], e.g., from SLA scoring.

    Returns
    -------
    Amount
        Reward in base token units as an integer.
    """
    u = _normalize_units(units, kind="AI")
    if u == 0:
        return 0
    s = _normalize_factor(surge, name="surge")
    q = _normalize_factor(quality, name="quality")
    base = Decimal(schedule.ai_per_unit) * Decimal(u)
    reward = base * s * q
    rounded = _round_amount(reward, schedule.rounding)
    return _apply_bounds(rounded, schedule, u)


def price_quantum_units(
    units: int,
    schedule: PricingSchedule = DEFAULT_SCHEDULE,
    *,
    surge: float | Decimal = 1.0,
    quality: float | Decimal = 1.0,
) -> Amount:
    """
    Price a Quantum job.

    Parameters
    ----------
    units : int
        Measured Quantum units for the job (policy-defined).
    schedule : PricingSchedule
        Pricing parameters.
    surge : float|Decimal
        Optional surge multiplier (e.g., demand-driven). Clamped to (0, 10].
    quality : float|Decimal
        Optional quality factor in (0, 10], e.g., from SLA scoring.

    Returns
    -------
    Amount
        Reward in base token units as an integer.
    """
    u = _normalize_units(units, kind="Quantum")
    if u == 0:
        return 0
    s = _normalize_factor(surge, name="surge")
    q = _normalize_factor(quality, name="quality")
    base = Decimal(schedule.quantum_per_unit) * Decimal(u)
    reward = base * s * q
    rounded = _round_amount(reward, schedule.rounding)
    return _apply_bounds(rounded, schedule, u)


def price_job_generic(
    *,
    kind: Literal["AI", "Quantum"],
    units: int,
    schedule: PricingSchedule = DEFAULT_SCHEDULE,
    surge: float | Decimal = 1.0,
    quality: float | Decimal = 1.0,
) -> Amount:
    """
    Generic entrypoint when only kind + units are known.

    This is a thin dispatcher around `price_ai_units` / `price_quantum_units`.
    """
    if kind == "AI":
        return price_ai_units(units, schedule, surge=surge, quality=quality)
    if kind == "Quantum":
        return price_quantum_units(units, schedule, surge=surge, quality=quality)
    raise PricingError(f"Unsupported job kind: {kind!r}")


__all__ = [
    "Amount",
    "PricingError",
    "PricingSchedule",
    "DEFAULT_SCHEDULE",
    "price_ai_units",
    "price_quantum_units",
    "price_job_generic",
]
