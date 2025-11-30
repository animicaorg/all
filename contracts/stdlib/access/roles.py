# -*- coding: utf-8 -*-
"""
contracts.stdlib.access.roles
=============================

Deterministic, minimal **Role-Based Access Control** (RBAC) utilities for
Animica Python contracts.

Design goals
------------
- **Bytes32 role identifiers**: each role is identified by 32 bytes.
- **Deterministic storage**: canonical keys with explicit namespacing.
- **Owner-as-superadmin (optional)**: if `contracts.stdlib.access.ownable` is
  present and the caller is the contract owner, they are treated as holding the
  `DEFAULT_ADMIN_ROLE` for *all* roles.
- **Idempotent operations**: granting an existing role or revoking a missing
  role is a no-op (no revert), but emits events only on state change.
- **VM-safe imports**: uses only `stdlib` modules: `storage`, `events`, `abi`,
  and optionally `hash` for deriving role ids from names.

API surface
-----------
- **Constants**
    - `DEFAULT_ADMIN_ROLE`: bytes32 zero; default admin for all roles.
    - `ROLE_MEMBER_PREFIX`, `ROLE_ADMIN_PREFIX`: internal storage prefixes.

- **Helpers**
    - `derive_role_id(name: bytes) -> bytes`: bytes32 = sha3_256(name).
    - `normalize_role(role: bytes) -> bytes`: ensures 32-byte role id.

- **Queries**
    - `has_role(role: bytes, account: bytes) -> bool`
    - `get_role_admin(role: bytes) -> bytes`
    - `is_admin_for_role(role: bytes, caller: bytes) -> bool`
    - `require_role(role: bytes, caller: bytes) -> None` (reverts if missing)

- **Mutations**
    - `grant_role(caller: bytes, role: bytes, account: bytes) -> None`
    - `revoke_role(caller: bytes, role: bytes, account: bytes) -> None`
    - `renounce_role(caller: bytes, role: bytes) -> None`
    - `set_role_admin(caller: bytes, role: bytes, admin_role: bytes) -> None`

Events
------
- **RoleGranted**      : {"role": bytes, "account": bytes, "sender": bytes}
- **RoleRevoked**      : {"role": bytes, "account": bytes, "sender": bytes}
- **RoleAdminChanged** : {"role": bytes, "previousAdminRole": bytes, "newAdminRole": bytes}

Storage layout
--------------
- Member flag:  key = b"access:role:member:" + role + b":" + account  → b"1"
- Admin-of-role: key = b"access:role:admin:"  + role                  → bytes32 role-id
  If absent, falls back to `DEFAULT_ADMIN_ROLE`.

Notes
-----
- Addresses are `bytes`. The module does not impose a specific length; callers
  should consistently pass their normalized address representation.
- Role ids **must be exactly 32 bytes**; otherwise `normalize_role` triggers a
  deterministic revert `ACCESS:ROLE_LEN`.
"""
from __future__ import annotations

from typing import Optional

__all__ = [
    "DEFAULT_ADMIN_ROLE",
    "ROLE_MEMBER_PREFIX",
    "ROLE_ADMIN_PREFIX",
    "derive_role_id",
    "normalize_role",
    "has_role",
    "get_role_admin",
    "is_admin_for_role",
    "require_role",
    "grant_role",
    "revoke_role",
    "renounce_role",
    "set_role_admin",
]

# ---- Constants & prefixes ----------------------------------------------------

DEFAULT_ADMIN_ROLE: bytes = b"\x00" * 32

# Canonical prefixes (keep in sync across stdlib access utilities if copied)
ROLE_MEMBER_PREFIX: bytes = b"access:role:member:"
ROLE_ADMIN_PREFIX: bytes = b"access:role:admin:"


# ---- Lazy stdlib accessors (VM import guard friendly) ------------------------


def _std_storage():
    from stdlib import storage  # type: ignore

    return storage


def _std_events():
    from stdlib import events  # type: ignore

    return events


def _std_abi():
    from stdlib import abi  # type: ignore

    return abi


def _std_hash_or_none():
    try:
        from stdlib import hash as _h  # type: ignore

        return _h
    except Exception:
        return None


# ---- Optional owner superadmin bridge ---------------------------------------


def _owner_is(caller: bytes) -> bool:
    """
    Returns True if contracts.stdlib.access.ownable.get_owner() exists
    and equals `caller`. Fails closed (False) if ownable is absent.
    """
    try:
        from . import ownable as _own  # type: ignore

        owner = _own.get_owner()
        return owner is not None and owner == caller
    except Exception:
        return False


# ---- Internal key helpers ----------------------------------------------------


def _key_member(role: bytes, account: bytes) -> bytes:
    return ROLE_MEMBER_PREFIX + role + b":" + account


def _key_admin(role: bytes) -> bytes:
    return ROLE_ADMIN_PREFIX + role


