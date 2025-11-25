"""
execution.state.apply_balance — safe balance ops and fee accounting.

This module provides:
- debit(...) / credit(...): overflow-safe balance updates with checks.
- safe_transfer(...): from→to value transfer with sufficient-funds guard.
- apply_gas_fees(...): debit sender for gas and credit coinbase/treasury.

It assumes the execution state exposes a minimal balance API:

    class State(Protocol):
        def get_balance(self, address: bytes) -> int: ...
        def set_balance(self, address: bytes, value: int) -> None: ...

If your state has a different shape, provide a thin adapter with these two
methods. All amounts are integers in the smallest unit (e.g. wei-like).
"""

from __future__ import annotations

from typing import Protocol, Optional, Dict, Any

from ..errors import ExecError


# =============================================================================
# Balance access protocol
# =============================================================================

class BalanceAccess(Protocol):
    def get_balance(self, address: bytes) -> int: ...
    def set_balance(self, address: bytes, value: int) -> None: ...


# =============================================================================
# Errors
# =============================================================================

class InsufficientBalance(ExecError):
    """Raised when a debit would make an account balance negative."""


class NegativeAmount(ExecError):
    """Raised when a negative amount is passed to a credit/debit/transfer."""


# =============================================================================
# Internal helpers
# =============================================================================

def _ensure_non_negative(amount: int) -> None:
    if amount < 0:
        raise NegativeAmount(f"amount must be >= 0, got {amount}")


def _safe_add(a: int, b: int) -> int:
    # Python ints are unbounded; keep a guard for semantic clarity.
    res = a + b
    if res < 0:
        # This can only happen if 'a' is negative; we forbid that for balances.
        raise ExecError("balance underflow")
    return res


def _safe_sub(a: int, b: int) -> int:
    res = a - b
    if res < 0:
        raise InsufficientBalance("insufficient balance")
    return res


# =============================================================================
# Public balance operations
# =============================================================================

def credit(state: BalanceAccess, address: bytes, amount: int) -> int:
    """
    Increase `address` balance by `amount` and return the new balance.
    """
    _ensure_non_negative(amount)
    if amount == 0:
        return state.get_balance(address)
    cur = state.get_balance(address)
    new = _safe_add(cur, amount)
    state.set_balance(address, new)
    return new


def debit(state: BalanceAccess, address: bytes, amount: int) -> int:
    """
    Decrease `address` balance by `amount` and return the new balance.
    Raises InsufficientBalance if the account cannot cover the debit.
    """
    _ensure_non_negative(amount)
    if amount == 0:
        return state.get_balance(address)
    cur = state.get_balance(address)
    new = _safe_sub(cur, amount)
    state.set_balance(address, new)
    return new


def safe_transfer(state: BalanceAccess, sender: bytes, recipient: bytes, amount: int) -> Dict[str, int]:
    """
    Transfer `amount` from `sender` to `recipient` with checks.

    No-op if sender == recipient or amount == 0 (after validation).
    Returns a dict with {"debited": amount, "credited": amount}.
    """
    _ensure_non_negative(amount)
    if amount == 0 or sender == recipient:
        return {"debited": 0, "credited": 0}
    debit(state, sender, amount)
    credit(state, recipient, amount)
    return {"debited": amount, "credited": amount}


# =============================================================================
# Gas fees
# =============================================================================

def apply_gas_fees(
    state: BalanceAccess,
    *,
    sender: bytes,
    gas_used: int,
    base_price: int,
    tip_price: int,
    coinbase: bytes,
    treasury: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Debit gas from `sender` and credit fee destinations.

    Semantics
    ---------
    - total_fee   = gas_used * (base_price + tip_price)
    - base_fee    = gas_used * base_price        -> credited to `treasury` if provided, otherwise burned (no credit)
    - tip_fee     = gas_used * tip_price         -> credited to `coinbase`

    Notes
    -----
    - If `treasury` is None, base_fee is simply debited from the sender
      (representing a burn from the state layer’s perspective).
    - This call performs a single sufficiency check up-front by debiting the
      *total* and then crediting destinations, so the sender must have enough
      to cover both parts. This ordering prevents partial credits on failure.

    Returns
    -------
    {
      "total_debited": int,
      "base_fee": int,
      "tip_fee": int,
      "credited_coinbase": int,
      "credited_treasury": int,
      "burned": int,
    }
    """
    # Validate
    for n in (gas_used, base_price, tip_price):
        _ensure_non_negative(n)

    base_fee = gas_used * base_price
    tip_fee = gas_used * tip_price
    total = base_fee + tip_fee

    # Debit once to ensure sufficiency
    debit(state, sender, total)

    credited_coinbase = 0
    credited_treasury = 0
    burned = 0

    if tip_fee:
        credited_coinbase = credit(state, coinbase, tip_fee)

    if base_fee:
        if treasury is None:
            burned = base_fee  # accounted as burned; no credit
        else:
            credited_treasury = credit(state, treasury, base_fee)

    return {
        "total_debited": total,
        "base_fee": base_fee,
        "tip_fee": tip_fee,
        "credited_coinbase": tip_fee,
        "credited_treasury": base_fee if treasury is not None else 0,
        "burned": burned,
    }


__all__ = [
    "BalanceAccess",
    "InsufficientBalance",
    "NegativeAmount",
    "credit",
    "debit",
    "safe_transfer",
    "apply_gas_fees",
]
