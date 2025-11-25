# -*- coding: utf-8 -*-
"""
contracts.stdlib.utils.events
=============================

Canonical event names and thin, deterministic encoders used by Animica
standard contracts. All helpers route through the VM's safe event emitter
(`from stdlib import events as _ev`) and normalize field values via
`contracts.stdlib.utils.event_fields` so encodings are stable:

- Keys are **bytes** (ASCII suggested), lexicographically ordered.
- Values are **bytes** after normalization:
  - `int` → minimal big-endian unsigned bytes (0 → b"\x00")
  - `bool` → b"\x01"/b"\x00"
  - `bytes/bytearray` → unchanged

Event **names** are bytes and namespaced as `b"animica.<domain>.<Event>"`.
This module exports:
  1) `EV_*` byte constants for canonical names
  2) `emit_*` helpers that validate minimally and emit normalized fields
  3) `emit_custom(name, fields)` for bespoke events that still apply canonical normalization

These helpers are deliberately lightweight so they can be used from any
contract in the deterministic VM without pulling extra dependencies.
"""
from __future__ import annotations

from typing import Mapping, Union

from stdlib import events as _ev  # type: ignore
from . import event_fields, ensure_len, to_bytes, bool_flag

# ------------------------------------------------------------------------------
# Canonical event name constants
# ------------------------------------------------------------------------------

# Ownership / Access Control
EV_OWNERSHIP_TRANSFERRED = b"animica.access.OwnershipTransferred"
EV_ROLE_GRANTED          = b"animica.access.RoleGranted"
EV_ROLE_REVOKED          = b"animica.access.RoleRevoked"

# Control
EV_PAUSED                = b"animica.control.Paused"
EV_UNPAUSED              = b"animica.control.Unpaused"
EV_TIMELOCK_QUEUED       = b"animica.control.TimelockQueued"
EV_TIMELOCK_EXECUTED     = b"animica.control.TimelockExecuted"
EV_TIMELOCK_CANCELED     = b"animica.control.TimelockCanceled"

# Tokens (Animica-20 style)
EV_TRANSFER              = b"animica.token.Transfer"
EV_APPROVAL              = b"animica.token.Approval"
EV_MINT                  = b"animica.token.Mint"
EV_BURN                  = b"animica.token.Burn"

# Treasury / Payments
EV_ESCROW_DEPOSITED      = b"animica.treasury.EscrowDeposited"
EV_ESCROW_RELEASED       = b"animica.treasury.EscrowReleased"
EV_ESCROW_DISPUTED       = b"animica.treasury.EscrowDisputed"
EV_ESCROW_RESOLVED       = b"animica.treasury.EscrowResolved"
EV_SPLITTER_PAYMENT      = b"animica.treasury.SplitterPayment"

# Registry / Names
EV_NAME_REGISTERED       = b"animica.registry.NameRegistered"
EV_NAME_UPDATED          = b"animica.registry.NameUpdated"

# Capabilities (off-chain compute, DA, randomness, zk)
EV_AI_JOB_ENQUEUED       = b"animica.cap.ai.JobEnqueued"
EV_AI_RESULT_READY       = b"animica.cap.ai.ResultReady"
EV_Q_JOB_ENQUEUED        = b"animica.cap.q.JobEnqueued"
EV_Q_RESULT_READY        = b"animica.cap.q.ResultReady"
EV_DA_BLOB_PINNED        = b"animica.cap.da.BlobPinned"
EV_ZK_VERIFIED           = b"animica.cap.zk.Verified"

EV_RAND_COMMITTED        = b"animica.rand.Committed"
EV_RAND_REVEALED         = b"animica.rand.Revealed"
EV_RAND_BEACON_MIXED     = b"animica.rand.BeaconMixed"

# ------------------------------------------------------------------------------
# Core emit primitive (normalization + dispatch)
# ------------------------------------------------------------------------------

def _emit(name: bytes, fields: Mapping[Union[bytes, bytearray], Union[bytes, bytearray, int, bool]]) -> None:
    """
    Normalize and emit through VM stdlib.
    """
    _ev.emit(name, event_fields(fields))

def emit_custom(name: Union[bytes, bytearray], fields: Mapping[Union[bytes, bytearray], Union[bytes, bytearray, int, bool]]) -> None:
    """
    Emit any custom event while preserving canonical field normalization.
    """
    _emit(to_bytes(name), fields)

