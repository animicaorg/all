# -*- coding: utf-8 -*-
"""
contracts.stdlib.treasury.escrow
================================

Generalized escrow accounting helpers for contracts that hold funds in their own
treasury balance (via VM stdlib `treasury`). This module lets a contract open
escrows between a payer and a payee, optionally appoint an arbiter, allow
disputes, and finalize by releasing to payee or refunding to payer.

Design goals
------------
- **Deterministic**: pure arithmetic and storage; no clocks or randomness.
- **Audit-friendly**: small state machine with explicit error codes.
- **Reserve-first**: on open, escrowed funds are *reserved* from the contract's
  treasury so they cannot be double-spent. Settlement performs the actual
  `treasury.transfer`.
- **Pluggable time/deadlines**: deadlines are *block-height integers provided
  by the caller* (e.g., your contract can pass current height from its own
  state or inputs). If `deadline == 0`, the escrow has no deadline.

Usage pattern (inside a contract)
---------------------------------
Typical entrypoints call these helpers and pass the *actor* (caller address)
explicitly. For example:

    from stdlib import abi, events
    from contracts.stdlib.treasury import treasury as trez  # convenience alias
    from contracts.stdlib.treasury import escrow as esc

    def open_escrow(payer: bytes, payee: bytes, amount: int, deadline: int, nonce: bytes, arbiter: bytes=b"", meta: bytes=b"") -> bytes:
        # ensure contract has enough unreserved funds
        trez.require_min_balance(amount + esc.reserved_total())
        return esc.open(payer, payee, amount, deadline, nonce, arbiter, meta)

    def release(id: bytes, actor: bytes) -> None:
        esc.release(id, actor)

    def refund(id: bytes, actor: bytes, now_height: int) -> None:
        esc.refund(id, actor, now_height)

    def dispute(id: bytes, actor: bytes, reason: bytes=b"") -> None:
        esc.dispute(id, actor, reason)

    def resolve(id: bytes, actor: bytes, payout_to_payee: bool, reason: bytes=b"") -> None:
        esc.resolve(id, actor, payout_to_payee, reason)

State & storage layout
----------------------
- Escrow ID: keccak256(payer|payee|amount|deadline|nonce) (32 bytes).
- Keys are prefixed; integers are stored as 32-byte big-endian u256.

    "esc:ex:" + id  -> b"\x01" (existence flag)
    "esc:p:"  + id  -> payer (bytes)
    "esc:q:"  + id  -> payee (bytes)
    "esc:a:"  + id  -> arbiter (bytes, empty if none)
    "esc:m:"  + id  -> amount (u256)
    "esc:d:"  + id  -> deadline (u256; 0 = none)
    "esc:s:"  + id  -> status byte (0=open,1=paid,2=refunded,3=disputed)
    "esc:r:"  + id  -> latest reason (bytes; dispute/resolve text)
    "esc:x:"  + id  -> metadata blob (bytes; optional)

- Global reserved total:
    "esc:reserved_total" -> u256

Statuses
--------
OPEN=0, PAID=1, REFUNDED=2, DISPUTED=3

Error codes (revert messages)
-----------------------------
- b"ESCROW:NOT_FOUND"
- b"ESCROW:EXISTS"
- b"ESCROW:BAD_ACTOR"
- b"ESCROW:BAD_STATE"
- b"ESCROW:ZERO_ADDR"
- b"ESCROW:NEG_AMOUNT"
- b"ESCROW:NO_FUNDS"
- b"ESCROW:DEADLINE"

Events (names)
--------------
- b"EscrowOpened"    {id,payer,payee,arbiter,amount,deadline,meta}
- b"EscrowReleased"  {id,amount}
- b"EscrowRefunded"  {id,amount}
- b"EscrowDisputed"  {id,reason}
- b"EscrowResolved"  {id,outcome,reason} outcome=b"paid"|b"refunded"

Security & invariants
---------------------
- On OPEN → (PAID|REFUNDED), global reserved_total decreases by `amount` exactly once.
- No transition from a terminal state (PAID/REFUNDED) is allowed.
- DISPUTED can only be resolved by the arbiter (if set). If no arbiter is set,
  disputes still freeze the escrow until caller logic decides to resolve
  (e.g., by deploying a version with a governance actor that can act as arbiter).

"""
from __future__ import annotations

from typing import Dict, Final, Tuple

from stdlib import storage  # type: ignore
from stdlib import abi, events  # type: ignore
from stdlib import hash as _hash  # type: ignore

