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

import inspect
import json
import logging
import os
from typing import Any, Dict, Optional, Protocol

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
    """Raised when a debit would make an account balance negative.
    
    Attributes:
        required: Total amount required (value + fee)
        available: Current balance
        shortfall: Difference between required and available
    """
    
    def __init__(
        self,
        message: str = "insufficient balance",
        *,
        required: int | None = None,
        available: int | None = None,
        shortfall: int | None = None,
        data: Dict[str, Any] | None = None,
    ):
        d: Dict[str, Any] = {}
        if data:
            d.update(data)
        if required is not None:
            d.setdefault("required", str(required))
        if available is not None:
            d.setdefault("available", str(available))
        if shortfall is not None:
            d.setdefault("shortfall", str(shortfall))
        super().__init__(message=message, code="INSUFFICIENT_BALANCE", data=d or None)


class NegativeAmount(ExecError):
    """Raised when a negative amount is passed to a credit/debit/transfer."""


# =============================================================================
# Internal helpers
# =============================================================================

# Maximum safe balance value to prevent overflow (2^256 - 1)
MAX_BALANCE = 2**256 - 1
_log = logging.getLogger(__name__)
_DEBUG_BALANCE_EVENTS: list[dict[str, Any]] = []


def _is_debug_balance_enabled() -> bool:
    return os.getenv("ANIMICA_DEBUG_BALANCE", "0") == "1"


def _record_debug_balance_event(event: dict[str, Any]) -> None:
    _DEBUG_BALANCE_EVENTS.append(event)


def get_debug_balance_events(*, tx_hash: str | None = None) -> list[dict[str, Any]]:
    if tx_hash is None:
        return list(_DEBUG_BALANCE_EVENTS)
    return [evt for evt in _DEBUG_BALANCE_EVENTS if evt.get("tx_hash") == tx_hash]


def reset_debug_balance_events() -> None:
    _DEBUG_BALANCE_EVENTS.clear()


def _ensure_non_negative(amount: int) -> None:
    if amount < 0:
        raise NegativeAmount(f"amount must be >= 0, got {amount}")


def _safe_add(a: int, b: int) -> int:
    # Python ints are unbounded; keep a guard for semantic clarity.
    # Also protect against extremely large values that could cause issues
    if a < 0 or b < 0:
        raise ExecError("negative balance component")
    res = a + b
    if res < 0:
        # This can only happen if 'a' is negative; we forbid that for balances.
        raise ExecError("balance underflow")
    if res > MAX_BALANCE:
        raise ExecError("balance overflow - exceeds maximum safe value")
    return res


def _safe_sub(a: int, b: int) -> int:
    res = a - b
    if res < 0:
        raise InsufficientBalance(
            "insufficient balance",
            required=b,
            available=a,
            shortfall=b - a,
        )
    return res


def _mutate_balance(
    state: BalanceAccess,
    address: bytes,
    delta: int,
    *,
    reason: str,
    tx_hash: str | None,
    height: int | None,
    callsite: str | None = None,
) -> int:
    cur = state.get_balance(address)
    if delta >= 0:
        new = _safe_add(cur, delta)
    else:
        new = _safe_sub(cur, -delta)
    state.set_balance(address, new)

    if _is_debug_balance_enabled():
        if callsite is None:
            frame = inspect.stack()[1]
            callsite = f"{frame.filename}:{frame.lineno}"
        event = {
            "tx_hash": tx_hash,
            "height": height,
            "address": address.hex(),
            "delta": int(delta),
            "reason": reason,
            "callsite": callsite,
        }
        _record_debug_balance_event(event)
        _log.info(
            "BAL_MUT %s",
            json.dumps(
                {
                    "tx": tx_hash,
                    "h": height,
                    "addr": address.hex(),
                    "delta": int(delta),
                    "reason": reason,
                    "site": callsite,
                },
                separators=(",", ":"),
            ),
        )
    return new


# =============================================================================
# Public balance operations
# =============================================================================


def credit(
    state: BalanceAccess,
    address: bytes,
    amount: int,
    *,
    reason: str = "CREDIT",
    tx_hash: str | None = None,
    height: int | None = None,
    callsite: str | None = None,
) -> int:
    """
    Increase `address` balance by `amount` and return the new balance.
    """
    _ensure_non_negative(amount)
    if amount == 0:
        return state.get_balance(address)
    return _mutate_balance(
        state,
        address,
        amount,
        reason=reason,
        tx_hash=tx_hash,
        height=height,
        callsite=callsite,
    )


def debit(
    state: BalanceAccess,
    address: bytes,
    amount: int,
    *,
    reason: str = "DEBIT",
    tx_hash: str | None = None,
    height: int | None = None,
    callsite: str | None = None,
) -> int:
    """
    Decrease `address` balance by `amount` and return the new balance.
    Raises InsufficientBalance if the account cannot cover the debit.
    """
    _ensure_non_negative(amount)
    if amount == 0:
        return state.get_balance(address)
    return _mutate_balance(
        state,
        address,
        -amount,
        reason=reason,
        tx_hash=tx_hash,
        height=height,
        callsite=callsite,
    )


def safe_transfer(
    state: BalanceAccess, sender: bytes, recipient: bytes, amount: int
) -> Dict[str, int]:
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