def _set_flag(key: bytes) -> None:
    _std_storage().set(key, b"1")


def _clear_key(key: bytes) -> None:
    _std_storage().set(key, b"")


# ---- Role id helpers ---------------------------------------------------------


def normalize_role(role: bytes) -> bytes:
    """
    Ensure `role` is exactly 32 bytes; otherwise revert.
    """
    if not isinstance(role, (bytes, bytearray)) or len(role) != 32:
        _std_abi().revert(b"ACCESS:ROLE_LEN")
    # Ensure immutable bytes
    return bytes(role)


def derive_role_id(name: bytes) -> bytes:
    """
    Deterministic role id derivation: sha3_256(name) → bytes32.
    Safe even if `stdlib.hash` is not present (then revert explicitly).
    """
    h = _std_hash_or_none()
    if h is None:
        _std_abi().revert(b"ACCESS:HASH_UNAVAILABLE")
    return h.sha3_256(name)


# ---- Queries ----------------------------------------------------------------


def has_role(role: bytes, account: bytes) -> bool:
    role = normalize_role(role)
    if account is None or len(account) == 0:
        return False
    v = _std_storage().get(_key_member(role, account))
    return v is not None and len(v) > 0


def get_role_admin(role: bytes) -> bytes:
    """
    Returns the admin role-id for `role`, or DEFAULT_ADMIN_ROLE if unset.
    """
    role = normalize_role(role)
    v = _std_storage().get(_key_admin(role))
    if v is None or len(v) == 0:
        return DEFAULT_ADMIN_ROLE
    # Persisted admin must also be 32 bytes; if not, treat as default to be safe.
    return v if len(v) == 32 else DEFAULT_ADMIN_ROLE


def is_admin_for_role(role: bytes, caller: bytes) -> bool:
    """
    True if `caller` holds `get_role_admin(role)` or is the (optional) owner.
    """
    admin_role = get_role_admin(role)
    if has_role(admin_role, caller):
        return True
    # Optional: owner acts as superadmin
    return _owner_is(caller)


def require_role(role: bytes, caller: bytes) -> None:
    """
    Revert unless `caller` has `role`.
    """
    if not has_role(role, caller):
        _std_abi().revert(b"ACCESS:MISSING_ROLE")


# ---- Mutations ---------------------------------------------------------------


def grant_role(caller: bytes, role: bytes, account: bytes) -> None:
    """
    Grant `role` to `account`. Only callable by an admin of `role`.
    Idempotent if already a member.

    Emits RoleGranted on first grant.
    """
    abi = _std_abi()
    ev = _std_events()
    role = normalize_role(role)

    if account is None or len(account) == 0:
        abi.revert(b"ACCESS:ACCOUNT_EMPTY")

    if not is_admin_for_role(role, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")

    key = _key_member(role, account)
    if has_role(role, account):
        return  # idempotent
    _set_flag(key)
    ev.emit(b"RoleGranted", {"role": role, "account": account, "sender": caller})


def revoke_role(caller: bytes, role: bytes, account: bytes) -> None:
    """
    Revoke `role` from `account`. Only callable by an admin of `role`.
    Idempotent if not a member.

    Emits RoleRevoked on successful state change.
    """
    abi = _std_abi()
    ev = _std_events()
    role = normalize_role(role)

    if account is None or len(account) == 0:
        abi.revert(b"ACCESS:ACCOUNT_EMPTY")

    if not is_admin_for_role(role, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")

    key = _key_member(role, account)
    if not has_role(role, account):
        return  # idempotent
    _clear_key(key)
    ev.emit(b"RoleRevoked", {"role": role, "account": account, "sender": caller})


def renounce_role(caller: bytes, role: bytes) -> None:
    """
    Caller removes themself from `role`. Idempotent if not a member.

    Emits RoleRevoked on successful state change.
    """
    ev = _std_events()
    role = normalize_role(role)
    key = _key_member(role, caller)
    if not has_role(role, caller):
        return
    _clear_key(key)
    ev.emit(b"RoleRevoked", {"role": role, "account": caller, "sender": caller})


def set_role_admin(caller: bytes, role: bytes, admin_role: bytes) -> None:
    """
    Set the admin role-id for `role` to `admin_role`.

    Only callable by *current* admin of `role` (or owner via superadmin bridge).
    Emits RoleAdminChanged on change. Idempotent if unchanged.
    """
    abi = _std_abi()
    ev = _std_events()

    role = normalize_role(role)
    admin_role = normalize_role(admin_role)

    if not is_admin_for_role(role, caller):
        abi.revert(b"ACCESS:NOT_ROLE_ADMIN")

    prev = get_role_admin(role)
    if prev == admin_role:
        return  # idempotent

    _std_storage().set(_key_admin(role), admin_role)
    ev.emit(
        b"RoleAdminChanged",
        {"role": role, "previousAdminRole": prev, "newAdminRole": admin_role},
    )