# Reuse treasury helpers from the sibling module
from . import balance as _trez_balance
from . import require_min_balance as _trez_require
from . import transfer as _trez_transfer

# ---------- Constants & status codes ----------

_U256_MAX: Final[int] = (1 << 256) - 1

OPEN: Final[int] = 0
PAID: Final[int] = 1
REFUNDED: Final[int] = 2
DISPUTED: Final[int] = 3

# Storage prefixes
_P_EX: Final[bytes] = b"esc:ex:"
_P_PAYER: Final[bytes] = b"esc:p:"
_P_PAYEE: Final[bytes] = b"esc:q:"
_P_ARBITER: Final[bytes] = b"esc:a:"
_P_AMOUNT: Final[bytes] = b"esc:m:"
_P_DEADLINE: Final[bytes] = b"esc:d:"
_P_STATUS: Final[bytes] = b"esc:s:"
_P_REASON: Final[bytes] = b"esc:r:"
_P_META: Final[bytes] = b"esc:x:"
_P_RESERVED_TOTAL: Final[bytes] = b"esc:reserved_total"

# ---------- Small encode/decode helpers ----------


def _u256_to_bytes(x: int) -> bytes:
    if x < 0 or x > _U256_MAX:
        abi.revert(b"ESCROW:NEG_AMOUNT")
    return int(x).to_bytes(32, "big")


def _bytes_to_u256(b: bytes) -> int:
    if len(b) == 0:
        return 0
    if len(b) != 32:
        # Defensive: corrupted storage should not happen; classify as not found
        abi.revert(b"ESCROW:NOT_FOUND")
    return int.from_bytes(b, "big")


def _set_u256(k: bytes, x: int) -> None:
    storage.set(k, _u256_to_bytes(x))


def _get_u256(k: bytes) -> int:
    return _bytes_to_u256(storage.get(k))


def _setb(k: bytes, v: bytes) -> None:
    storage.set(k, bytes(v))


def _getb(k: bytes) -> bytes:
    return storage.get(k)


def _k(prefix: bytes, id_: bytes) -> bytes:
    return prefix + id_


def _exists(id_: bytes) -> bool:
    return storage.get(_k(_P_EX, id_)) == b"\x01"


def _set_exists(id_: bytes) -> None:
    storage.set(_k(_P_EX, id_), b"\x01")


def _del_exists(
    id_: bytes,
) -> None:  # kept for completeness (not used; we keep audit trails)
    storage.set(_k(_P_EX, id_), b"")


# ---------- Public getters ----------


def reserved_total() -> int:
    """Current total reserved amount across all OPEN/DISPUTED escrows."""
    return _get_u256(_P_RESERVED_TOTAL)


def available_balance() -> int:
    """Treasury balance not reserved by any escrow."""
    bal = _trez_balance()
    res = reserved_total()
    # Invariant: res <= bal is expected; if not, available floors at 0
    return bal - res if bal >= res else 0


def compute_id(
    payer: bytes, payee: bytes, amount: int, deadline: int, nonce: bytes
) -> bytes:
    """
    Deterministic escrow id. Contracts should persist/emit this id to reference escrows.
    """
    _require_addr(payer)
    _require_addr(payee)
    _require_nonnegative(amount)
    _require_u256(deadline)
    # id = keccak256(payer|payee|u256(amount)|u256(deadline)|nonce)
    return _hash.keccak256(
        payer + payee + _u256_to_bytes(amount) + _u256_to_bytes(deadline) + bytes(nonce)
    )


def info(id_: bytes) -> Dict[bytes, bytes]:
    """
    Return a dict view of the escrow (bytes keys/values; integers are 32-byte u256).
    Keys: b"payer", b"payee", b"arbiter", b"amount", b"deadline", b"status", b"meta", b"reason".
    Reverts if not found.
    """
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    return {
        b"payer": _getb(_k(_P_PAYER, id_)),
        b"payee": _getb(_k(_P_PAYEE, id_)),
        b"arbiter": _getb(_k(_P_ARBITER, id_)),
        b"amount": _get_u256(_k(_P_AMOUNT, id_)).to_bytes(32, "big"),
        b"deadline": _get_u256(_k(_P_DEADLINE, id_)).to_bytes(32, "big"),
        b"status": bytes(
            [
                (
                    storage.get(_k(_P_STATUS, id_))[0]
                    if storage.get(_k(_P_STATUS, id_))
                    else 0
                )
            ]
        ),
        b"meta": _getb(_k(_P_META, id_)),
        b"reason": _getb(_k(_P_REASON, id_)),
    }


