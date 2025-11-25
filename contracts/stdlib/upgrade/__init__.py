# -*- coding: utf-8 -*-
"""
contracts.stdlib.upgrade
========================

Minimal, deterministic helpers for **upgradeable contracts**.

This module provides a tiny, opinionated surface to store and read an
implementation pointer (proxy-style) and to emit consistent upgrade events.
It **does not** perform authorization. Callers MUST enforce policy (e.g.
Ownable/roles) in their contracts *before* invoking mutators here.

Design notes
------------
- Single storage key for implementation: `b"upg:impl"`.
- Implementation identifier is an **opaque bytes string**. Projects commonly
  store one of:
    * the code hash (keccak256) of the implementation,
    * an address where the implementation code lives,
    * a content-addressed artifact id (e.g., DA commitment).
  This library only enforces **non-empty**.
- Deterministic events are emitted for observability:
    - `b"UPG:ImplementationSet" {old, new}`
- A **proxiable UUID** is exposed (UUPS-like), derived from a namespace tag.
  Implementations may expose/compare this to ensure compatibility.

Typical usage
-------------
    from stdlib import abi
    from contracts.stdlib.access.ownable import only_owner
    from contracts.stdlib.upgrade import (
        implementation,
        set_implementation,
        proxiable_uuid,
        UPGRADE_NAMESPACE,
    )

    def upgrade(caller: bytes, new_impl: bytes) -> None:
        # Authorization handled by the calling contract:
        only_owner(caller)  # or abi.require(caller == owner(), b"ONLY_OWNER")
        # Optional: enforce a specific UUID / compatibility check out-of-band
        set_implementation(new_impl)

    def impl() -> bytes:
        return implementation()

Security guidance
-----------------
- Always authenticate upgrades (owner/multisig/roles/timelock).
- Consider **two-step** upgrades (propose → execute) for high-value contracts.
- If storing an *address* here, ensure the runtime/dispatcher uses it safely
  (no dynamic imports; deterministic behavior only).
- Consider storing a **code hash** and verifying it matches the on-chain code
  or artifact manifest during deployment and on first use.

"""
from __future__ import annotations

from typing import Final

from stdlib import abi, events  # type: ignore
from stdlib import storage      # type: ignore
from stdlib import hash as _hash  # type: ignore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Namespace tag used to derive a stable proxiable UUID (UUPS-like).
UPGRADE_NAMESPACE: Final[bytes] = b"animica/upgrade/v1"

#: Canonical storage key for the implementation pointer.
_IMPL_KEY: Final[bytes] = b"upg:impl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def proxiable_uuid() -> bytes:
    """
    Return a 32-byte UUID derived from :data:`UPGRADE_NAMESPACE`.

    Contracts may compare this value against an implementation's reported UUID
    to ensure compatibility before switching implementations.
    """
    return _hash.keccak256(UPGRADE_NAMESPACE)

def implementation() -> bytes:
    """
    Read the current implementation identifier (opaque bytes).

    Returns
    -------
    bytes
        The stored value; empty bytes if unset.
    """
    return storage.get(__IMPL_KEY)

def set_implementation(new_impl: bytes) -> None:
    """
    Set/replace the implementation identifier.

    Policy/authorization is the responsibility of the caller. This function
    enforces only basic sanity & emits a deterministic event.

    Parameters
    ----------
    new_impl : bytes
        Opaque identifier (must be non-empty). Commonly a 32-byte hash,
        an address-as-bytes, or a DA commitment.

    Reverts
    -------
    b"UPG:EMPTY"  if `new_impl` is empty
    b"UPG:SAME"   if `new_impl` equals the current value (no-op upgrade)

    Emits
    -----
    b"UPG:ImplementationSet"  with fields `{old, new}`
    """
    if not isinstance(new_impl, (bytes, bytearray)) or len(new_impl) == 0:
        abi.revert(b"UPG:EMPTY")

    old = storage.get(__IMPL_KEY)
    if old == new_impl:
        abi.revert(b"UPG:SAME")

    storage.set(__IMPL_KEY, bytes(new_impl))

    events.emit(b"UPG:ImplementationSet", {
        b"old": bytes(old),
        b"new": bytes(new_impl),
    })

__all__ = [
    "UPGRADE_NAMESPACE",
    "proxiable_uuid",
    "implementation",
    "set_implementation",
]
