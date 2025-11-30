# -*- coding: utf-8 -*-
"""
contracts.stdlib.control.timelock
=================================

Deterministic **timelock** helper for Animica Python contracts.

This module provides a self-contained, storage-backed queue of "operations"
that can only be executed after a delay measured in **blocks** (a deterministic
clock). It is designed so contracts can **guard** sensitive admin functions
(e.g., parameter changes, role grants, pauses) behind an explicit delay with a
clear, reproducible transcript.

Core ideas
----------
- Operations are identified by a deterministic **op_id = sha3_256(tag | payload | salt)**.
- Each queued op stores an **ETA = now_height + delay_blocks**.
- An optional **grace window** allows execution for a bounded time after ETA.
- **Admin authorization** is required for mutating timelock parameters and for
  cancelling scheduled operations. By default:
  - If `stdlib.access.roles` is linked, callers with role **TIMELOCK_ADMIN**
    are admins.
  - Else if `stdlib.access.ownable` is linked, the **Owner** is admin.
  - Else, an explicit `timelock admin` address can be set in storage via
    :func:`set_admin`.
- The module does **not** invoke arbitrary functions. Instead it offers a
  pattern: you compute an op_id for the *intended* action, queue it, and at
  execution time you check `ready(op_id)` and then perform the action guarded by
  your contract function.

Deterministic clock
-------------------
The timelock needs the **current block height**. We attempt to read it from the
VM stdlib if available (common attribute names are probed):
  - `abi.block_height()` or `abi.get_block_height()` or `abi.block_number()`.
If the height cannot be read automatically, functions that rely on "now"
accept an optional `now_height: int` parameter. If both are provided, the
explicit `now_height` wins.

Storage layout (per contract)
-----------------------------
- `b"\x01tl:min_delay"`     → u64 (blocks)
- `b"\x01tl:grace"`         → u64 (blocks)
- `b"\x01tl:admin"`         → 20/32-byte address (opaque bytes)
- `b"\x01tl:q:" + op_id`    → u64 ETA (big-endian 8 bytes)

Events (emitted via stdlib.events.emit)
---------------------------------------
- `TimelockQueued`    : {"op": bytes, "eta": int, "delay": int}
- `TimelockCancelled` : {"op": bytes}
- `TimelockExecuted`  : {"op": bytes}
- `TimelockParams`    : {"min_delay": int, "grace": int}
- `TimelockAdmin`     : {"admin": bytes}

Public API (call-level)
-----------------------
- compute_id(payload: bytes, salt: bytes=b"") -> bytes
- get_params() -> tuple[int, int]
- set_params(caller: bytes, *, min_delay: int | None = None, grace: int | None = None) -> None
- set_admin(caller: bytes, new_admin: bytes) -> None
- get_admin() -> bytes | None
- queue(caller: bytes, payload: bytes, delay_blocks: int, *, salt: bytes = b"", now_height: int | None = None) -> bytes
- is_queued(op_id: bytes) -> bool
- get_eta(op_id: bytes) -> int | None
- ready(op_id: bytes, *, now_height: int | None = None) -> bool
- cancel(caller: bytes, op_id: bytes) -> None
- execute_mark(caller: bytes, op_id: bytes, *, now_height: int | None = None) -> None
- require_ready(op_id: bytes, *, now_height: int | None = None) -> None

Usage pattern (within your contract)
------------------------------------
    from stdlib.control import timelock

    # Guarded parameter update: 2-step queue → execute.
    def admin_queue_set_fee(caller: bytes, new_fee_bps: int, salt: bytes) -> bytes:
        payload = b"SET_FEE|" + new_fee_bps.to_bytes(4, "big", signed=False)
        return timelock.queue(caller, payload, delay_blocks=20, salt=salt)

    def admin_execute_set_fee(caller: bytes, new_fee_bps: int, salt: bytes) -> None:
        payload = b"SET_FEE|" + new_fee_bps.to_bytes(4, "big", signed=False)
        op = timelock.compute_id(payload, salt)
        timelock.require_ready(op)
        # Now perform the action:
        _set_fee_internal(new_fee_bps)
        # Mark executed (deletes the queue entry, emits event):
        timelock.execute_mark(caller, op)

All functions are deterministic and pure with respect to the VM environment
(except expected storage/events/abi interactions).
"""
from __future__ import annotations

from typing import Final, Optional, Tuple

# ---- Late imports (to cooperate with VM import guard) -----------------------


def _abi():
    from stdlib import abi  # type: ignore

    return abi


def _storage():
    from stdlib import storage  # type: ignore

    return storage


def _events():
    from stdlib import events  # type: ignore

    return events


def _hash():
    from stdlib import hash as std_hash  # type: ignore

    return std_hash


def _roles_or_none():
    try:
        from stdlib.access import roles  # type: ignore

        return roles
    except Exception:
        return None


def _ownable_or_none():
    try:
        from stdlib.access import ownable  # type: ignore

        return ownable
    except Exception:
        return None