def status(id_: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    sb = storage.get(_k(_P_STATUS, id_))
    return sb[0] if len(sb) == 1 else 0


# ---------- Core operations ----------


def open(
    payer: bytes,
    payee: bytes,
    amount: int,
    deadline: int,
    nonce: bytes,
    arbiter: bytes = b"",
    meta: bytes = b"",
) -> bytes:
    """
    Open a new escrow, reserving `amount` from the contract's treasury balance.

    Authorization: decided by the contract. This helper only enforces invariants:
      - valid addresses, non-negative amounts
      - sufficient *available* funds (balance - already_reserved >= amount)
      - id uniqueness

    Returns the `id` (32-byte keccak).
    """
    _require_addr(payer)
    _require_addr(payee)
    _require_nonnegative(amount)
    _require_u256(deadline)
    if payer == payee:
        abi.revert(b"ESCROW:BAD_ACTOR")
    if arbiter is None:
        arbiter = b""
    if not isinstance(meta, (bytes, bytearray)):
        meta = b""

    id_ = compute_id(payer, payee, amount, deadline, nonce)
    if _exists(id_):
        abi.revert(b"ESCROW:EXISTS")

    # Ensure funds are available to reserve (does not move funds yet).
    avail = available_balance()
    if avail < amount:
        abi.revert(b"ESCROW:NO_FUNDS")

    # Persist fields
    _setb(_k(_P_PAYER, id_), payer)
    _setb(_k(_P_PAYEE, id_), payee)
    _setb(_k(_P_ARBITER, id_), bytes(arbiter))
    _set_u256(_k(_P_AMOUNT, id_), int(amount))
    _set_u256(_k(_P_DEADLINE, id_), int(deadline))
    _setb(_k(_P_STATUS, id_), bytes([OPEN]))
    _setb(_k(_P_META, id_), bytes(meta))
    _setb(_k(_P_REASON, id_), b"")
    _set_exists(id_)

    # Increase reserved
    _set_u256(_P_RESERVED_TOTAL, reserved_total() + int(amount))

    events.emit(
        b"EscrowOpened",
        {
            b"id": id_,
            b"payer": payer,
            b"payee": payee,
            b"arbiter": bytes(arbiter),
            b"amount": _u256_to_bytes(int(amount)),
            b"deadline": _u256_to_bytes(int(deadline)),
            b"meta": bytes(meta),
        },
    )
    return id_


def dispute(id_: bytes, actor: bytes, reason: bytes = b"") -> None:
    """
    Move OPEN → DISPUTED. Only payer or payee may dispute.
    """
    _require_addr(actor)
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    st = status(id_)
    if st != OPEN:
        abi.revert(b"ESCROW:BAD_STATE")

    payer = _getb(_k(_P_PAYER, id_))
    payee = _getb(_k(_P_PAYEE, id_))
    if actor != payer and actor != payee:
        abi.revert(b"ESCROW:BAD_ACTOR")

    _setb(_k(_P_STATUS, id_), bytes([DISPUTED]))
    _setb(_k(_P_REASON, id_), bytes(reason or b""))
    events.emit(b"EscrowDisputed", {b"id": id_, b"reason": bytes(reason or b"")})


def release(id_: bytes, actor: bytes) -> None:
    """
    OPEN → PAID: send funds to payee. Allowed for `payer` or `arbiter`.
    """
    _require_addr(actor)
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    st = status(id_)
    if st != OPEN:
        abi.revert(b"ESCROW:BAD_STATE")

    payer = _getb(_k(_P_PAYER, id_))
    payee = _getb(_k(_P_PAYEE, id_))
    arbiter = _getb(_k(_P_ARBITER, id_))
    if actor != payer and (len(arbiter) == 0 or actor != arbiter):
        abi.revert(b"ESCROW:BAD_ACTOR")

    amt = _get_u256(_k(_P_AMOUNT, id_))
    _finalize_payout(id_, amt, payee, is_refund=False)


def refund(id_: bytes, actor: bytes, now_height: int) -> None:
    """
    OPEN → REFUNDED: send funds back to payer.

    Authorization:
      - arbiter can refund anytime (if set).
      - payee can voluntarily refund anytime.
      - payer can refund only if `deadline > 0 and now_height >= deadline`.

    Note: `now_height` is an *argument* supplied by your contract entrypoint
    according to your environment. If you don't have block height, pass 0 and
    rely on arbiter/payee paths.
    """
    _require_addr(actor)
    _require_u256(now_height)
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    st = status(id_)
    if st != OPEN:
        abi.revert(b"ESCROW:BAD_STATE")

    payer = _getb(_k(_P_PAYER, id_))
    payee = _getb(_k(_P_PAYEE, id_))
    arbiter = _getb(_k(_P_ARBITER, id_))
    dl = _get_u256(_k(_P_DEADLINE, id_))

    can_refund = False
    if len(arbiter) != 0 and actor == arbiter:
        can_refund = True
    elif actor == payee:
        can_refund = True
    elif actor == payer and dl > 0 and int(now_height) >= dl:
        can_refund = True

    if not can_refund:
        abi.revert(b"ESCROW:DEADLINE")

    amt = _get_u256(_k(_P_AMOUNT, id_))
    _finalize_payout(id_, amt, payer, is_refund=True)


def resolve(
    id_: bytes, actor: bytes, payout_to_payee: bool, reason: bytes = b""
) -> None:
    """
    DISPUTED → (PAID|REFUNDED) by arbiter only.
    """
    _require_addr(actor)
    if not _exists(id_):
        abi.revert(b"ESCROW:NOT_FOUND")
    st = status(id_)
    if st != DISPUTED:
        abi.revert(b"ESCROW:BAD_STATE")

    arbiter = _getb(_k(_P_ARBITER, id_))
    if len(arbiter) == 0 or actor != arbiter:
        abi.revert(b"ESCROW:BAD_ACTOR")

    payer = _getb(_k(_P_PAYER, id_))
    payee = _getb(_k(_P_PAYEE, id_))
    amt = _get_u256(_k(_P_AMOUNT, id_))

    _setb(_k(_P_REASON, id_), bytes(reason or b""))

    if payout_to_payee:
        _finalize_payout(id_, amt, payee, is_refund=False)
        events.emit(
            b"EscrowResolved",
            {b"id": id_, b"outcome": b"paid", b"reason": bytes(reason or b"")},
        )
    else:
        _finalize_payout(id_, amt, payer, is_refund=True)
        events.emit(
            b"EscrowResolved",
            {b"id": id_, b"outcome": b"refunded", b"reason": bytes(reason or b"")},
        )


# ---------- Internal helpers ----------


def _finalize_payout(
    id_: bytes, amount: int, to_addr: bytes, *, is_refund: bool
) -> None:
    """
    Common tail for OPEN/DISPUTED terminal transitions:
      - decrease reserved_total
      - update status
      - perform treasury.transfer
      - emit event
    """
    _require_addr(to_addr)
    _require_nonnegative(amount)

    # Decrease reserved_total (floor at 0 defensively)
    cur = reserved_total()
    new_res = cur - amount if cur >= amount else 0
    _set_u256(_P_RESERVED_TOTAL, new_res)

    # Status & event
    _setb(_k(_P_STATUS, id_), bytes([REFUNDED if is_refund else PAID]))

    # Perform the actual transfer from the contract treasury to recipient.
    # Ensure the contract still has funds (should hold due to reservation, but
    # a buggy/hostile contract could have spent them; enforce invariant).
    _trez_require(amount)
    _trez_transfer(to_addr, amount)

    if is_refund:
        events.emit(b"EscrowRefunded", {b"id": id_, b"amount": _u256_to_bytes(amount)})
    else:
        events.emit(b"EscrowReleased", {b"id": id_, b"amount": _u256_to_bytes(amount)})


# ---------- Validation ----------


def _require_addr(addr: bytes) -> None:
    if not isinstance(addr, (bytes, bytearray)) or len(addr) == 0:
        abi.revert(b"ESCROW:ZERO_ADDR")


def _require_nonnegative(x: int) -> None:
    if x < 0:
        abi.revert(b"ESCROW:NEG_AMOUNT")


def _require_u256(x: int) -> None:
    if x < 0 or x > _U256_MAX:
        abi.revert(b"ESCROW:NEG_AMOUNT")


__all__ = [
    # constants
    "OPEN",
    "PAID",
    "REFUNDED",
    "DISPUTED",
    # balance views
    "reserved_total",
    "available_balance",
    # id & queries
    "compute_id",
    "info",
    "status",
    # operations
    "open",
    "dispute",
    "release",
    "refund",
    "resolve",
]
