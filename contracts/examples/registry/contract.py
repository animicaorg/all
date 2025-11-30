# -*- coding: utf-8 -*-
"""
Deterministic Name Registry (bytes32 ⇄ address)

This contract stores an opaque 32-byte key ("name") mapped to a bech32m Animica
address string ("anim1…"). The contract is intentionally small and boring:
- No wall clock or nondeterminism
- Pure storage + events
- Crisp reverts and input validation
- Canonical event names/args

Functions (see manifest.json for ABI details):
- set(name: bytes32, addr: address) -> None
- get(name: bytes32) -> address  (empty string "" if unset)
- has(name: bytes32) -> bool
- remove(name: bytes32) -> None

Events:
- NameSet(name: bytes32, addr: address)
- NameRemoved(name: bytes32)
"""
from __future__ import annotations

from stdlib import abi, events, storage  # provided by vm_py runtime

# ---- storage layout ---------------------------------------------------------

# Namespacing prevents key collisions with other contracts or future fields.
# Key = REGISTRY_NS || name (32 bytes)
REGISTRY_NS = b"registry:name:"  # 14 bytes; total key len = 14 + 32 = 46
KEY_LEN = len(REGISTRY_NS) + 32

# Zero/empty address sentinel for "not set".
# We use an empty string "" for reads to keep ABI simple & deterministic.
EMPTY_ADDR = ""


def _key_for(name: bytes) -> bytes:
    """
    Build the storage key for a given 32-byte name.
    Validates input length to remain deterministic and defensive.
    """
    # Accept exactly 32 bytes; callers should pre-hash labels off-chain if needed.
    if not isinstance(name, (bytes, bytearray)) or len(name) != 32:
        abi.revert("name-bad-len")  # deterministic error string
    return REGISTRY_NS + bytes(name)  # ensure immutable bytes


def _validate_addr(addr: str) -> None:
    """
    Very lightweight sanity checks on address strings.
    We do *not* perform heavy validation here (that belongs off-chain or in the SDK);
    this is merely to catch obvious mistakes deterministically.
    """
    # Address must be a non-empty ASCII-ish string and reasonably small.
    if not isinstance(addr, str) or not addr:
        abi.revert("zero-address")
    # Animica addresses are bech32m "anim1..." — we check the human-readable part prefix.
    # Do not check checksum here to keep cost minimal.
    if not addr.startswith("anim1"):
        abi.revert("addr-bad-hrp")
    # Avoid unbounded storage bloat: cap length. Be generous to allow future encodings.
    if len(addr) > 128:
        abi.revert("addr-too-long")


# ---- public interface --------------------------------------------------------


def set(name: bytes, addr: str) -> None:
    """
    Register or update a mapping.
    Emits NameSet(name, addr).
    """
    _validate_addr(addr)
    k = _key_for(name)
    # Persist as UTF-8 bytes; storage API is byte-oriented.
    storage.set(k, addr.encode("utf-8"))
    events.emit(b"NameSet", {b"name": name, b"addr": addr})


def get(name: bytes) -> str:
    """
    Resolve a name to an address. If unset, returns the empty string "".
    """
    k = _key_for(name)
    v = storage.get(k)
    if v is None or len(v) == 0:
        return EMPTY_ADDR
    # Decode as UTF-8; we only ever store ASCII bech32m strings.
    try:
        return v.decode("utf-8")
    except Exception:
        # Should not occur for data written by this contract; be defensive.
        abi.revert("corrupt-state")


def has(name: bytes) -> bool:
    """
    Fast existence check.
    """
    k = _key_for(name)
    v = storage.get(k)
    return bool(v)  # None or b"" => False; any non-empty bytes => True


def remove(name: bytes) -> None:
    """
    Delete a mapping if it exists. Emits NameRemoved.
    If missing, reverts with 'not-found' to keep callers honest.
    """
    k = _key_for(name)
    v = storage.get(k)
    if not v:
        abi.revert("not-found")
    # vm_py storage API is set/get; represent deletion as empty bytes.
    storage.set(k, b"")
    events.emit(b"NameRemoved", {b"name": name})


# ---- optional reverse mapping (commented template) --------------------------
# If you decide to maintain a reverse map (address -> name), uncomment and use this
# pattern. Remember to update the manifest ABI accordingly and consider the doubled
# write cost for set/remove.
#
# REVERSE_NS = b"registry:rev:"  # REVERSE_NS || addr_string_utf8
#
# def _rev_key_for(addr: str) -> bytes:
#     _validate_addr(addr)
#     return REVERSE_NS + addr.encode("utf-8")
#
# def set_with_reverse(name: bytes, addr: str) -> None:
#     _validate_addr(addr)
#     k = _key_for(name)
#     rk = _rev_key_for(addr)
#     storage.set(k, addr.encode("utf-8"))
#     storage.set(rk, name)
#     events.emit(b"NameSet", {b"name": name, b"addr": addr})
#
# def get_reverse(addr: str) -> bytes:
#     rk = _rev_key_for(addr)
#     v = storage.get(rk)
#     return v if v else b""
#
# def remove_with_reverse(name: bytes) -> None:
#     k = _key_for(name)
#     v = storage.get(k)
#     if not v:
#         abi.revert("not-found")
#     storage.set(k, b"")
#     # best-effort cleanup of reverse key
#     rk = _rev_key_for(v.decode("utf-8"))
#     storage.set(rk, b"")
#     events.emit(b"NameRemoved", {b"name": name})
