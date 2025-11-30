# -*- coding: utf-8 -*-
"""
contracts.stdlib.access.ownable
================================

Minimal, deterministic **Ownable** helper for Animica Python contracts.

This module provides a focused owner storage and control surface:
- read the current owner (`get_owner`)
- initialize the owner once (`init_owner`)
- check that a caller is the owner (`require_owner`)
- transfer ownership to a new account (`transfer_ownership`)
- renounce ownership (clear owner) (`renounce_ownership`)

It is intentionally small and VM-safe: only the sanctioned stdlib modules
(`storage`, `events`, `abi`) are used; there is no time, randomness, or I/O.

Conventions
-----------
- Addresses are `bytes` (callers should pass normalized 32-byte or ABI-encoded
  addresses consistently across the contract).
- The owner value is stored at a deterministic key shared with
  `contracts.stdlib.access` (`OWNER_KEY = b"access:owner"`).
- Events:
    - "OwnershipTransferred" args: {"previous": bytes, "new": bytes}

Typical usage
-------------
    from contracts.stdlib.access.ownable import (
        init_owner, get_owner, require_owner,
        transfer_ownership, renounce_ownership,
    )

    def init(caller: bytes) -> None:
        init_owner(caller)

    def admin_only(caller: bytes) -> None:
        require_owner(caller)
        # ... privileged logic ...

Safety notes
------------
- `init_owner` is idempotent and will not overwrite a previously set owner.
- `transfer_ownership` rejects an empty `new_owner` — use `renounce_ownership`
  explicitly to leave the contract without an owner.
"""
from __future__ import annotations

from typing import Optional

# Reuse the canonical key from the package so we don't accidentally drift.
try:
    # Local import; avoids import-cycles because this submodule is leaf-only.
    from . import OWNER_KEY  # type: ignore
except Exception:  # pragma: no cover - defensive fallback if imported standalone
    OWNER_KEY: bytes = b"access:owner"  # keep in sync with contracts.stdlib.access

__all__ = [
    "OWNER_KEY",
    "get_owner",
    "init_owner",
    "require_owner",
    "transfer_ownership",
    "renounce_ownership",
]

# --- Internal stdlib accessors (lazy to satisfy VM import guard) --------------


def _std_storage():
    from stdlib import storage  # type: ignore

    return storage


def _std_events():
    from stdlib import events  # type: ignore

    return events


def _std_abi():
    from stdlib import abi  # type: ignore

    return abi


# --- Owner primitives ---------------------------------------------------------


def get_owner() -> Optional[bytes]:
    """
    Return the current owner address, or None if not set.
    """
    s = _std_storage()
    v = s.get(OWNER_KEY)
    return v if v is not None and len(v) > 0 else None


def init_owner(owner: bytes) -> None:
    """
    Initialize the contract owner. Idempotent: does not overwrite if already set.

    Recommended to call during contract deployment or first initialization step.
    """
    s = _std_storage()
    current = s.get(OWNER_KEY)
    if current is None or len(current) == 0:
        s.set(OWNER_KEY, owner)


def require_owner(caller: bytes) -> None:
    """
    Revert unless `caller` equals the current owner.
    """
    abi = _std_abi()
    owner = get_owner()
    if owner is None or owner != caller:
        abi.revert(b"ACCESS:NOT_OWNER")


def transfer_ownership(caller: bytes, new_owner: bytes) -> None:
    """
    Owner-only: transfer ownership to `new_owner` (must be non-empty).

    Emits:
        - "OwnershipTransferred" with {"previous": <old>, "new": <new_owner>}
    """
    abi = _std_abi()
    ev = _std_events()
    s = _std_storage()

    require_owner(caller)

    if new_owner is None or len(new_owner) == 0:
        abi.revert(b"ACCESS:NEW_OWNER_EMPTY")

    previous = get_owner() or b""
    s.set(OWNER_KEY, new_owner)
    ev.emit(b"OwnershipTransferred", {"previous": previous, "new": new_owner})


def renounce_ownership(caller: bytes) -> None:
    """
    Owner-only: renounce ownership (sets owner to empty bytes).

    After renounce, `require_owner` will always fail until an initialization or
    migration path explicitly sets a new owner (not usually recommended).
    Emits:
        - "OwnershipTransferred" with {"previous": <old>, "new": b""}
    """
    ev = _std_events()
    s = _std_storage()

    require_owner(caller)

    previous = get_owner() or b""
    s.set(OWNER_KEY, b"")
    ev.emit(b"OwnershipTransferred", {"previous": previous, "new": b""})
