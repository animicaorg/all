# -*- coding: utf-8 -*-
"""
contracts.stdlib.upgrade.proxy
==============================

Minimal, deterministic **predictable proxy** helper that pins an implementation
by **code hash** and emits stable upgrade events. This library is intended to be
imported into proxy-style contracts and used to manage an immutable/whitelisted
implementation hash, while leaving *authorization policy* (owner/roles/timelock)
to the calling contract.

What this module provides
-------------------------
- Storage layout (single slot) for the current implementation **code hash**.
- One-time initializer to set the initial implementation hash.
- Upgrade function which, if a *pinned* code hash is compiled-in, rejects any
  other target (predictable upgrades).
- Deterministic events:
    - ``b"PROXY:Initialized" {hash}``
    - ``b"PROXY:Upgraded" {old, new}``
- A UUPS-like **proxiable UUID**, derived from the upgrade namespace tag.
- A resolver descriptor (for node/runtime adapters): ``{kind:"code_hash", value:<bytes>, uuid:<bytes32>}``.

What this module does **not** provide
-------------------------------------
- No authorization: caller must check ``caller == owner()`` (or roles/timelock)
  in the contract that uses this helper.
- No dynamic dispatch/delegate; execution engines/tooling are expected to use
  the stored code hash to route to the appropriate implementation image.

Typical usage
-------------
    from stdlib import abi
    from stdlib.access.ownable import owner
    from contracts.stdlib.upgrade.proxy import (
        initialize, implementation_hash, upgrade_to,
        uuid, pinned_code_hash, resolve,
    )

    def proxy_init(initial_impl_hash: bytes) -> None:
        # Can be gated or constructor-only depending on your deploy flow.
        initialize(initial_impl_hash)

    def proxy_upgrade(caller: bytes, new_impl_hash: bytes) -> None:
        if caller != owner():
            abi.revert(b"ONLY_OWNER")
        upgrade_to(new_impl_hash, caller)

    def get_impl_hash() -> bytes:
        return implementation_hash()

Security guidance
-----------------
- Prefer two-step governance (propose→queue→execute) for high-value targets.
- If you *compile in* a ``PINNED_CODE_HASH``, this helper will enforce that only
  that exact hash is accepted (predictable proxy).
- If you do not pin, any non-empty hash is acceptable (still requires your
  explicit authorization in the caller).

"""
from __future__ import annotations

from typing import Final

from stdlib import abi, events  # type: ignore
from stdlib import hash as _hash  # type: ignore
from stdlib import storage  # type: ignore

# Optional: used only if the proxy contract chooses owner-gated upgrades.
try:  # pragma: no cover - imported when available
    from stdlib.access.ownable import owner  # type: ignore
except Exception:  # pragma: no cover
    # Fallback stub to keep import-time happy in minimal environments.
    def owner() -> bytes:  # type: ignore
        return b""


# -----------------------------------------------------------------------------
# Constants & storage keys
# -----------------------------------------------------------------------------

#: Storage key for the current implementation code hash.
_IMPL_HASH_KEY: Final[bytes] = b"proxy:impl_hash"

#: Storage key to mark the proxy as initialized (one-time guard).
_INIT_KEY: Final[bytes] = b"proxy:inited"

#: Namespace tag that anchors the proxiable UUID (keccak256 domain tag).
UPGRADE_NAMESPACE: Final[bytes] = b"animica/upgrade/v1"

#: Compile-time **pinned** code hash. Tooling (e.g., contracts/tools/build_package.py)
#: may replace this constant with the desired 32-byte target. When non-zero,
#: `upgrade_to()` will **only** accept this hash.
PINNED_CODE_HASH: bytes = b"\x00" * 32  # patched during packaging if desired


# -----------------------------------------------------------------------------
# UUID / compatibility
# -----------------------------------------------------------------------------


def uuid() -> bytes:
    """
    Return the UUPS-like proxiable UUID (keccak256 of :data:`UPGRADE_NAMESPACE`).

    Implementations may expose the same UUID for compatibility checks.
    """
    return _hash.keccak256(UPGRADE_NAMESPACE)


def pinned_code_hash() -> bytes:
    """
    Return the compiled-in pinned code hash. If all-zero, pinning is disabled.
    """
    return PINNED_CODE_HASH


