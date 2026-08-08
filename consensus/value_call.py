"""FORK_VALUE_CALL (9.5.0) — consensus rules for a CALL that carries ANM.

`TxKind.CALL` had no amount field, so a contract could not be paid by the invocation
that used it. Animica Pay could not do an atomic 98/2 merchant split because of it, and
a valueless CALL can grief a payment.

This module holds the two rules that must be identical on every node, kept apart from
both the type (`core.types.tx.TxCall`, which only knows how to encode the field) and the
executor (which only knows how to move the coins):

  1. `check_call_value` — below the activation height a non-zero amount is INVALID.
     Pre-fork that state was unreachable because the field could not be expressed, so
     enforcing it explicitly is what keeps history byte-identical while making an
     old node's rejection deterministic rather than a parse failure.
  2. `debit_credit_for_call` — from the height, the amount moves caller -> callee
     BEFORE execution, and a revert returns it.

Everything here is a pure function of (amount, height, chain_id, balances). No clocks,
no params files, no node-local state — the properties that let two honest nodes agree.
"""

from __future__ import annotations

from typing import Optional

from core.network_params import FORK_VALUE_CALL, is_fork_active


class ValueCallError(ValueError):
    """A CALL's attached value is not permissible at this height."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def value_calls_active(height: int, *, chain_id: int = 1) -> bool:
    """True when a CALL may carry a non-zero amount at `height`."""
    return bool(is_fork_active(FORK_VALUE_CALL, int(height), chain_id=int(chain_id)))


def check_call_value(amount: int, height: int, *, chain_id: int = 1) -> None:
    """Raise unless `amount` is permissible on a CALL at `height`.

    A zero amount is always fine — that is every CALL that has ever existed. A
    non-zero amount is fine only from the fork height, and is rejected with a stable
    code below it so every node refuses the same transaction for the same reason.
    """
    amt = int(amount or 0)
    if amt < 0:
        raise ValueCallError("CALL amount must be >= 0", code="NEGATIVE_CALL_VALUE")
    if amt == 0:
        return
    if not value_calls_active(height, chain_id=chain_id):
        raise ValueCallError(
            "CALL may not carry value before FORK_VALUE_CALL",
            code="VALUE_CALL_NOT_ACTIVE",
        )


def debit_credit_for_call(
    *,
    amount: int,
    sender_balance: int,
    height: int,
    chain_id: int = 1,
) -> int:
    """Validate the transfer of `amount` out of `sender_balance` for a value CALL.

    Returns the amount to move (0 when the fork is inactive or the amount is zero).
    Raises when the caller cannot cover it, BEFORE execution — an under-funded value
    call must fail cleanly rather than execute and leave the callee short.

    The caller is responsible for applying the movement and for returning it on
    revert; keeping the arithmetic here means the *rule* has one reviewed home.
    """
    check_call_value(amount, height, chain_id=chain_id)
    amt = int(amount or 0)
    if amt == 0:
        return 0
    if int(sender_balance) < amt:
        raise ValueCallError(
            f"insufficient balance for CALL value: have {int(sender_balance)}, need {amt}",
            code="INSUFFICIENT_CALL_VALUE",
        )
    return amt


def call_amount_of(tx_payload: object) -> int:
    """The amount attached to a CALL payload, tolerating payloads that predate it.

    Old objects, dicts decoded from old blocks, and third-party payload shapes may have
    no `amount` at all; every one of those means zero.
    """
    amt: Optional[object]
    if isinstance(tx_payload, dict):
        amt = tx_payload.get("amount", 0)
    else:
        amt = getattr(tx_payload, "amount", 0)
    try:
        return int(amt or 0)
    except (TypeError, ValueError):
        return 0