# ------------------------------------------------------------------------------
# Ownership / Access Control
# ------------------------------------------------------------------------------

def emit_ownership_transferred(previous_owner: Union[bytes, bytearray], new_owner: Union[bytes, bytearray]) -> None:
    ensure_len(previous_owner, min_len=1, max_len=64)
    ensure_len(new_owner, min_len=1, max_len=64)
    _emit(EV_OWNERSHIP_TRANSFERRED, {
        b"previous": to_bytes(previous_owner),
        b"new":      to_bytes(new_owner),
    })

def emit_role_granted(role: Union[bytes, bytearray], account: Union[bytes, bytearray], sender: Union[bytes, bytearray]) -> None:
    ensure_len(role, min_len=1, max_len=64)       # role id (e.g., 32-byte)
    ensure_len(account, min_len=1, max_len=64)    # address
    ensure_len(sender, min_len=1, max_len=64)     # admin
    _emit(EV_ROLE_GRANTED, {
        b"role":   to_bytes(role),
        b"account":to_bytes(account),
        b"sender": to_bytes(sender),
    })

def emit_role_revoked(role: Union[bytes, bytearray], account: Union[bytes, bytearray], sender: Union[bytes, bytearray]) -> None:
    ensure_len(role, min_len=1, max_len=64)
    ensure_len(account, min_len=1, max_len=64)
    ensure_len(sender, min_len=1, max_len=64)
    _emit(EV_ROLE_REVOKED, {
        b"role":    to_bytes(role),
        b"account": to_bytes(account),
        b"sender":  to_bytes(sender),
    })

# ------------------------------------------------------------------------------
# Control
# ------------------------------------------------------------------------------

def emit_paused(account: Union[bytes, bytearray]) -> None:
    ensure_len(account, min_len=1, max_len=64)
    _emit(EV_PAUSED, { b"account": to_bytes(account) })

def emit_unpaused(account: Union[bytes, bytearray]) -> None:
    ensure_len(account, min_len=1, max_len=64)
    _emit(EV_UNPAUSED, { b"account": to_bytes(account) })

def emit_timelock_queued(op_id: Union[bytes, bytearray], eta_seconds: int) -> None:
    ensure_len(op_id, min_len=1, max_len=64)  # operation identifier (hash)
    _emit(EV_TIMELOCK_QUEUED, { b"op": to_bytes(op_id), b"eta": eta_seconds })

def emit_timelock_executed(op_id: Union[bytes, bytearray]) -> None:
    ensure_len(op_id, min_len=1, max_len=64)
    _emit(EV_TIMELOCK_EXECUTED, { b"op": to_bytes(op_id) })

def emit_timelock_canceled(op_id: Union[bytes, bytearray]) -> None:
    ensure_len(op_id, min_len=1, max_len=64)
    _emit(EV_TIMELOCK_CANCELED, { b"op": to_bytes(op_id) })

# ------------------------------------------------------------------------------
# Tokens (Animica-20)
# ------------------------------------------------------------------------------

def emit_transfer(sender: Union[bytes, bytearray], to: Union[bytes, bytearray], value: int) -> None:
    ensure_len(sender, min_len=1, max_len=64)
    ensure_len(to,     min_len=1, max_len=64)
    _emit(EV_TRANSFER, {
        b"from": to_bytes(sender),
        b"to":   to_bytes(to),
        b"value": value,
    })

def emit_approval(owner: Union[bytes, bytearray], spender: Union[bytes, bytearray], value: int) -> None:
    ensure_len(owner,  min_len=1, max_len=64)
    ensure_len(spender,min_len=1, max_len=64)
    _emit(EV_APPROVAL, {
        b"owner":   to_bytes(owner),
        b"spender": to_bytes(spender),
        b"value":   value,
    })

def emit_mint(to: Union[bytes, bytearray], value: int) -> None:
    ensure_len(to, min_len=1, max_len=64)
    _emit(EV_MINT, { b"to": to_bytes(to), b"value": value })

def emit_burn(frm: Union[bytes, bytearray], value: int) -> None:
    ensure_len(frm, min_len=1, max_len=64)
    _emit(EV_BURN, { b"from": to_bytes(frm), b"value": value })

# ------------------------------------------------------------------------------
# Treasury / Payments
# ------------------------------------------------------------------------------

