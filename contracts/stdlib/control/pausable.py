# -*- coding: utf-8 -*-
"""
contracts.stdlib.control.pausable
=================================

Global pause switch helpers for Animica Python contracts.

This module provides a thin, explicit facade over the core "Pausable" helpers
exposed by ``contracts.stdlib.control``. It is intended for contracts that want
a dedicated import path (``from stdlib.control import pausable``) and a couple
of extra convenience guards.

Key Points
----------
- The paused flag is **global to the contract** (single boolean).
- Changing pause state requires either:
  * the **Pauser** role (bytes32) derived deterministically from the name
    ``b"PAUSER_ROLE"``; or
  * the **Owner** (if ``stdlib.access.ownable`` is linked).
- Emitted Events (on change only):
  * ``Paused``   : {"sender": bytes}
  * ``Unpaused`` : {"sender": bytes}
- Deterministic behavior only; depends solely on contract storage and inputs.

Public API
----------
- ``is_paused() -> bool``: read current flag
- ``require_not_paused() -> None``: revert if paused
- ``require_paused() -> None``: revert if NOT paused
- ``pause(caller: bytes) -> None``: set paused (requires Pauser/Owner)
- ``unpause(caller: bytes) -> None``: clear paused (requires Pauser/Owner)
- ``set_paused(caller: bytes, flag: bool) -> None``: idempotent toggle
- ``get_pauser_role() -> bytes``: deterministic role id (bytes32)

See also: ``contracts.stdlib.control`` for lower-level primitives and details.

Usage
-----
    from stdlib.control import pausable

    def inc(caller: bytes) -> None:
        pausable.require_not_paused()
        # ... critical mutation ...

    def admin_pause(caller: bytes) -> None:
        pausable.pause(caller)

    def admin_unpause(caller: bytes) -> None:
        pausable.unpause(caller)
"""
from __future__ import annotations

from typing import Final

# Re-use the canonical implementations from the control package.
# We intentionally *re-export* a stable, minimal surface here.
from . import get_pauser_role as _get_pauser_role_core
from . import \
    is_paused as _is_paused_core  # noqa: F401 (re-exported via __all__)
from . import pause as _pause_core
from . import require_not_paused as _require_not_paused_core
from . import unpause as _unpause_core

__all__ = [
    "PAUSER_ROLE_NAME",
    "get_pauser_role",
    "is_paused",
    "require_not_paused",
    "require_paused",
    "pause",
    "unpause",
    "set_paused",
]

# ---- Constants ---------------------------------------------------------------

PAUSER_ROLE_NAME: Final[bytes] = b"PAUSER_ROLE"


# ---- Local stdlib accessors --------------------------------------------------


def _abi():
    # Import-late to cooperate with the VM's sandbox/import guard.
    from stdlib import abi  # type: ignore

    return abi


# ---- Re-exports / thin wrappers ---------------------------------------------


def get_pauser_role() -> bytes:
    """
    Return deterministic bytes32 role id for the Pauser role.
    """
    return _get_pauser_role_core()


def is_paused() -> bool:
    """
    Read global pause flag.
    """
    return _is_paused_core()


def require_not_paused() -> None:
    """
    Revert with b"CONTROL:PAUSED" if the contract is paused.
    """
    _require_not_paused_core()


def require_paused() -> None:
    """
    Revert with b"CONTROL:NOT_PAUSED" if the contract is NOT paused.
    """
    if not _is_paused_core():
        _abi().revert(b"CONTROL:NOT_PAUSED")


def pause(caller: bytes) -> None:
    """
    Set the global pause flag.

    Authorization: caller must have Pauser role or be Owner (if Ownable linked).
    Idempotent: no-op if already paused.
    Emits: Paused(sender)
    """
    _pause_core(caller)


def unpause(caller: bytes) -> None:
    """
    Clear the global pause flag.

    Authorization: caller must have Pauser role or be Owner (if Ownable linked).
    Idempotent: no-op if already unpaused.
    Emits: Unpaused(sender)
    """
    _unpause_core(caller)


def set_paused(caller: bytes, flag: bool) -> None:
    """
    Idempotently set pause state to `flag`. Uses authorization of `pause`/`unpause`.
    """
    cur = _is_paused_core()
    if flag and not cur:
        _pause_core(caller)
    elif not flag and cur:
        _unpause_core(caller)
    # else: already desired state → no-op
