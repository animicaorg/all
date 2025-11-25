# -*- coding: utf-8 -*-
"""
Deterministic Escrow (template)
------------------------------

A minimal N-of-M style escrow between a payer and a payee with an optional arbiter.

Model (no external time dependency):
- Parties: payer, payee, optional arbiter.
- Amount: fixed u128 at setup. Contract expects its own treasury balance to hold >= amount
  before release/refund actions (i.e., the deployer funds the contract address out-of-band).
- Approvals:
    * release() requires BOTH payer & payee approvals, OR arbiter override.
    * refund()  requires BOTH payer & payee approvals, OR arbiter override.
  Approvals are per-outcome and revocable per party.
- Single-settlement invariant: exactly one of {released, refunded} can happen once.
- Events: setup, approvals/withdrawals of approvals, released, refunded.

Deterministic subset notes:
- No wall-clock usage; no randomness; no I/O; bounded storage; bounded integers (u128).
- Addresses are 32-byte values (bech32m-encoded off-chain; here handled as raw bytes).

This template favors clarity and safety; adapt as needed.
"""
from __future__ import annotations

from typing import Optional, Tuple

# Contract-safe stdlib (deterministic)
from stdlib import storage, events, abi, treasury


# ---- constants & keys --------------------------------------------------------

_U128_MAX = (1 << 128) - 1

K_PAYER = b"escrow:payer"
K_PAYEE = b"escrow:payee"
K_ARBITER = b"escrow:arbiter"
K_AMOUNT = b"escrow:amount"          # u128 BE
K_INIT = b"escrow:initialized"       # b"\x01" when set
K_DONE = b"escrow:settled"           # b"\x01" when either outcome executed
K_OUTCOME = b"escrow:outcome"        # b"released" | b"refunded"

# per-party approvals (release / refund)
K_APPR_PAYER_REL = b"escrow:appr:rele:payer"
K_APPR_PAYEE_REL = b"escrow:appr:rele:payee"
K_APPR_PAYER_REF = b"escrow:appr:refd:payer"
K_APPR_PAYEE_REF = b"escrow:appr:refd:payee"


# ---- helpers -----------------------------------------------------------------


def _require(cond: bool, code: bytes) -> None:
    if not cond:
        abi.revert(code)


def _u128(n: int) -> int:
    _require(isinstance(n, int) and 0 <= n <= _U128_MAX, b"U128_BOUNDS")
    return n


def _put_u128(key: bytes, n: int) -> None:
    n = _u128(n)
    storage.set(key, n.to_bytes(16, "big"))


def _get_u128(key: bytes) -> int:
    v = storage.get(key)
    _require(v is not None and len(v) == 16, b"BAD_U128_ENC")
    return int.from_bytes(v, "big")


def _addr_ok(a: Optional[bytes]) -> bool:
    return isinstance(a, (bytes, bytearray)) and len(a) == 32


def _get_addr(key: bytes) -> Optional[bytes]:
    v = storage.get(key)
    if v is None:
        return None
    _require(len(v) == 32, b"ADDR_LEN")
    return v


def _set_flag(key: bytes, on: bool) -> None:
    storage.set(key, b"\x01" if on else b"\x00")


def _get_flag(key: bytes) -> bool:
    v = storage.get(key)
    if v is None:
        return False
    return v == b"\x01"


def _only_party(sender: bytes) -> None:
    payer = _get_addr(K_PAYER)
    payee = _get_addr(K_PAYEE)
    _require(payer is not None and payee is not None, b"NOT_INIT")
    _require(sender == payer or sender == payee, b"ONLY_PARTY")


def _only_arbiter(sender: bytes) -> None:
    arb = _get_addr(K_ARBITER)
    _require(arb is not None and sender == arb, b"ONLY_ARBITER")


def _not_settled() -> None:
    _require(not _get_flag(K_DONE), b"ALREADY_SETTLED")


def _maybe_init_guard() -> None:
    _require(storage.get(K_INIT) is None, b"ALREADY_INIT")


def _mark_settled(outcome: bytes) -> None:
    _set_flag(K_DONE, True)
    storage.set(K_OUTCOME, outcome)


def _allowance_satisfied(amount: int) -> None:
    # Ensure the contract holds at least `amount` to transfer out.
    bal = treasury.balance()
    _require(bal >= amount, b"INSUFFICIENT_ESCROW_BAL")


def _pay(to: bytes, amount: int) -> None:
    # Move funds from this contract's treasury balance to recipient.
    _require(_addr_ok(to), b"ADDR_LEN")
    _require(amount > 0, b"ZERO_AMOUNT")
    _allowance_satisfied(amount)
    treasury.transfer(to, amount)


# ---- public: setup & views ---------------------------------------------------


