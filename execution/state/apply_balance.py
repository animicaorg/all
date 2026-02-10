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
_CURRENT_APPLY_CONTEXT: dict[str, Any] | None = None


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


def begin_apply_block(height: int, block_hash: str | None) -> None:
    global _CURRENT_APPLY_CONTEXT
    _CURRENT_APPLY_CONTEXT = {
        "height": int(height),
        "block_hash": block_hash,
        "events": [],
    }


def end_apply_block() -> list[dict[str, Any]]:
    global _CURRENT_APPLY_CONTEXT
    events: list[dict[str, Any]] = []
    if _CURRENT_APPLY_CONTEXT is not None:
        events = list(_CURRENT_APPLY_CONTEXT.get("events") or [])
    _CURRENT_APPLY_CONTEXT = None
    return events


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


def confirmed_balance_mutate(
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

    if callsite is None:
        frame = inspect.stack()[1]
        callsite = f"{frame.filename}:{frame.lineno}#{frame.function}"
    event = {
        "tx_hash": tx_hash,
        "height": height,
        "address": address.hex(),
        "delta": int(delta),
        "reason": reason,
        "callsite": callsite,
    }
    if _CURRENT_APPLY_CONTEXT is not None:
        _CURRENT_APPLY_CONTEXT.setdefault("events", []).append(dict(event))

    if _is_debug_balance_enabled():
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
    return confirmed_balance_mutate(
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
    return confirmed_balance_mutate(
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


def assert_single_tx_balance_deltas(
    *,
    tx_hash: str | None,
    sender: bytes,
    recipient: bytes,
    amount: int,
    total_fee: int,
) -> None:
    """
    Assert that a transaction results in exactly ONE sender debit and ONE recipient credit.
    
    This debug assertion is enabled when ANIMICA_DEBUG_BALANCE=1 and validates:
    1. Sender has exactly ONE negative delta event
    2. Sender total delta equals -(amount + total_fee)
    3. Recipient has exactly ONE positive delta event (if sender != recipient)
    4. Recipient total delta equals +amount
    
    Raises ExecError if the assertion fails, providing detailed diagnostics about
    which callsites are performing the double debit.
    """
    if not _is_debug_balance_enabled() or not tx_hash:
        return
    
    events = get_debug_balance_events(tx_hash=tx_hash)
    sender_neg = [e for e in events if e.get("address") == sender.hex() and int(e.get("delta", 0)) < 0]
    sender_total = sum(int(e.get("delta", 0)) for e in sender_neg)
    recipient_pos = [e for e in events if e.get("address") == recipient.hex() and int(e.get("delta", 0)) > 0]
    recipient_total = sum(int(e.get("delta", 0)) for e in recipient_pos)

    expected_sender = -(int(amount) + int(total_fee)) if sender != recipient else -int(total_fee)
    expected_recipient = int(amount) if sender != recipient else 0
    
    # Check for multiple sender debits (the DOUBLE-DEBIT BUG)
    if len(sender_neg) > 1:
        callsites = [e.get("callsite", "unknown") for e in sender_neg]
        reasons = [e.get("reason", "unknown") for e in sender_neg]
        raise ExecError(
            f"❌ DOUBLE DEBIT DETECTED: Sender has {len(sender_neg)} debits for same tx",
            code="DOUBLE_DEBIT_BUG",
            data={
                "tx_hash": tx_hash,
                "sender": sender.hex(),
                "num_debits": len(sender_neg),
                "debits": [
                    {
                        "delta": e.get("delta"),
                        "reason": e.get("reason"),
                        "site": e.get("callsite"),
                    }
                    for e in sender_neg
                ],
                "callsites": callsites,
                "reasons": reasons,
                "total_debited": sender_total,
                "expected_debit": expected_sender,
                "diagnosis": "Multiple code paths are debiting the sender. Check callsites above.",
            },
        )
    
    # Check sender debit count
    if len(sender_neg) == 0:
        raise ExecError(
            "Sender has NO negative delta events (expected exactly 1)",
            code="SENDER_DEBIT_MISSING",
            data={
                "tx_hash": tx_hash,
                "sender": sender.hex(),
                "expected": 1,
                "actual": 0,
            },
        )
    
    # Check sender debit amount
    if sender_total != expected_sender:
        raise ExecError(
            f"Sender total delta {sender_total} != expected {expected_sender}",
            code="SENDER_DEBIT_AMOUNT_MISMATCH",
            data={
                "tx_hash": tx_hash,
                "sender": sender.hex(),
                "actual": sender_total,
                "expected": expected_sender,
                "amount": amount,
                "total_fee": total_fee,
            },
        )
    
    # Check recipient credit (only if not self-transfer)
    if sender != recipient:
        if recipient_total != expected_recipient:
            raise ExecError(
                f"Recipient total delta {recipient_total} != expected {expected_recipient}",
                code="RECIPIENT_CREDIT_MISMATCH",
                data={
                    "tx_hash": tx_hash,
                    "recipient": recipient.hex(),
                    "actual": recipient_total,
                    "expected": expected_recipient,
                },
            )


def assert_block_apply_deltas(
    *,
    tx_expectations: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    if os.getenv("ANIMICA_ASSERT_BALANCE", "0") != "1":
        return

    for txe in tx_expectations:
        tx_hash = txe.get("tx_hash")
        sender = str(txe.get("sender") or "")
        recipient = str(txe.get("recipient") or "")
        expected_sender_delta = int(txe.get("sender_delta") or 0)
        expected_recipient_delta = int(txe.get("recipient_delta") or 0)

        tx_events = [e for e in events if e.get("tx_hash") == tx_hash]
        sender_events = [e for e in tx_events if e.get("address") == sender and int(e.get("delta", 0)) < 0]
        recipient_events = [e for e in tx_events if e.get("address") == recipient and int(e.get("delta", 0)) > 0]

        sender_total = sum(int(e.get("delta", 0)) for e in sender_events)
        recipient_total = sum(int(e.get("delta", 0)) for e in recipient_events)

        if len(sender_events) != 1 or sender_total != expected_sender_delta:
            raise ExecError(
                "block apply sender debit invariant failed",
                code="BLOCK_DOUBLE_DEBIT",
                data={
                    "tx_hash": tx_hash,
                    "sender": sender,
                    "expected_sender_delta": expected_sender_delta,
                    "actual_sender_delta": sender_total,
                    "sender_events": sender_events,
                    "all_tx_events": tx_events,
                },
            )
        if recipient and sender != recipient and (len(recipient_events) != 1 or recipient_total != expected_recipient_delta):
            raise ExecError(
                "block apply recipient credit invariant failed",
                code="BLOCK_CREDIT_INVARIANT",
                data={
                    "tx_hash": tx_hash,
                    "recipient": recipient,
                    "expected_recipient_delta": expected_recipient_delta,
                    "actual_recipient_delta": recipient_total,
                    "recipient_events": recipient_events,
                    "all_tx_events": tx_events,
                },
            )


__all__ = [
    "BalanceAccess",
    "InsufficientBalance",
    "NegativeAmount",
    "credit",
    "debit",
    "safe_transfer",
    "apply_gas_fees",
    "begin_apply_block",
    "end_apply_block",
    "confirmed_balance_mutate",
    "assert_single_tx_balance_deltas",
    "assert_block_apply_deltas",
    "get_debug_balance_events",
    "reset_debug_balance_events",
]
