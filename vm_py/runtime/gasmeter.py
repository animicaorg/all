"""
vm_py.runtime.gasmeter — deterministic gas metering with OOG semantics.

This module provides a minimal, production-ready GasMeter used by the VM
interpreter. It is intentionally simple:

- Gas is charged *before* executing an operation.
- If the meter would exceed its limit, an OOG error is raised.
- A refund pool is tracked but not applied automatically; callers may use
  `finalize(max_refund_ratio=...)` if they want capped refunds.
- Snapshots/checkpoints support speculative execution.

Refunds in the VM are typically minimal (most refund logic lives in the
execution layer). We still provide the hooks for completeness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional
from contextlib import contextmanager

try:
    # Reuse VM error types if available.
    from ..errors import VmError, OOG  # type: ignore
except Exception:  # pragma: no cover
    class VmError(Exception):
        ...
    class OOG(VmError):
        ...


@dataclass(frozen=True)
class GasSnapshot:
    used: int
    refund_pool: int


class GasMeter:
    """
    Deterministic gas meter.

    Typical usage:
        gm = GasMeter(limit=200_000)
        gm.consume(3)        # charge cost
        gm.refund(1)         # optional: record a refund credit
        # ...
        effective = gm.finalize(max_refund_ratio=0.0)  # often zero at VM layer

    Notes:
    - All values are Python ints; negative or non-int inputs raise.
    - `used` is monotonically non-decreasing (refunds don't change it).
    - `remaining` never goes below zero; OOG is raised before that.
    """

    __slots__ = ("_limit", "_used", "_refund_pool")

    def __init__(self, *, limit: int) -> None:
        self._limit = self._require_int_ge(limit, 0, "limit")
        self._used = 0
        self._refund_pool = 0

    # -------------------------- properties -------------------------- #

    @property
    def limit(self) -> int:
        """Configured hard limit."""
        return self._limit

    @property
    def used(self) -> int:
        """Total gas consumed so far (before any refunds)."""
        return self._used

    @property
    def refund_pool(self) -> int:
        """Accumulated refundable amount (not yet applied)."""
        return self._refund_pool

    @property
    def remaining(self) -> int:
        """Gas remaining before hitting the hard limit (>= 0)."""
        return self._limit - self._used

    # --------------------------- actions ---------------------------- #

    def consume(self, amount: int) -> None:
        """Charge `amount` gas; raise OOG if this would exceed the limit."""
        amt = self._require_int_ge(amount, 0, "consume amount")
        new_used = self._used + amt
        if new_used > self._limit:
            # Do not mutate state; fail deterministically.
            raise OOG(
                f"OutOfGas: need {amt} (used {self._used}, limit {self._limit})"
            )
        self._used = new_used

    def refund(self, amount: int) -> None:
        """Accumulate a refundable amount (does not reduce `used` immediately)."""
        amt = self._require_int_ge(amount, 0, "refund amount")
        # Unbounded pool; caller caps via finalize().
        self._refund_pool += amt

    def finalize(self, *, max_refund_ratio: float = 0.0) -> int:
        """
        Compute effective used gas after applying a *capped* refund.

        `max_refund_ratio` is a float in [0.0, 1.0], e.g. 0.5 caps refunds to 50%
        of `used`. Defaults to 0.0 at the VM layer (refunds handled elsewhere).

        Returns the effective_used (>= 0).
        """
        if not isinstance(max_refund_ratio, (int, float)):
            raise VmError("max_refund_ratio must be a number")
        if max_refund_ratio < 0.0 or max_refund_ratio > 1.0:
            raise VmError("max_refund_ratio must be between 0.0 and 1.0")
        cap = int(self._used * float(max_refund_ratio))
        applied = min(self._refund_pool, cap)
        eff = self._used - applied
        if eff < 0:
            eff = 0
        return eff

    # ------------------------ checkpoints --------------------------- #

    def snapshot(self) -> GasSnapshot:
        """Capture a reversible snapshot of the meter."""
        return GasSnapshot(self._used, self._refund_pool)

    def restore(self, snap: GasSnapshot) -> None:
        """Restore the meter to a previous snapshot."""
        if not isinstance(snap, GasSnapshot):
            raise VmError("Invalid gas snapshot")
        self._used = self._require_int_ge(snap.used, 0, "snapshot.used")
        self._refund_pool = self._require_int_ge(snap.refund_pool, 0, "snapshot.refund_pool")

    @contextmanager
    def checkpoint(self):
        """
        Context manager that rolls back to the previous state if an exception
        occurs. On success, changes remain.
        """
        snap = self.snapshot()
        try:
            yield self
        except Exception:
            self.restore(snap)
            raise

    # --------------------------- helpers ---------------------------- #

    @staticmethod
    def _require_int_ge(v: int, lb: int, name: str) -> int:
        if not isinstance(v, int):
            raise VmError(f"{name} must be int, got {type(v).__name__}")
        if v < lb:
            raise VmError(f"{name} must be >= {lb}, got {v}")
        return v

    # --------------------------- repr -------------------------------- #

    def __repr__(self) -> str:  # pragma: no cover
        return f"GasMeter(limit={self._limit}, used={self._used}, refund_pool={self._refund_pool})"


__all__ = ["GasMeter", "GasSnapshot"]
