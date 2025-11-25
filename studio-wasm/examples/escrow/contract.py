"""
Escrow — simple, deterministic escrow example for the Animica Python VM.

Public functions:
  - configure(payer: bytes, payee: bytes) -> None
  - deposit(amount: int) -> int
  - release() -> int
  - refund() -> int
  - balance() -> int
  - get_payer() -> bytes
  - get_payee() -> bytes

Notes:
  - This demo keeps value accounting *inside contract storage* for determinism in the browser
    simulator. It does not move any external currency in this example.
  - Emits deterministic events on key actions.
"""

from stdlib.storage import (
    get_int,
    set_int,
    get_bytes,
    set_bytes,
)
from stdlib.events import emit
from stdlib.abi import require

# Storage keys
K_PAYER = b"escrow/payer"
K_PAYEE = b"escrow/payee"
K_BAL   = b"escrow/balance"

# Limits for safe arithmetic / sizes in examples
MAX_AMOUNT = 2**53 - 1           # JS-safe integer for demo UX
MAX_ADDR_LEN = 96                # bytes


def _addr_ok(a: bytes) -> bool:
    return isinstance(a, (bytes, bytearray)) and 0 < len(a) <= MAX_ADDR_LEN


def _get_payer() -> bytes:
    return get_bytes(K_PAYER, default=b"")


def _get_payee() -> bytes:
    return get_bytes(K_PAYEE, default=b"")


def _get_balance() -> int:
    bal = get_int(K_BAL, default=0)
    require(0 <= bal <= MAX_AMOUNT, "corrupt balance")
    return bal


def _set_balance(v: int) -> None:
    require(isinstance(v, int), "amount must be int")
    require(0 <= v <= MAX_AMOUNT, "amount out of range")
    set_int(K_BAL, v)


def configure(payer: bytes, payee: bytes) -> None:
    """
    One-time configuration of parties. Fails if already configured.
    """
    require(_addr_ok(payer), "bad payer")
    require(_addr_ok(payee), "bad payee")
    require(_get_payer() == b"" and _get_payee() == b"", "already configured")

    set_bytes(K_PAYER, bytes(payer))
    set_bytes(K_PAYEE, bytes(payee))
    emit(b"Configured", {"payer": payer.hex(), "payee": payee.hex()})


def deposit(amount: int) -> int:
    """
    Increase escrowed balance by `amount` (must be > 0).
    Returns the new balance.
    """
    require(_get_payer() != b"" and _get_payee() != b"", "not configured")
    require(isinstance(amount, int), "amount must be int")
    require(amount > 0, "amount must be > 0")
    cur = _get_balance()
    new = cur + amount
    require(new >= cur, "overflow")
    _set_balance(new)
    emit(b"Deposit", {"amount": amount, "balance": new})
    return new


def release() -> int:
    """
    Release the entire escrowed balance to the payee (accounting-only for the demo).
    Returns the amount released.
    """
    require(_get_payer() != b"" and _get_payee() != b"", "not configured")
    bal = _get_balance()
    require(bal > 0, "nothing to release")
    _set_balance(0)
    emit(b"Released", {"amount": bal, "to": _get_payee().hex()})
    return bal


def refund() -> int:
    """
    Refund the entire escrowed balance to the payer (accounting-only for the demo).
    Returns the amount refunded.
    """
    require(_get_payer() != b"" and _get_payee() != b"", "not configured")
    bal = _get_balance()
    require(bal > 0, "nothing to refund")
    _set_balance(0)
    emit(b"Refunded", {"amount": bal, "to": _get_payer().hex()})
    return bal


def balance() -> int:
    """Return current escrowed balance."""
    return _get_balance()


def get_payer() -> bytes:
    """Return payer bytes (as configured)."""
    return _get_payer()


def get_payee() -> bytes:
    """Return payee bytes (as configured)."""
    return _get_payee()