# ---- Constants & keys -------------------------------------------------------

_TAG: Final[bytes] = b"TIMELOCK/OP/v1"
_ADMIN_ROLE_NAME: Final[bytes] = b"TIMELOCK_ADMIN"
_KEY_MIN_DELAY: Final[bytes] = b"\x01tl:min_delay"
_KEY_GRACE: Final[bytes] = b"\x01tl:grace"
_KEY_ADMIN: Final[bytes] = b"\x01tl:admin"
_KEY_Q_PREFIX: Final[bytes] = b"\x01tl:q:"

# Sensible defaults if unset.
_DEFAULT_MIN_DELAY_BLOCKS: Final[int] = 20
_DEFAULT_GRACE_BLOCKS: Final[int] = 200


# ---- Int encoding helpers (u64 big-endian) ---------------------------------


def _u64_be(n: int) -> bytes:
    if n < 0:
        _abi().revert(b"TIMELOCK:NEGATIVE_U64")
    return int(n).to_bytes(8, "big", signed=False)


def _u64_from(b: Optional[bytes]) -> Optional[int]:
    if not b:
        return None
    if len(b) != 8:
        _abi().revert(b"TIMELOCK:BAD_U64_LEN")
    return int.from_bytes(b, "big", signed=False)


# ---- Height helpers ---------------------------------------------------------


def _read_height_from_env() -> Optional[int]:
    """
    Probe several likely names on abi for block height. Returns None if not found.
    """
    abi = _abi()
    for attr in ("block_height", "get_block_height", "block_number", "height"):
        try:
            v = getattr(abi, attr)
        except Exception:
            continue
        try:
            # Some expose method(), some expose property/int already.
            h = int(v() if callable(v) else v)  # type: ignore[arg-type]
            if h >= 0:
                return h
        except Exception:
            continue
    return None


def _resolve_now(now_height: Optional[int]) -> int:
    if now_height is not None:
        return int(now_height)
    h = _read_height_from_env()
    if h is None:
        _abi().revert(
            b"TIMELOCK:NO_HEIGHT"
        )  # Contract must pass now_height explicitly.
    return h


# ---- Admin guard ------------------------------------------------------------


def _admin_role_id() -> bytes:
    return _hash().sha3_256(b"ROLE|" + _ADMIN_ROLE_NAME)


def _get_admin_addr_storage() -> Optional[bytes]:
    return _storage().get(_KEY_ADMIN)


def _has_admin(caller: bytes) -> bool:
    roles = _roles_or_none()
    if roles is not None:
        try:
            return bool(roles.has_role(_admin_role_id(), caller))
        except Exception:
            pass
    ownable = _ownable_or_none()
    if ownable is not None:
        try:
            return bool(ownable.is_owner(caller))
        except Exception:
            pass
    admin = _get_admin_addr_storage()
    return admin is not None and admin == caller


def _require_admin(caller: bytes) -> None:
    if not _has_admin(caller):
        _abi().revert(b"TIMELOCK:NOT_ADMIN")


# ---- Public: identifiers & params ------------------------------------------


def compute_id(payload: bytes, salt: bytes = b"") -> bytes:
    """
    Compute deterministic op_id from payload and salt.
    """
    return _hash().sha3_256(_TAG + b"|" + payload + b"|" + salt)


def get_params() -> Tuple[int, int]:
    """
    Return (min_delay_blocks, grace_blocks).
    """
    s = _storage()
    md = _u64_from(s.get(_KEY_MIN_DELAY))
    gr = _u64_from(s.get(_KEY_GRACE))
    return (
        md if md is not None else _DEFAULT_MIN_DELAY_BLOCKS,
        gr if gr is not None else _DEFAULT_GRACE_BLOCKS,
    )


def set_params(
    caller: bytes,
    *,
    min_delay: Optional[int] = None,
    grace: Optional[int] = None,
) -> None:
    """
    Update timelock parameters (admin-only). Unspecified values remain unchanged.
    Emits TimelockParams(min_delay, grace).
    """
    _require_admin(caller)
    s = _storage()
    cur_md, cur_gr = get_params()
    new_md = cur_md if (min_delay is None) else int(min_delay)
    new_gr = cur_gr if (grace is None) else int(grace)
    if new_md < 0 or new_gr < 0:
        _abi().revert(b"TIMELOCK:NEGATIVE_PARAM")
    s.set(_KEY_MIN_DELAY, _u64_be(new_md))
    s.set(_KEY_GRACE, _u64_be(new_gr))
    _events().emit(b"TimelockParams", {b"min_delay": new_md, b"grace": new_gr})


