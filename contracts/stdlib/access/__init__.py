# -*- coding: utf-8 -*-
"""
contracts.stdlib.access
=======================

Deterministic, VM-safe access-control helpers for Animica Python contracts.

This tiny library provides **owner**, **role-based access control (RBAC)**,
and **pausable** primitives designed to be imported and used directly inside
contracts written for the Animica Python VM. It only uses the sanctioned
VM stdlib modules (`storage`, `events`, `abi`) and simple byte operations.

Design goals
------------
- **Deterministic only**: No clock, no randomness, no external I/O.
- **Byte-first**: Addresses and role ids are `bytes`. (Use 32-byte or bech32→bytes
  per your contract’s interface.)
- **Explicit caller**: Helpers take `caller: bytes` so you can plumb the value
  from your contract’s entrypoint. (In many patterns this will be the tx sender.)
- **Composable**: Owner + RBAC + Pausable can be combined without state/key clashes.

Storage layout (by convention)
------------------------------
- Owner:
    key `b"access:owner"` → `bytes` (address) or empty for unset during deploy.
- Pausable:
    key `b"access:paused"` → `b"\x01"` or absent (treated as unpaused).
- RBAC membership:
    key `b"access:role:\x00" + role + b"\x00" + addr` → `b"\x01"` if member.
- RBAC admin role for a role:
    key `b"access:role_admin:\x00" + role` → `bytes` role-id (defaults to DEFAULT_ADMIN_ROLE).

These keys are deterministic byte strings; *do not* change them after deploy.

Events (convention)
-------------------
- "OwnershipTransferred" args: {"previous": bytes, "new": bytes}
- "RoleGranted"        args: {"role": bytes, "account": bytes, "sender": bytes}
- "RoleRevoked"        args: {"role": bytes, "account": bytes, "sender": bytes}
- "Paused"             args: {"account": bytes}
- "Unpaused"           args: {"account": bytes}

Quick usage (inside a contract)
-------------------------------
    from stdlib import abi
    from contracts.stdlib.access import (
        init_owner, require_owner, transfer_ownership,
        DEFAULT_ADMIN_ROLE, has_role, grant_role, revoke_role,
        require_role, set_paused, require_not_paused
    )

    def init(caller: bytes) -> None:
        # set initial owner and give admin role to owner
        init_owner(caller)
        grant_role(caller, DEFAULT_ADMIN_ROLE, caller)

    def critical(caller: bytes) -> None:
        require_not_paused()
        require_owner(caller)  # or: require_role(caller, ADMIN_ROLE)
        # ... critical logic ...

"""
from __future__ import annotations

from typing import Optional

# Public symbols
__all__ = [
    # constants
    "OWNER_KEY",
    "PAUSED_KEY",
    "DEFAULT_ADMIN_ROLE",
    # owner API
    "get_owner",
    "init_owner",
    "require_owner",
    "transfer_ownership",
    "renounce_ownership",
    # pause API
    "is_paused",
    "require_not_paused",
    "set_paused",
    # roles API
    "has_role",
    "require_role",
    "grant_role",
    "revoke_role",
    "get_role_admin",
    "set_role_admin",
]

# ---- Constants & key helpers -------------------------------------------------

# Fixed storage keys (namespaced)
OWNER_KEY: bytes = b"access:owner"
PAUSED_KEY: bytes = b"access:paused"

# Convention: the default admin role id (32 bytes recommended, but any bytes accepted).
DEFAULT_ADMIN_ROLE: bytes = b"ACCESS:DEFAULT_ADMIN_ROLE"


def _role_member_key(role: bytes, account: bytes) -> bytes:
    # key = "access:role:" 0x00 role 0x00 account
    return b"access:role:\x00" + role + b"\x00" + account


def _role_admin_key(role: bytes) -> bytes:
    # key = "access:role_admin:" 0x00 role
    return b"access:role_admin:\x00" + role


# ---- Safe stdlib import wrappers (lazy) --------------------------------------


def _std_storage():
    # Imported lazily to play nicely with the VM import guard.
    from stdlib import storage  # type: ignore

    return storage


def _std_events():
    from stdlib import events  # type: ignore

    return events


def _std_abi():
    from stdlib import abi  # type: ignore

    return abi


# ---- Owner helpers -----------------------------------------------------------


def get_owner() -> Optional[bytes]:
    """
    Return the current owner address or None if not set.
    """
    s = _std_storage()
    v = s.get(OWNER_KEY)
    return v if v is not None and len(v) > 0 else None


def init_owner(owner: bytes) -> None:
    """
    Set the owner during deployment/initialization.
    Will **not** overwrite if already set (idempotent).
    """
    s = _std_storage()
    cur = s.get(OWNER_KEY)
    if cur is None or len(cur) == 0:
        s.set(OWNER_KEY, owner)