def setup(payer: bytes, payee: bytes, amount: int, arbiter: Optional[bytes] = None) -> None:
    """
    Initialize the escrow.

    Args:
      payer   (address: bytes32)
      payee   (address: bytes32)
      amount  (u128)
      arbiter (optional address: bytes32)

    Rules:
      - Callable exactly once (first transaction post-deploy). Anyone may call.
      - All addresses must be 32 bytes.
      - `amount` is bounded to u128.
    Emits:
      EscrowSetup(payer, payee, amount, arbiter?)
    """
    _maybe_init_guard()

    _require(_addr_ok(payer), b"PAYER_BAD")
    _require(_addr_ok(payee), b"PAYEE_BAD")
    if arbiter is not None:
        _require(_addr_ok(arbiter), b"ARBITER_BAD")

    _put_u128(K_AMOUNT, amount)
    storage.set(K_PAYER, bytes(payer))
    storage.set(K_PAYEE, bytes(payee))
    if arbiter is not None:
        storage.set(K_ARBITER, bytes(arbiter))

    _set_flag(K_INIT, True)

    events.emit(
        b"EscrowSetup",
        {
            b"payer": payer,
            b"payee": payee,
            b"amount": amount.to_bytes(16, "big"),
            b"arbiter": (arbiter if arbiter is not None else b"\x00" * 32),
        },
    )


def status() -> dict:
    """
    Return a compact status dictionary for tooling/UI.
    Keys:
      payer, payee, arbiter, amount, initialized, settled, outcome,
      approvals: { payer_release, payee_release, payer_refund, payee_refund }
    """
    return {
        b"payer": _get_addr(K_PAYER) or b"\x00" * 32,
        b"payee": _get_addr(K_PAYEE) or b"\x00" * 32,
        b"arbiter": _get_addr(K_ARBITER) or b"\x00" * 32,
        b"amount": (_get_u128(K_AMOUNT).to_bytes(16, "big") if storage.get(K_AMOUNT) else (0).to_bytes(16, "big")),
        b"initialized": b"\x01" if storage.get(K_INIT) else b"\x00",
        b"settled": b"\x01" if _get_flag(K_DONE) else b"\x00",
        b"outcome": storage.get(K_OUTCOME) or b"",
        b"approvals": {
            b"payer_release": b"\x01" if _get_flag(K_APPR_PAYER_REL) else b"\x00",
            b"payee_release": b"\x01" if _get_flag(K_APPR_PAYEE_REL) else b"\x00",
            b"payer_refund": b"\x01" if _get_flag(K_APPR_PAYER_REF) else b"\x00",
            b"payee_refund": b"\x01" if _get_flag(K_APPR_PAYEE_REF) else b"\x00",
        },
        b"escrow_balance": treasury.balance().to_bytes(16, "big"),
    }


# ---- public: approvals -------------------------------------------------------


def approve_release() -> None:
    """
    Caller (payer or payee) signals approval to release funds to payee.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_party(sender)

    if sender == _get_addr(K_PAYER):
        _set_flag(K_APPR_PAYER_REL, True)
    else:
        _set_flag(K_APPR_PAYEE_REL, True)

    events.emit(b"ApprovalRelease", {b"who": sender})


def revoke_release() -> None:
    """
    Caller revokes their release approval.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_party(sender)

    if sender == _get_addr(K_PAYER):
        _set_flag(K_APPR_PAYER_REL, False)
    else:
        _set_flag(K_APPR_PAYEE_REL, False)

    events.emit(b"RevokeRelease", {b"who": sender})


def approve_refund() -> None:
    """
    Caller (payer or payee) signals approval to refund funds to payer.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_party(sender)

    if sender == _get_addr(K_PAYER):
        _set_flag(K_APPR_PAYER_REF, True)
    else:
        _set_flag(K_APPR_PAYEE_REF, True)

    events.emit(b"ApprovalRefund", {b"who": sender})


def revoke_refund() -> None:
    """
    Caller revokes their refund approval.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_party(sender)

    if sender == _get_addr(K_PAYER):
        _set_flag(K_APPR_PAYER_REF, False)
    else:
        _set_flag(K_APPR_PAYEE_REF, False)

    events.emit(b"RevokeRefund", {b"who": sender})


# ---- public: arbiter overrides ----------------------------------------------


def arbiter_release() -> None:
    """
    Arbiter overrides and releases to payee.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_arbiter(sender)
    _do_release(override=True)


def arbiter_refund() -> None:
    """
    Arbiter overrides and refunds to payer.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    sender = abi.sender()
    _only_arbiter(sender)
    _do_refund(override=True)


# ---- public: settlement actions ---------------------------------------------