def emit_escrow_deposited(escrow_id: Union[bytes, bytearray], payer: Union[bytes, bytearray], payee: Union[bytes, bytearray], amount: int) -> None:
    ensure_len(escrow_id, min_len=1, max_len=64)
    ensure_len(payer,     min_len=1, max_len=64)
    ensure_len(payee,     min_len=1, max_len=64)
    _emit(EV_ESCROW_DEPOSITED, {
        b"id":     to_bytes(escrow_id),
        b"payer":  to_bytes(payer),
        b"payee":  to_bytes(payee),
        b"amount": amount,
    })

def emit_escrow_released(escrow_id: Union[bytes, bytearray], to: Union[bytes, bytearray], amount: int) -> None:
    ensure_len(escrow_id, min_len=1, max_len=64)
    ensure_len(to,        min_len=1, max_len=64)
    _emit(EV_ESCROW_RELEASED, {
        b"id":     to_bytes(escrow_id),
        b"to":     to_bytes(to),
        b"amount": amount,
    })

def emit_escrow_disputed(escrow_id: Union[bytes, bytearray]) -> None:
    ensure_len(escrow_id, min_len=1, max_len=64)
    _emit(EV_ESCROW_DISPUTED, { b"id": to_bytes(escrow_id) })

def emit_escrow_resolved(escrow_id: Union[bytes, bytearray], winner: Union[bytes, bytearray]) -> None:
    ensure_len(escrow_id, min_len=1, max_len=64)
    ensure_len(winner,    min_len=1, max_len=64)
    _emit(EV_ESCROW_RESOLVED, {
        b"id":     to_bytes(escrow_id),
        b"winner": to_bytes(winner),
    })

def emit_splitter_payment(release_id: Union[bytes, bytearray], to: Union[bytes, bytearray], amount: int) -> None:
    ensure_len(release_id, min_len=1, max_len=64)
    ensure_len(to,         min_len=1, max_len=64)
    _emit(EV_SPLITTER_PAYMENT, {
        b"release": to_bytes(release_id),
        b"to":      to_bytes(to),
        b"amount":  amount,
    })

# ------------------------------------------------------------------------------
# Registry / Names
# ------------------------------------------------------------------------------

def emit_name_registered(name: Union[bytes, bytearray], owner: Union[bytes, bytearray]) -> None:
    ensure_len(name,  min_len=1, max_len=64)   # e.g., bytes32 label
    ensure_len(owner, min_len=1, max_len=64)
    _emit(EV_NAME_REGISTERED, {
        b"name":  to_bytes(name),
        b"owner": to_bytes(owner),
    })

def emit_name_updated(name: Union[bytes, bytearray], old_addr: Union[bytes, bytearray], new_addr: Union[bytes, bytearray]) -> None:
    ensure_len(name,     min_len=1, max_len=64)
    ensure_len(old_addr, min_len=1, max_len=64)
    ensure_len(new_addr, min_len=1, max_len=64)
    _emit(EV_NAME_UPDATED, {
        b"name": to_bytes(name),
        b"old":  to_bytes(old_addr),
        b"new":  to_bytes(new_addr),
    })

# ------------------------------------------------------------------------------
# Capabilities
# ------------------------------------------------------------------------------

def emit_ai_job_enqueued(task_id: Union[bytes, bytearray], model: Union[bytes, bytearray], fee: int) -> None:
    ensure_len(task_id, min_len=1, max_len=64)
    ensure_len(model,   min_len=1, max_len=64)
    _emit(EV_AI_JOB_ENQUEUED, {
        b"task":  to_bytes(task_id),
        b"model": to_bytes(model),
        b"fee":   fee,
    })

def emit_ai_result_ready(task_id: Union[bytes, bytearray], ok: bool) -> None:
    ensure_len(task_id, min_len=1, max_len=64)
    _emit(EV_AI_RESULT_READY, {
        b"task": to_bytes(task_id),
        b"ok":   bool_flag(ok),
    })

def emit_quantum_job_enqueued(task_id: Union[bytes, bytearray], shots: int) -> None:
    ensure_len(task_id, min_len=1, max_len=64)
    _emit(EV_Q_JOB_ENQUEUED, {
        b"task":  to_bytes(task_id),
        b"shots": shots,
    })

