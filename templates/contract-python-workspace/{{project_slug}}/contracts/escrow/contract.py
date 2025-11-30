# -*- coding: utf-8 -*-
"""
Deterministic Escrow (template)
-------------------------------

Participants
* payer:   funds the escrow and can request refund before release
* payee:   receives the funds on release
* arbiter: authorized to release or refund

Lifecycle (happy-path)
1) init(payer, payee, arbiter, amount)
2) fund(amount) by payer (records the intended lock; actual coin funding is external)
3) release() by arbiter → transfers locked to payee

Alternative path
* refund() by arbiter or by payer → transfers locked back to payer
* cancel() before any funding to abandon the agreement (no transfers)

Notes on funds:
- This contract tracks the "locked" amount in storage and performs transfers using
  the stdlib.treasury API on release/refund. The environment must ensure the
  contract holds at least that much balance when release/refund is invoked.
- A typical flow is: payer sends value to the contract address at or before
  calling fund(amount). The contract checks its own balance at release/refund.

Storage layout (keys are short, domain-separated)
- b":i" → init flag (b"\x01" set)
- b":p" → payer address (32 bytes)
- b":y" → payee address (32 bytes)
- b":a" → arbiter address (32 bytes)
- b":A" → agreed total amount (u256-encoded)
- b":L" → currently locked amount (u256-encoded)
- b":s" → status bytes: {b"OPEN", b"RELEASED", b"REFUNDED", b"CANCELLED"}

Events (canonical, bytes names)
- b"Funded"   { payer, amount }
- b"Released" { payee, amount }
- b"Refunded" { payer, amount }
- b"Canceled" {}
"""
from __future__ import annotations

# Contract-safe standard library (deterministic subset)
from stdlib import (abi, events, storage,  # type: ignore[import-not-found]
                    treasury)

# ---- storage keys ------------------------------------------------------------

K_INIT = b":i"
K_PAYER = b":p"
K_PAYEE = b":y"
K_ARBITER = b":a"
K_AGREED = b":A"
K_LOCKED = b":L"
K_STATUS = b":s"

STATUS_OPEN = b"OPEN"
STATUS_RELEASED = b"RELEASED"
STATUS_REFUNDED = b"REFUNDED"
STATUS_CANCELLED = b"CANCELLED"

# ---- encoding helpers (u256 as big-endian 32 bytes) --------------------------


def _u256(n: int) -> bytes:
    abi.require(n >= 0, b"neg")
    abi.require(n < (1 << 256), b"u256overflow")
    return n.to_bytes(32, "big")


def _from_u256(b: bytes) -> int:
    abi.require(len(b) == 32, b"badlen")
    return int.from_bytes(b, "big")


def _write_addr(key: bytes, addr: bytes) -> None:
    abi.require(isinstance(addr, (bytes, bytearray)), b"type")
    abi.require(len(addr) == 32, b"addrlen")
    storage.set(key, bytes(addr))


def _read_addr(key: bytes) -> bytes:
    v = storage.get(key)
    abi.require(v is not None, b"miss")
    abi.require(len(v) == 32, b"addrlen")
    return v


def _set_status(s: bytes) -> None:
    storage.set(K_STATUS, s)


def _status() -> bytes:
    s = storage.get(K_STATUS)
    abi.require(s is not None, b"nost")
    return s


# ---- views -------------------------------------------------------------------


def initialized() -> int:
    """Return 1 if init has been called, else 0."""
    return 1 if storage.get(K_INIT) == b"\x01" else 0


def payer() -> bytes:
    return _read_addr(K_PAYER)


def payee() -> bytes:
    return _read_addr(K_PAYEE)


def arbiter() -> bytes:
    return _read_addr(K_ARBITER)


def agreed_amount() -> int:
    v = storage.get(K_AGREED)
    abi.require(v is not None, b"noA")
    return _from_u256(v)


def locked_amount() -> int:
    v = storage.get(K_LOCKED)
    abi.require(v is not None, b"noL")
    return _from_u256(v)


