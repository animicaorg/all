from __future__ import annotations

"""
gasmeter — minimal deterministic gas accounting for the browser VM subset.

Design goals
------------
- Tiny and dependency-free; runs under Pyodide/WASM.
- Deterministic integer arithmetic (no floats).
- Clear failure mode: raises OOG when limit is exceeded.
- Ergonomic helpers: `remaining`, `ensure_available`, context manager for scoped costs.

Notes
-----
This meter is intentionally conservative. Refunds (if used) are applied immediately
but never allow the `used` counter to drop below zero. For most browser simulations
we only *debit* gas (no complex refund logic needed), but the API supports both.
"""

from dataclasses import dataclass

# Errors are shared across vm_pkg
from ..errors import OOG, ValidationError


@dataclass
class GasSnapshot:
    """Immutable snapshot of gas state (for debugging/inspection)."""

    limit: int
    used: int
    refunds: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class GasMeter:
    """
    GasMeter(limit)
    ---------------
    - `debit(n)`: Increases `used` by `n` (must be >= 0). Raises OOG if over limit.
    - `refund(n)`: Decreases `used` by up to `n` (must be >= 0). Clamped at 0.
    - `remaining`: Property: max(0, limit - used).
    - `ensure_available(n)`: Fast check raising OOG if `n` can't be afforded.
    - `snapshot()`: Returns a GasSnapshot for logging or diagnostics.
    - Context helper: `with meter.cost(n): ...` will debit `n` on enter.
    """

    __slots__ = ("_limit", "_used", "_refunds")

    def __init__(self, *, limit: int, used: int = 0) -> None:
        if not isinstance(limit, int) or limit < 0:
            raise ValidationError(f"gas limit must be non-negative int, got {limit!r}")
        if not isinstance(used, int) or used < 0:
            raise ValidationError(f"gas used must be non-negative int, got {used!r}")
        if used > limit:
            raise OOG(f"initial gas used {used} exceeds limit {limit}")
        self._limit = int(limit)
        self._used = int(used)
        self._refunds = 0  # cumulative refunds applied

    # ---------------- Properties ----------------

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    @property
    def refunds(self) -> int:
        return self._refunds

    @property
    def remaining(self) -> int:
        # Never negative
        rem = self._limit - self._used
        return rem if rem > 0 else 0

    # ---------------- Core API ----------------

    def debit(self, n: int) -> None:
        """Consume `n` units of gas; raises OOG if this exceeds the limit."""
        if not isinstance(n, int) or n < 0:
            raise ValidationError(f"gas debit must be non-negative int, got {n!r}")
        # Fast path: cheap check before write
        new_used = self._used + n
        if new_used > self._limit:
            # Keep state unchanged and throw
            raise OOG(f"out of gas: used {self._used} + {n} > limit {self._limit}")
        self._used = new_used

    def ensure_available(self, n: int) -> None:
        """Raise OOG immediately if at least `n` gas is not available."""
        if not isinstance(n, int) or n < 0:
            raise ValidationError(f"gas ensure must be non-negative int, got {n!r}")
        if self._used + n > self._limit:
            raise OOG(
                f"insufficient gas for operation: need {n}, remaining {self.remaining}"
            )

    def refund(self, n: int) -> None:
        """Apply a refund up to `n` units; clamps `used` at 0 (never negative)."""
        if not isinstance(n, int) or n < 0:
            raise ValidationError(f"gas refund must be non-negative int, got {n!r}")
        if n == 0:
            return
        prev_used = self._used
        self._used = prev_used - n
        if self._used < 0:
            self._used = 0
        # Track the amount of refund effectively applied
        self._refunds += prev_used - self._used

    # ---------------- Diagnostics ----------------

    def snapshot(self) -> GasSnapshot:
        return GasSnapshot(limit=self._limit, used=self._used, refunds=self._refunds)

    # ---------------- Context Helpers ----------------

    class _CostCtx:
        __slots__ = ("_meter", "_n", "_debited")

        def __init__(self, meter: "GasMeter", n: int) -> None:
            self._meter = meter
            self._n = n
            self._debited = False

        def __enter__(self) -> "GasMeter._CostCtx":
            self._meter.debit(self._n)
            self._debited = True
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            # If code decides to "waive" cost on error, it can manually call refund
            # in the try/except around this context. We do not auto-refund.
            return False  # do not suppress exceptions

    def cost(self, n: int) -> "GasMeter._CostCtx":
        """Context manager that debits `n` gas on enter."""
        return GasMeter._CostCtx(self, n)