def emit_quantum_result_ready(task_id: Union[bytes, bytearray], ok: bool) -> None:
    ensure_len(task_id, min_len=1, max_len=64)
    _emit(EV_Q_RESULT_READY, {
        b"task": to_bytes(task_id),
        b"ok":   bool_flag(ok),
    })

def emit_da_blob_pinned(ns: Union[bytes, bytearray, int], commitment: Union[bytes, bytearray], size_bytes: int) -> None:
    """
    ns: bytes or small int namespace id (normalized to bytes if int)
    commitment: NMT root/commitment (bytes)
    """
    ns_b: bytes
    if isinstance(ns, int):
        # minimal big-endian
        ns_b = b"\x00" if ns == 0 else int(ns).to_bytes((ns.bit_length() + 7)//8, "big")
    else:
        ns_b = to_bytes(ns)
    ensure_len(ns_b, min_len=1, max_len=16)             # DA namespaces tend to be short
    ensure_len(commitment, min_len=16, max_len=64)      # commitment/root
    _emit(EV_DA_BLOB_PINNED, {
        b"ns":    ns_b,
        b"commit":to_bytes(commitment),
        b"size":  size_bytes,
    })

def emit_zk_verified(circuit: Union[bytes, bytearray], ok: bool) -> None:
    ensure_len(circuit, min_len=1, max_len=64)
    _emit(EV_ZK_VERIFIED, {
        b"circuit": to_bytes(circuit),
        b"ok":      bool_flag(ok),
    })

# ------------------------------------------------------------------------------
# Randomness
# ------------------------------------------------------------------------------

def emit_rand_committed(round_id: int, addr: Union[bytes, bytearray]) -> None:
    ensure_len(addr, min_len=1, max_len=64)
    _emit(EV_RAND_COMMITTED, { b"round": round_id, b"addr": to_bytes(addr) })

def emit_rand_revealed(round_id: int, addr: Union[bytes, bytearray]) -> None:
    ensure_len(addr, min_len=1, max_len=64)
    _emit(EV_RAND_REVEALED, { b"round": round_id, b"addr": to_bytes(addr) })

def emit_rand_beacon_mixed(round_id: int, beacon: Union[bytes, bytearray]) -> None:
    ensure_len(beacon, min_len=16, max_len=64)
    _emit(EV_RAND_BEACON_MIXED, { b"round": round_id, b"beacon": to_bytes(beacon) })

# ------------------------------------------------------------------------------
# Public exports
# ------------------------------------------------------------------------------

__all__ = [
    # constants
    "EV_OWNERSHIP_TRANSFERRED", "EV_ROLE_GRANTED", "EV_ROLE_REVOKED",
    "EV_PAUSED", "EV_UNPAUSED", "EV_TIMELOCK_QUEUED", "EV_TIMELOCK_EXECUTED", "EV_TIMELOCK_CANCELED",
    "EV_TRANSFER", "EV_APPROVAL", "EV_MINT", "EV_BURN",
    "EV_ESCROW_DEPOSITED", "EV_ESCROW_RELEASED", "EV_ESCROW_DISPUTED", "EV_ESCROW_RESOLVED",
    "EV_SPLITTER_PAYMENT",
    "EV_NAME_REGISTERED", "EV_NAME_UPDATED",
    "EV_AI_JOB_ENQUEUED", "EV_AI_RESULT_READY", "EV_Q_JOB_ENQUEUED", "EV_Q_RESULT_READY",
    "EV_DA_BLOB_PINNED", "EV_ZK_VERIFIED",
    "EV_RAND_COMMITTED", "EV_RAND_REVEALED", "EV_RAND_BEACON_MIXED",
    # helpers
    "emit_custom",
    "emit_ownership_transferred", "emit_role_granted", "emit_role_revoked",
    "emit_paused", "emit_unpaused", "emit_timelock_queued", "emit_timelock_executed", "emit_timelock_canceled",
    "emit_transfer", "emit_approval", "emit_mint", "emit_burn",
    "emit_escrow_deposited", "emit_escrow_released", "emit_escrow_disputed", "emit_escrow_resolved",
    "emit_splitter_payment",
    "emit_name_registered", "emit_name_updated",
    "emit_ai_job_enqueued", "emit_ai_result_ready", "emit_quantum_job_enqueued", "emit_quantum_result_ready",
    "emit_da_blob_pinned", "emit_zk_verified",
    "emit_rand_committed", "emit_rand_revealed", "emit_rand_beacon_mixed",
]