def release() -> None:
    """
    Release escrow to payee.
    Requires: payer_release AND payee_release approvals, OR arbiter override variant.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    _do_release(override=False)


def refund() -> None:
    """
    Refund escrow back to payer.
    Requires: payer_refund AND payee_refund approvals, OR arbiter override variant.
    """
    _not_settled()
    _require(storage.get(K_INIT) is not None, b"NOT_INIT")
    _do_refund(override=False)


# ---- internal: settlement engines -------------------------------------------


def _do_release(override: bool) -> None:
    payer = _get_addr(K_PAYER)
    payee = _get_addr(K_PAYEE)
    _require(payer is not None and payee is not None, b"NOT_INIT")

    if not override:
        _require(_get_flag(K_APPR_PAYER_REL) and _get_flag(K_APPR_PAYEE_REL), b"MISSING_APPROVALS")

    amount = _get_u128(K_AMOUNT)
    _pay(payee, amount)
    _mark_settled(b"released")

    events.emit(
        b"Released",
        {
            b"payer": payer,
            b"payee": payee,
            b"amount": amount.to_bytes(16, "big"),
            b"byArbiter": b"\x01" if override else b"\x00",
        },
    )


def _do_refund(override: bool) -> None:
    payer = _get_addr(K_PAYER)
    payee = _get_addr(K_PAYEE)
    _require(payer is not None and payee is not None, b"NOT_INIT")

    if not override:
        _require(_get_flag(K_APPR_PAYER_REF) and _get_flag(K_APPR_PAYEE_REF), b"MISSING_APPROVALS")

    amount = _get_u128(K_AMOUNT)
    _pay(payer, amount)
    _mark_settled(b"refunded")

    events.emit(
        b"Refunded",
        {
            b"payer": payer,
            b"payee": payee,
            b"amount": amount.to_bytes(16, "big"),
            b"byArbiter": b"\x01" if override else b"\x00",
        },
    )


# ---- ABI surface description (informal, for tools/abi_gen.py) ---------------

"""
@abi
functions:
  - name: setup
    inputs:
      - {name: payer,   type: address}
      - {name: payee,   type: address}
      - {name: amount,  type: uint128}
      - {name: arbiter, type: address, optional: true}
    outputs: []
    notice: Initialize payer/payee, optional arbiter, and fixed amount (u128). One-time only.

  - name: status
    inputs: []
    outputs:
      - {name: status, type: bytes}  # opaque dict encoded by the runtime (tooling-friendly)
    stateMutability: view
    notice: Return compact escrow status for UIs.

  - name: approve_release
    inputs: []
    outputs: []
    notice: Caller (payer or payee) approves releasing to payee.

  - name: revoke_release
    inputs: []
    outputs: []
    notice: Caller revokes release approval.

  - name: approve_refund
    inputs: []
    outputs: []
    notice: Caller (payer or payee) approves refunding to payer.

  - name: revoke_refund
    inputs: []
    outputs: []
    notice: Caller revokes refund approval.

  - name: release
    inputs: []
    outputs: []
    notice: Release funds to payee (needs both party approvals unless arbiter_* used).

  - name: refund
    inputs: []
    outputs: []
    notice: Refund funds to payer (needs both party approvals unless arbiter_* used).

  - name: arbiter_release
    inputs: []
    outputs: []
    notice: Arbiter override to release.

  - name: arbiter_refund
    inputs: []
    outputs: []
    notice: Arbiter override to refund.

events:
  - {name: EscrowSetup,    inputs: [{name: payer, type: address}, {name: payee, type: address}, {name: amount, type: uint128}, {name: arbiter, type: address}]}
  - {name: ApprovalRelease,inputs: [{name: who,   type: address}]}
  - {name: RevokeRelease,  inputs: [{name: who,   type: address}]}
  - {name: ApprovalRefund, inputs: [{name: who,   type: address}]}
  - {name: RevokeRefund,   inputs: [{name: who,   type: address}]}
  - {name: Released,       inputs: [{name: payer, type: address}, {name: payee, type: address}, {name: amount, type: uint128}, {name: byArbiter, type: bool}]}
  - {name: Refunded,       inputs: [{name: payer, type: address}, {name: payee, type: address}, {name: amount, type: uint128}, {name: byArbiter, type: bool}]}

errors:
  - {name: U128_BOUNDS}
  - {name: BAD_U128_ENC}
  - {name: ADDR_LEN}
  - {name: NOT_INIT}
  - {name: ONLY_PARTY}
  - {name: ONLY_ARBITER}
  - {name: ALREADY_INIT}
  - {name: ALREADY_SETTLED}
  - {name: INSUFFICIENT_ESCROW_BAL}
  - {name: ZERO_AMOUNT}
  - {name: MISSING_APPROVALS}
"""
