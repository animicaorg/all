# -*- coding: utf-8 -*-
"""
contracts.stdlib.registry.name_registry
=======================================

A tiny, deterministic **bytes32 ↔ address** name registry built on top of
`contracts.stdlib.registry`. Forward records map a fixed-length 32-byte name
to a raw address byte-string. An optional **primary** reverse mapping lets an
address designate a single canonical name.

This module does **not** perform authorization by itself. As with other stdlib
helpers, your contract must enforce permissions (e.g., owner/roles) before
calling mutators here.

Key properties
--------------
- **Deterministic storage keys** and event emission.
- **Forward table**: `name(32) → addr(bytes)`, with presence flag.
- **Reverse primary**: `addr → name(32)` (at most one name per address).
- **Safe updates**: reassigning a name clears the old address' primary if it
  pointed to that name; setting a new primary overwrites the previous one.

Events
------
- `b"NR:NameRegistered"`  `{id, name, addr, created}`           # created ∈ {0,1}
- `b"NR:NameCleared"`     `{id, name}`
- `b"NR:PrimarySet"`      `{id, addr, name}`
- `b"NR:PrimaryCleared"`  `{id, addr}`

Reverts
-------
- `b"NR:NOT_FOUND"`   — registry id missing or lookup missing
- `b"NR:B32"`         — name must be exactly 32 bytes
- `b"NR:BAD_ADDR"`    — address must be non-empty
- `b"NR:NOFWD"`       — expected forward record is absent

Typical usage
-------------
    from stdlib import abi
    from contracts.stdlib.registry import owner as reg_owner
    from contracts.stdlib.registry.name_registry as nr

    def create_registry(caller: bytes, nonce: bytes, meta: bytes=b"") -> bytes:
        rid = nr.create_registry(caller, nonce, meta)
        return rid

    def register(caller: bytes, rid: bytes, name32: bytes, addr: bytes, make_primary: int=1) -> int:
        # Authorization (example): only the registry owner may mutate
        abi.require(caller == reg_owner(rid), b"ONLY_OWNER")
        return nr.register_name(rid, name32, addr, make_primary)

    def resolve(rid: bytes, name32: bytes) -> bytes:
        return nr.resolve(rid, name32)

    def primary_of(rid: bytes, addr: bytes) -> bytes:
        return nr.reverse_primary(rid, addr)

Notes
-----
- Address is an opaque byte-string (non-empty). Upstream address formatting
  (e.g., bech32m) happens at the edge; on-chain we keep it as bytes.
- Names are fixed **32 bytes**. If you need human strings, hash or encode them
  to 32 bytes in your contract/dapp layer.
"""
from __future__ import annotations

from typing import Final

from stdlib import storage  # type: ignore
from stdlib import abi, events  # type: ignore
from stdlib import hash as _hash  # type: ignore

# Reuse the generic registry for id/owner/meta/existence.
from . import create as _reg_create  # type: ignore
from . import owner as _reg_owner

# -------- Constants & prefixes --------

#: Namespace tag used when creating a name-registry id via the generic registry.
_NS: Final[bytes] = b"animica/name-reg/v1"

# Storage prefixes (all keys are "prefix || id || ...")
_P_FOK: Final[bytes] = b"nr:fok:"  # forward presence: id|name32 -> b"\x01"
_P_FWD: Final[bytes] = b"nr:fwd:"  # forward value:    id|name32 -> addr
_P_RP: Final[bytes] = b"nr:rp:"  # reverse primary:  id|addr   -> name32

# -------- Internal helpers --------


def _k(prefix: bytes, id_: bytes) -> bytes:
    return prefix + id_


def _kname(prefix: bytes, id_: bytes, name32: bytes) -> bytes:
    return prefix + id_ + name32


def _kaddr(prefix: bytes, id_: bytes, addr: bytes) -> bytes:
    return prefix + id_ + addr


def _ensure_id_exists(id_: bytes) -> None:
    # Indirection via reading owner; will revert if id is unknown.
    _ = _reg_owner(id_)


def _check_name32(name32: bytes) -> None:
    if not isinstance(name32, (bytes, bytearray)) or len(name32) != 32:
        abi.revert(b"NR:B32")


def _check_addr(addr: bytes) -> None:
    # Keep address opaque, but require non-empty to avoid degenerate keys.
    if not isinstance(addr, (bytes, bytearray)) or len(addr) == 0:
        abi.revert(b"NR:BAD_ADDR")


def _present(id_: bytes, name32: bytes) -> bool:
    return storage.get(_kname(_P_FOK, id_, name32)) == b"\x01"


# -------- Public API --------


def create_registry(owner: bytes, nonce: bytes, meta: bytes = b"") -> bytes:
    """
    Create a new name-registry using the generic registry's storage for
    existence/owner/meta. Returns the `id` (keccak(namespace|owner|nonce)).
    """
    return _reg_create(_NS, owner, nonce, meta)


def id_from(owner: bytes, nonce: bytes) -> bytes:
    """
    Deterministically compute the registry id (without writing state).
    """
    return _hash.keccak256(_NS + bytes(owner) + bytes(nonce))


def has_name(id_: bytes, name32: bytes) -> bool:
    """
    True if a forward record for `name32` exists (irrespective of reverse).
    """
    _ensure_id_exists(id_)
    _check_name32(name32)
    return _present(id_, name32)


def resolve(id_: bytes, name32: bytes) -> bytes:
    """
    Resolve a 32-byte name to an address. Reverts if not present.
    """
    _ensure_id_exists(id_)
    _check_name32(name32)
    if not _present(id_, name32):
        abi.revert(b"NR:NOT_FOUND")
    return storage.get(_kname(_P_FWD, id_, name32))