def set_admin(caller: bytes, new_admin: bytes) -> None:
    """
    Set/replace explicit timelock admin (only if caller is current admin).
    Emits TimelockAdmin(admin).
    """
    # Note: if neither roles nor ownable are linked, the storage admin must be
    # initialized by the deployer calling set_admin first (no-op protection
    # for empty state: allow if storage admin is None AND caller == new_admin).
    current = _get_admin_addr_storage()
    if current is None:
        # Bootstrap: allow self-claim by first caller if roles/ownable not present.
        roles = _roles_or_none()
        ownable = _ownable_or_none()
        if roles is None and ownable is None and caller == new_admin:
            _storage().set(_KEY_ADMIN, new_admin)
            _events().emit(b"TimelockAdmin", {b"admin": new_admin})
            return
    # Normal path: must be admin.
    _require_admin(caller)
    _storage().set(_KEY_ADMIN, new_admin)
    _events().emit(b"TimelockAdmin", {b"admin": new_admin})


def get_admin() -> Optional[bytes]:
    """
    Return explicit admin address if set, else None (roles/ownable may still govern).
    """
    return _get_admin_addr_storage()


# ---- Queue keys -------------------------------------------------------------


def _qkey(op_id: bytes) -> bytes:
    return _KEY_Q_PREFIX + op_id


# ---- Public: queue / cancel / ready / execute -------------------------------


def queue(
    caller: bytes,
    payload: bytes,
    delay_blocks: int,
    *,
    salt: bytes = b"",
    now_height: Optional[int] = None,
) -> bytes:
    """
    Queue an operation for future execution. Returns op_id.

    - delay_blocks is clamped to at least min_delay.
    - Reverts if already queued.
    - Emits TimelockQueued(op, eta, delay)

    Admin is NOT strictly required to *queue* by default; you can implement
    an app-level policy where only admins call queue(). If you want to enforce
    admin-only queueing, call _require_admin(caller) before invoking queue().
    """
    op_id = compute_id(payload, salt)
    s = _storage()
    key = _qkey(op_id)
    if s.get(key) is not None:
        _abi().revert(b"TIMELOCK:ALREADY_QUEUED")

    min_delay, _ = get_params()
    now = _resolve_now(now_height)
    eff_delay = delay_blocks if delay_blocks >= min_delay else min_delay
    eta = now + int(eff_delay)
    s.set(key, _u64_be(eta))
    _events().emit(b"TimelockQueued", {b"op": op_id, b"eta": eta, b"delay": eff_delay})
    return op_id


def is_queued(op_id: bytes) -> bool:
    return _storage().get(_qkey(op_id)) is not None


def get_eta(op_id: bytes) -> Optional[int]:
    return _u64_from(_storage().get(_qkey(op_id)))


def ready(op_id: bytes, *, now_height: Optional[int] = None) -> bool:
    """
    True iff op is queued and now_height ∈ [eta, eta + grace].
    """
    eta = get_eta(op_id)
    if eta is None:
        return False
    now = _resolve_now(now_height)
    _, grace = get_params()
    return (now >= eta) and (now <= eta + grace)


def require_ready(op_id: bytes, *, now_height: Optional[int] = None) -> None:
    if not ready(op_id, now_height=now_height):
        _abi().revert(b"TIMELOCK:NOT_READY")


def cancel(caller: bytes, op_id: bytes) -> None:
    """
    Cancel a queued op (admin-only). Emits TimelockCancelled(op).
    """
    _require_admin(caller)
    key = _qkey(op_id)
    if _storage().get(key) is None:
        _abi().revert(b"TIMELOCK:NOT_QUEUED")
    _storage().delete(key)
    _events().emit(b"TimelockCancelled", {b"op": op_id})


def execute_mark(
    caller: bytes,
    op_id: bytes,
    *,
    now_height: Optional[int] = None,
) -> None:
    """
    Mark an op as executed (deletes queue entry). You must call this **after**
    performing the guarded operation in your contract.

    - Reverts if not queued or not within [eta, eta+grace].
    - Emits TimelockExecuted(op).
    """
    key = _qkey(op_id)
    if _storage().get(key) is None:
        _abi().revert(b"TIMELOCK:NOT_QUEUED")
    if not ready(op_id, now_height=now_height):
        # Distinguish "expired" vs "too early" can be helpful for UX, but we
        # preserve a single error to keep determinism/messages stable.
        _abi().revert(b"TIMELOCK:NOT_READY")
    _storage().delete(key)
    _events().emit(b"TimelockExecuted", {b"op": op_id})


# ---- Sugar: typed helpers for common patterns -------------------------------


def queue_call(
    caller: bytes,
    method_name: bytes,
    args_blob: bytes,
    delay_blocks: int,
    *,
    salt: bytes = b"",
    now_height: Optional[int] = None,
) -> bytes:
    """
    Convenience: derive payload from (method_name | args_blob) and queue.
    """
    payload = b"M|" + method_name + b"|" + args_blob
    return queue(caller, payload, delay_blocks, salt=salt, now_height=now_height)


def compute_call_id(
    method_name: bytes, args_blob: bytes, *, salt: bytes = b""
) -> bytes:
    """
    Convenience: deterministic id for a method+args pair.
    """
    return compute_id(b"M|" + method_name + b"|" + args_blob, salt)


# ---- Module end -------------------------------------------------------------
