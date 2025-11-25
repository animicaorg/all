"""
execution.gas.meter — GasMeter (debit/refund), OOG semantics.

The GasMeter tracks a transaction's gas budget during execution. It supports:
- deterministic debits that raise OOG when insufficient gas remains,
- explicit refunds (tracked separately), and
- snapshots/restore to support scoped execution (e.g., internal calls).

Final refund capping rules (e.g., at most 1/2 of gas used) are *not* enforced
here; use `finalize(refund_cap_ratio=...)` to compute a capped refund. A
separate `execution.gas.refund` module may supply network-specific policy.
"""

from __future__ import annotations

from typing import NamedTuple, Optional

from execution.errors import OOG
from execution.types.gas import (
    Gas,
    U256_MAX,
    is_u256,
    saturating_add,
)

class GasSnapshot(NamedTuple):
    """Lightweight snapshot for scoped execution blocks."""
    used: int
    refunded: int


class GasMeter:
    """
    Deterministic gas meter.

    Parameters
    ----------
    limit : int | Gas
        Total gas made available to the execution. Must be a u256.

    Notes
    -----
    - All arithmetic is u256-capped. Attempts to debit past the remaining
      budget raise `OOG`.
    - Refunds never increase the remaining budget during execution; they are
      realized only at `finalize(...)`.
    """

    __slots__ = ("_limit", "_used", "_refunded")

    def __init__(self, limit: int | Gas) -> None:
        lim = int(limit)
        if lim < 0 or not is_u256(lim):
            raise ValueError("gas limit must be a non-negative u256")
        self._limit: int = lim
        self._used: int = 0
        self._refunded: int = 0

    # --------------------------- properties ---------------------------------

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        return self._used

    @property
    def refunded(self) -> int:
        return self._refunded

    @property
    def remaining(self) -> int:
        # Remaining cannot be negative; clamp at zero (Python ints are unbounded).
        rem = self._limit - self._used
        return rem if rem > 0 else 0

    @property
    def exhausted(self) -> bool:
        return self.remaining == 0

    # --------------------------- operations ---------------------------------

    def require(self, amount: int, *, reason: Optional[str] = None) -> None:
        """
        Ensure at least `amount` gas remains; raise OOG if not.

        This does *not* modify the meter; use `debit` to actually consume gas.
        """
        amt = int(amount)
        if amt < 0:
            raise ValueError("gas amount must be non-negative")
        if amt > self.remaining:
            msg = "out of gas"
            if reason:
                msg = f"{msg}: {reason}"
            raise OOG(msg)

    def debit(self, amount: int, *, reason: Optional[str] = None) -> None:
        """
        Consume `amount` gas, raising OOG if insufficient remains.
        """
        amt = int(amount)
        if amt < 0:
            raise ValueError("gas amount must be non-negative")
        if amt > self.remaining:
            msg = "out of gas"
            if reason:
                msg = f"{msg}: {reason}"
            raise OOG(msg)
        # Safe u256-capped add (should not overflow since remaining >= amt).
        self._used = saturating_add(self._used, amt, cap=U256_MAX)

    def try_debit(self, amount: int) -> bool:
        """
        Best-effort debit that returns False (no mutation) if insufficient gas.
        """
        amt = int(amount)
        if amt < 0:
            return False
        if amt > self.remaining:
            return False
        self._used = saturating_add(self._used, amt, cap=U256_MAX)
        return True

    def refund(self, amount: int, *, reason: Optional[str] = None) -> None:
        """
        Record a refund amount (u256-capped). Refunds are applied at finalize.

        Refunds do not increase `remaining` during execution; they only reduce
        the *effective* gas charged at settlement.
        """
        amt = int(amount)
        if amt < 0:
            raise ValueError("refund amount must be non-negative")
        self._refunded = saturating_add(self._refunded, amt, cap=U256_MAX)

    # --------------------------- snapshots ----------------------------------

    def snapshot(self) -> GasSnapshot:
        """
        Take a cheap snapshot (used, refunded) for scoped execution.
        """
        return GasSnapshot(self._used, self._refunded)

    def restore(self, snap: GasSnapshot) -> None:
        """
        Restore to a previous snapshot (used, refunded).
        """
        if snap.used < 0 or snap.refunded < 0:
            raise ValueError("snapshot values must be non-negative")
        if snap.used > self._limit:
            raise ValueError("snapshot 'used' exceeds limit")
        self._used = int(snap.used)
        self._refunded = int(snap.refunded)

    # --------------------------- finalize -----------------------------------

    def finalize(self, *, refund_cap_ratio: float = 0.5) -> tuple[int, int, int]:
        """
        Compute the final (capped) refund and charged amount.

        Parameters
        ----------
        refund_cap_ratio : float
            Maximum fraction of used gas that may be refunded (e.g., 0.5).
            The effective refund is:
                min(self.refunded, floor(self.used * refund_cap_ratio))

        Returns
        -------
        (used, refund_applied, charged)
            - used: total gas consumed (pre-refund)
            - refund_applied: refund after cap
            - charged: used - refund_applied
        """
        if refund_cap_ratio < 0.0:
            refund_cap_ratio = 0.0
        if refund_cap_ratio > 1.0:
            refund_cap_ratio = 1.0

        cap_by_used = int(self._used * refund_cap_ratio)
        # Cap refund to both the policy cap and u256 range.
        refund_applied = min(self._refunded, cap_by_used, U256_MAX)
        charged = self._used - refund_applied
        if charged < 0:
            charged = 0
        return (self._used, refund_applied, charged)

    # --------------------------- repr ---------------------------------------

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"GasMeter(limit={self._limit}, used={self._used}, "
            f"refunded={self._refunded}, remaining={self.remaining})"
        )


__all__ = ["GasMeter", "GasSnapshot"]