def reverse_primary(id_: bytes, addr: bytes) -> bytes:
    """
    Return the primary name (bytes32) set for `addr`. Reverts if none.
    """
    _ensure_id_exists(id_)
    _check_addr(addr)
    nm = storage.get(_kaddr(_P_RP, id_, addr))
    if len(nm) != 32:
        abi.revert(b"NR:NOT_FOUND")
    return nm


def register_name(id_: bytes, name32: bytes, addr: bytes, set_primary: int = 1) -> int:
    """
    Insert or update the forward mapping `name32 → addr`.

    Returns:
        1 if this created a new forward record (absent → present),
        0 if it updated an existing record (same name).

    Side-effects:
        - If the name was previously assigned to a *different* address and
          that old address had `name32` as its primary, the reverse entry for
          that old address is cleared.
        - If `set_primary != 0`, the reverse primary for `addr` is set to
          `name32` (overwriting any previous primary).

    Emits:
        - b"NR:NameRegistered" {id, name, addr, created}
        - b"NR:PrimaryCleared" (optional)
        - b"NR:PrimarySet"     (optional)
    """
    _ensure_id_exists(id_)
    _check_name32(name32)
    _check_addr(addr)

    created = 0
    key_ok = _kname(_P_FOK, id_, name32)
    key_val = _kname(_P_FWD, id_, name32)

    was_present = storage.get(key_ok) == b"\x01"
    if not was_present:
        created = 1
        storage.set(key_ok, b"\x01")
        storage.set(key_val, bytes(addr))
    else:
        old_addr = storage.get(key_val)
        if old_addr != addr:
            # If primary(old_addr) == name32, clear it.
            rp_key_old = _kaddr(_P_RP, id_, old_addr)
            cur_nm = storage.get(rp_key_old)
            if cur_nm == name32:
                storage.set(rp_key_old, b"")  # clear
                events.emit(
                    b"NR:PrimaryCleared",
                    {
                        b"id": id_,
                        b"addr": old_addr,
                    },
                )
            # Update forward to new address.
            storage.set(key_val, bytes(addr))

    # Optionally set new primary for (addr).
    if set_primary != 0:
        storage.set(_kaddr(_P_RP, id_, addr), bytes(name32))
        events.emit(
            b"NR:PrimarySet",
            {
                b"id": id_,
                b"addr": bytes(addr),
                b"name": bytes(name32),
            },
        )

    events.emit(
        b"NR:NameRegistered",
        {
            b"id": id_,
            b"name": bytes(name32),
            b"addr": bytes(addr),
            b"created": (1).to_bytes(32, "big") if created else (0).to_bytes(32, "big"),
        },
    )
    return created


def clear_name(id_: bytes, name32: bytes) -> int:
    """
    Remove (tombstone) the forward mapping for `name32`.
    If the cleared name was the primary of some address, that reverse entry
    is cleared as well.

    Returns:
        1 if a record existed and was cleared, 0 if it was already absent.

    Emits:
        - b"NR:NameCleared"
        - b"NR:PrimaryCleared" (optional)
    """
    _ensure_id_exists(id_)
    _check_name32(name32)

    key_ok = _kname(_P_FOK, id_, name32)
    key_val = _kname(_P_FWD, id_, name32)

    if storage.get(key_ok) != b"\x01":
        return 0

    # If this name is primary for its current address, clear reverse.
    cur_addr = storage.get(key_val)
    if len(cur_addr) != 0:
        rp_key = _kaddr(_P_RP, id_, cur_addr)
        cur_nm = storage.get(rp_key)
        if cur_nm == name32:
            storage.set(rp_key, b"")
            events.emit(
                b"NR:PrimaryCleared",
                {
                    b"id": id_,
                    b"addr": cur_addr,
                },
            )

    # Clear forward presence and value (keep deterministic keys).
    storage.set(key_ok, b"")
    storage.set(key_val, b"")

    events.emit(
        b"NR:NameCleared",
        {
            b"id": id_,
            b"name": bytes(name32),
        },
    )
    return 1


def set_primary(id_: bytes, addr: bytes, name32: bytes) -> None:
    """
    Set `name32` as the primary reverse entry for `addr`.
    Requires that `name32` exists and currently resolves to `addr`.
    """
    _ensure_id_exists(id_)
    _check_addr(addr)
    _check_name32(name32)

    if storage.get(_kname(_P_FOK, id_, name32)) != b"\x01":
        abi.revert(b"NR:NOFWD")
    if storage.get(_kname(_P_FWD, id_, name32)) != addr:
        abi.revert(b"NR:NOFWD")

    storage.set(_kaddr(_P_RP, id_, addr), bytes(name32))
    events.emit(
        b"NR:PrimarySet",
        {
            b"id": id_,
            b"addr": bytes(addr),
            b"name": bytes(name32),
        },
    )


def clear_primary(id_: bytes, addr: bytes) -> int:
    """
    Clear the primary reverse entry for `addr`. Returns 1 if cleared, 0 if none.
    """
    _ensure_id_exists(id_)
    _check_addr(addr)
    rp_key = _kaddr(_P_RP, id_, addr)
    cur = storage.get(rp_key)
    if len(cur) != 32:
        return 0
    storage.set(rp_key, b"")
    events.emit(
        b"NR:PrimaryCleared",
        {
            b"id": id_,
            b"addr": bytes(addr),
        },
    )
    return 1


__all__ = [
    "create_registry",
    "id_from",
    "has_name",
    "resolve",
    "reverse_primary",
    "register_name",
    "clear_name",
    "set_primary",
    "clear_primary",
]
