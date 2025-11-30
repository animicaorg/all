# -*- coding: utf-8 -*-
"""
contracts.stdlib.control
========================

Deterministic, storage-backed control primitives for Animica Python contracts.

This module provides three small, composable building blocks:

1) **Pausable**
   - `is_paused() -> bool`
   - `require_not_paused() -> None`
   - `pause(caller: bytes) -> None`
   - `unpause(caller: bytes) -> None`
   - `get_pauser_role() -> bytes` (bytes32 role id, deterministic)

   Pausing requires the **Pauser** role (bytes32 id derived from the name
   "PAUSER_ROLE") or contract **owner** if `stdlib.access.ownable` is present.
   Emits `Paused` and `Unpaused` events on state change (idempotent otherwise).

2) **Reentrancy Guard**
   - `guard_enter(scope: bytes = b"default") -> None`
   - `guard_exit(scope: bytes = b"default") -> None`
   - `require_not_entered(scope: bytes = b"default") -> None`

   A lightweight non-reentrancy latch keyed by a *scope* tag. Typical pattern:

       control.guard_enter(b"inc")
       try:
           # critical section
           ...
       finally:
           control.guard_exit(b"inc")

   If a guard is already entered for a scope, the next `guard_enter` reverts
   with `CONTROL:REENTRANT`.

3) **Initialize-Once Flag**
   - `initialize_once(flag: bytes = b"default") -> None`
   - `is_initialized(flag: bytes = b"default") -> bool`

   Use during deploy/constructor-like flows to protect against multiple
   initialization attempts (reverts with `CONTROL:ALREADY_INIT`).

All functions only use VM-safe `stdlib` modules: `storage`, `events`, and `abi`.
Integration with `access.roles` and `access.ownable` is **optional** and fails
closed (no special privileges) if those modules are not present.

Storage Layout
--------------
- Paused flag:
    key = b"control:paused"                                → b"1" or empty
- Reentrancy latch:
    key = b"control:reentrancy:" + scope                   → b"1" or empty
- Initialize-once flag:
    key = b"control:init:" + flag                          → b"1" or empty

Events
------
- `Paused`   : {"sender": bytes}
- `Unpaused` : {"sender": bytes}

Notes
-----
- Addresses are opaque `bytes`; pass your normalized address format consistently.
- Role identifiers are 32 bytes; see `access.roles.derive_role_id`.
"""
from __future__ import annotations

from typing import Optional

__all__ = [
    # Pausable
    "is_paused",
    "require_not_paused",
    "pause",
    "unpause",
    "get_pauser_role",
    # Reentrancy Guard
    "guard_enter",
    "guard_exit",
    "require_not_entered",
    # Initialize-once
    "initialize_once",
    "is_initialized",
]

# ---- Canonical storage keys / prefixes --------------------------------------

_PAUSED_KEY: bytes = b"control:paused"
_REENT_PREFIX: bytes = b"control:reentrancy:"
_INIT_PREFIX: bytes = b"control:init:"


# ---- Lazy stdlib accessors ---------------------------------------------------


def _std_storage():
    from stdlib import storage  # type: ignore

    return storage


def _std_events():
    from stdlib import events  # type: ignore

    return events


def _std_abi():
    from stdlib import abi  # type: ignore

    return abi


def _std_hash_optional():
    try:
        from stdlib import hash as _h  # type: ignore

        return _h
    except Exception:
        return None


# ---- Optional access/ownable integration ------------------------------------


def _roles_optional():
    try:
        # Prefer same-package import to avoid global name clashes
        from ..access import roles as _roles  # type: ignore

        return _roles
    except Exception:
        return None


def _owner_optional() -> Optional[bytes]:
    """
    Returns owner address if ownable present, else None.
    """
    try:
        from ..access import ownable as _own  # type: ignore

        return _own.get_owner()
    except Exception:
        return None


# ---- Helpers ----------------------------------------------------------------


def _set_flag(key: bytes) -> None:
    _std_storage().set(key, b"1")


def _clear_flag(key: bytes) -> None:
    _std_storage().set(key, b"")


def _has_flag(key: bytes) -> bool:
    v = _std_storage().get(key)
    return v is not None and len(v) > 0


# ---- Pausable ---------------------------------------------------------------


def get_pauser_role() -> bytes:
    """
    Deterministically derive the Pauser role id (bytes32) as sha3_256("PAUSER_ROLE").

    If `stdlib.hash` is absent (unexpected in standard VM), revert to avoid
    inconsistent role ids across nodes.
    """
    roles = _roles_optional()
    if roles is None:
        h = _std_hash_optional()
        if h is None:
            _std_abi().revert(b"CONTROL:HASH_UNAVAILABLE")
        return h.sha3_256(b"PAUSER_ROLE")
    # Prefer roles.derive_role_id to remain consistent with stdlib.access
    return roles.derive_role_id(b"PAUSER_ROLE")


def _is_pauser_or_owner(caller: bytes) -> bool:
    roles = _roles_optional()
    if roles is not None:
        if roles.has_role(get_pauser_role(), caller):
            return True
    owner = _owner_optional()
    return owner is not None and owner == caller


def is_paused() -> bool:
    return _has_flag(_PAUSED_KEY)


def require_not_paused() -> None:
    if is_paused():
        _std_abi().revert(b"CONTROL:PAUSED")


def pause(caller: bytes) -> None:
    """
    Set the global paused flag. Requires Pauser role or Owner (if available).
    Idempotent if already paused.
    """
    if not _is_pauser_or_owner(caller):
        _std_abi().revert(b"CONTROL:NOT_PAUSER")

    if is_paused():
        return
    _set_flag(_PAUSED_KEY)
    _std_events().emit(b"Paused", {"sender": caller})


def unpause(caller: bytes) -> None:
    """
    Clear the global paused flag. Requires Pauser role or Owner (if available).
    Idempotent if already unpaused.
    """
    if not _is_pauser_or_owner(caller):
        _std_abi().revert(b"CONTROL:NOT_PAUSER")

    if not is_paused():
        return
    _clear_flag(_PAUSED_KEY)
    _std_events().emit(b"Unpaused", {"sender": caller})


# ---- Reentrancy Guard -------------------------------------------------------


def _guard_key(scope: bytes) -> bytes:
    # Scope is an arbitrary (short) bytes tag. Empty is allowed but discouraged.
    return _REENT_PREFIX + scope


def require_not_entered(scope: bytes = b"default") -> None:
    """
    Revert if the guard for `scope` is already entered.
    """
    if _has_flag(_guard_key(scope)):
        _std_abi().revert(b"CONTROL:REENTRANT")


def guard_enter(scope: bytes = b"default") -> None:
    """
    Enter a non-reentrant section for `scope`. Reverts if already entered.
    """
    key = _guard_key(scope)
    if _has_flag(key):
        _std_abi().revert(b"CONTROL:REENTRANT")
    _set_flag(key)


def guard_exit(scope: bytes = b"default") -> None:
    """
    Exit a non-reentrant section for `scope`. Idempotent.
    """
    _clear_flag(_guard_key(scope))


# ---- Initialize-once --------------------------------------------------------


def is_initialized(flag: bytes = b"default") -> bool:
    return _has_flag(_INIT_PREFIX + flag)


def initialize_once(flag: bytes = b"default") -> None:
    """
    Ensure single-time initialization for a given `flag` key. Reverts if already initialized.
    """
    key = _INIT_PREFIX + flag
    if _has_flag(key):
        _std_abi().revert(b"CONTROL:ALREADY_INIT")
    _set_flag(key)