# -----------------------------------------------------------------------------
# Internal helpers
# -----------------------------------------------------------------------------


def _initialized() -> bool:
    return storage.get(_INIT_KEY) == b"\x01"


def _validate_hash(h: bytes) -> None:
    """
    Sanity checks for code-hash payloads.

    Accepts typical sizes (32, 48, 64 bytes) and rejects empty/very short input.
    """
    if not isinstance(h, (bytes, bytearray)) or len(h) == 0:
        abi.revert(b"PROXY:EMPTY_HASH")
    if len(h) not in (32, 48, 64) and len(h) < 16:
        abi.revert(b"PROXY:HASH_LEN")


def _is_pinned_active() -> bool:
    # Any non-zero byte means a pin is set.
    return any(PINNED_CODE_HASH)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def initialize(initial_hash: bytes) -> None:
    """
    One-time initializer for the proxy's implementation hash.

    Reverts
    -------
    b"PROXY:ALREADY_INIT"   if called more than once
    b"PROXY:EMPTY_HASH"     if `initial_hash` is empty
    b"PROXY:HASH_LEN"       if `initial_hash` length is implausible
    b"PROXY:NOT_PINNED"     if pinning is active and `initial_hash` ≠ PINNED_CODE_HASH
    """
    if _initialized():
        abi.revert(b"PROXY:ALREADY_INIT")

    _validate_hash(initial_hash)

    if _is_pinned_active() and initial_hash != PINNED_CODE_HASH:
        abi.revert(b"PROXY:NOT_PINNED")

    storage.set(_IMPL_HASH_KEY, bytes(initial_hash))
    storage.set(_INIT_KEY, b"\x01")

    events.emit(b"PROXY:Initialized", {b"hash": bytes(initial_hash)})


def implementation_hash() -> bytes:
    """
    Read the current implementation code hash from storage.

    Returns
    -------
    bytes
        The stored code hash (empty bytes if unset).
    """
    return storage.get(_IMPL_HASH_KEY)


def upgrade_to(new_hash: bytes, caller: bytes) -> None:
    """
    Upgrade to a new implementation hash.

    Policy (authorization) MUST be enforced by the caller before invoking this.
    A common pattern is:

        if caller != owner():
            abi.revert(b"ONLY_OWNER")

    Reverts
    -------
    b"PROXY:EMPTY_HASH"  if new_hash empty
    b"PROXY:HASH_LEN"    if new_hash length is implausible
    b"PROXY:SAME"        if new_hash equals current
    b"PROXY:NOT_PINNED"  if pinning is active and new_hash ≠ PINNED_CODE_HASH
    """
    _validate_hash(new_hash)

    old = storage.get(_IMPL_HASH_KEY)
    if old == new_hash:
        abi.revert(b"PROXY:SAME")

    if _is_pinned_active() and new_hash != PINNED_CODE_HASH:
        abi.revert(b"PROXY:NOT_PINNED")

    storage.set(_IMPL_HASH_KEY, bytes(new_hash))

    # Best-effort: attempt to sync with the generic upgrade slot if the sibling
    # module is available. We intentionally ignore failures to avoid coupling.
    try:  # pragma: no cover
        from . import set_implementation  # type: ignore

        try:
            set_implementation(bytes(new_hash))  # may raise; safe to ignore
        except Exception:
            pass
    except Exception:
        pass

    events.emit(b"PROXY:Upgraded", {b"old": bytes(old), b"new": bytes(new_hash)})


def resolve() -> dict:
    """
    Return a deterministic descriptor for the runtime/dispatcher.

    Shape: ``{b"kind": b"code_hash", b"value": <bytes>, b"uuid": <bytes32>}``

    Execution engines may use this to route calls to the correct implementation
    image (e.g., verified artifact mapping by code hash).
    """
    h = storage.get(_IMPL_HASH_KEY)
    return {b"kind": b"code_hash", b"value": bytes(h), b"uuid": uuid()}


__all__ = [
    "UPGRADE_NAMESPACE",
    "PINNED_CODE_HASH",
    "uuid",
    "pinned_code_hash",
    "initialize",
    "implementation_hash",
    "upgrade_to",
    "resolve",
]