def require_owner(caller: bytes) -> None:
    """
    Revert if `caller` is not the current owner.
    """
    abi = _std_abi()
    owner = get_owner()
    if owner is None or owner != caller:
        abi.revert(b"ACCESS:NOT_OWNER")


def transfer_ownership(caller: bytes, new_owner: bytes) -> None:
    """
    Owner-only: transfer ownership to `new_owner`.
    Emits OwnershipTransferred(previous, new).
    """
    require_owner(caller)
    s = _std_storage()
    ev = _std_events()
    prev = get_owner() or b""
    s.set(OWNER_KEY, new_owner)
    ev.emit(b"OwnershipTransferred", {"previous": prev, "new": new_owner})


def renounce_ownership(caller: bytes) -> None:
    """
    Owner-only: clear ownership (leaves contract without an owner).
    """
    require_owner(caller)
    s = _std_storage()
    ev = _std_events()
    prev = get_owner() or b""
    s.set(OWNER_KEY, b"")
    ev.emit(b"OwnershipTransferred", {"previous": prev, "new": b""})


# ---- Pausable helpers --------------------------------------------------------


def is_paused() -> bool:
    """
    Return True if the contract is paused.
    """
    s = _std_storage()
    v = s.get(PAUSED_KEY)
    return v == b"\x01"


def require_not_paused() -> None:
    """
    Revert if paused.
    """
    abi = _std_abi()
    if is_paused():
        abi.revert(b"ACCESS:PAUSED")


def set_paused(caller: bytes, paused: bool) -> None:
    """
    Pause/unpause the contract.
    Authorization strategy: caller must be owner OR have DEFAULT_ADMIN_ROLE.
    Emits Paused/Unpaused.
    """
    abi = _std_abi()
    s = _std_storage()
    ev = _std_events()

    # Authorization: owner or admin role
    owner = get_owner()
    if not (owner is not None and owner == caller) and not has_role(
        DEFAULT_ADMIN_ROLE, caller
    ):
        abi.revert(b"ACCESS:PAUSE_FORBIDDEN")

    target = b"\x01" if paused else b""
    s.set(PAUSED_KEY, target)
    ev.emit(b"Paused" if paused else b"Unpaused", {"account": caller})


# ---- Role helpers ------------------------------------------------------------


def has_role(role: bytes, account: bytes) -> bool:
    """
    Return True if `account` is a member of `role`.
    """
    s = _std_storage()
    k = _role_member_key(role, account)
    return s.get(k) == b"\x01"


def require_role(account: bytes, role: bytes) -> None:
    """
    Revert unless `account` is a member of `role`.
    """
    abi = _std_abi()
    if not has_role(role, account):
        abi.revert(b"ACCESS:MISSING_ROLE")


def get_role_admin(role: bytes) -> bytes:
    """
    Read the admin role for `role` (who may grant/revoke it).
    Defaults to DEFAULT_ADMIN_ROLE if unset.
    """
    s = _std_storage()
    v = s.get(_role_admin_key(role))
    return v if v is not None and len(v) > 0 else DEFAULT_ADMIN_ROLE


def set_role_admin(caller: bytes, role: bytes, admin_role: bytes) -> None:
    """
    Set the admin role for `role`. Only callable by current admin of `role`.
    """
    abi = _std_abi()
    s = _std_storage()
    current_admin = get_role_admin(role)
    if not has_role(current_admin, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")
    s.set(_role_admin_key(role), admin_role)


def grant_role(caller: bytes, role: bytes, account: bytes) -> None:
    """
    Grant `role` to `account`. Caller must hold the admin role of `role`.
    Emits RoleGranted(role, account, sender).
    """
    abi = _std_abi()
    s = _std_storage()
    ev = _std_events()

    admin_role = get_role_admin(role)
    if not has_role(admin_role, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")

    k = _role_member_key(role, account)
    if s.get(k) != b"\x01":
        s.set(k, b"\x01")
        ev.emit(b"RoleGranted", {"role": role, "account": account, "sender": caller})


def revoke_role(caller: bytes, role: bytes, account: bytes) -> None:
    """
    Revoke `role` from `account`. Caller must hold the admin role of `role`.
    Emits RoleRevoked(role, account, sender).
    """
    abi = _std_abi()
    s = _std_storage()
    ev = _std_events()

    admin_role = get_role_admin(role)
    if not has_role(admin_role, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")

    k = _role_member_key(role, account)
    if s.get(k) == b"\x01":
        s.set(k, b"")  # clear
        ev.emit(b"RoleRevoked", {"role": role, "account": account, "sender": caller})