def status() -> bytes:
    return _status()


# ---- mutators ----------------------------------------------------------------


def init(
    payer_addr: bytes, payee_addr: bytes, arbiter_addr: bytes, amount: int
) -> None:
    """
    Initialize the escrow. Callable once.
    """
    abi.require(initialized() == 0, b"reinit")
    abi.require(amount > 0, b"zeroA")

    _write_addr(K_PAYER, payer_addr)
    _write_addr(K_PAYEE, payee_addr)
    _write_addr(K_ARBITER, arbiter_addr)
    storage.set(K_AGREED, _u256(amount))
    storage.set(K_LOCKED, _u256(0))
    _set_status(STATUS_OPEN)
    storage.set(K_INIT, b"\x01")


def fund(amount: int) -> bool:
    """
    Record that `amount` is locked for the escrow. Only payer may call.
    The actual coins must already be (or will be) available in the contract's balance.
    Idempotent until total equals agreed amount.
    """
    abi.require(_status() == STATUS_OPEN, b"notopen")

    caller = treasury.caller()  # deterministic caller address (32 bytes)
    abi.require(caller == _read_addr(K_PAYER), b"notpayer")
    abi.require(amount > 0, b"zero")

    agreed = agreed_amount()
    cur_locked = locked_amount()
    abi.require(cur_locked < agreed, b"full")
    # Clamp to remaining
    remaining = agreed - cur_locked
    abi.require(amount <= remaining, b"over")

    new_locked = cur_locked + amount
    storage.set(K_LOCKED, _u256(new_locked))

    events.emit(
        b"Funded",
        {b"payer": caller, b"amount": _u256(amount)},
    )
    return True


def release() -> bool:
    """
    Release funds to the payee.
    Authorized: arbiter only.
    """
    abi.require(_status() == STATUS_OPEN, b"notopen")
    caller = treasury.caller()
    abi.require(caller == _read_addr(K_ARBITER), b"notarb")

    amt = locked_amount()
    abi.require(amt > 0, b"nolock")

    # Safety: ensure we actually hold enough to pay out.
    abi.require(treasury.balance() >= amt, b"insuff")

    treasury.transfer(_read_addr(K_PAYEE), amt)
    storage.set(K_LOCKED, _u256(0))
    _set_status(STATUS_RELEASED)

    events.emit(
        b"Released",
        {b"payee": _read_addr(K_PAYEE), b"amount": _u256(amt)},
    )
    return True


def refund() -> bool:
    """
    Refund funds to the payer.
    Authorized: arbiter OR payer.
    """
    abi.require(_status() == STATUS_OPEN, b"notopen")
    caller = treasury.caller()
    is_arbiter = caller == _read_addr(K_ARBITER)
    is_payer = caller == _read_addr(K_PAYER)
    abi.require(is_arbiter or is_payer, b"unauth")

    amt = locked_amount()
    abi.require(amt > 0, b"nolock")
    abi.require(treasury.balance() >= amt, b"insuff")

    treasury.transfer(_read_addr(K_PAYER), amt)
    storage.set(K_LOCKED, _u256(0))
    _set_status(STATUS_REFUNDED)

    events.emit(
        b"Refunded",
        {b"payer": _read_addr(K_PAYER), b"amount": _u256(amt)},
    )
    return True


def cancel() -> bool:
    """
    Cancel the escrow before any funds are locked.
    Authorized: arbiter or payer. No transfers occur.
    """
    abi.require(_status() == STATUS_OPEN, b"notopen")
    abi.require(locked_amount() == 0, b"haslock")
    caller = treasury.caller()
    is_arbiter = caller == _read_addr(K_ARBITER)
    is_payer = caller == _read_addr(K_PAYER)
    abi.require(is_arbiter or is_payer, b"unauth")

    _set_status(STATUS_CANCELLED)
    events.emit(b"Canceled", {})
    return True
