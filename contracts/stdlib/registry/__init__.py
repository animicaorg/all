# -*- coding: utf-8 -*-
"""
contracts.stdlib.registry
=========================

Deterministic, contract-local key→value registry helpers.

This module provides a small *owned* registry primitive you can embed into your
contract. It persists an immutable registry ID (derived from inputs), owner,
optional metadata, and a name→value mapping with deterministic key ordering and
bounded, chunkable enumeration.

Design goals
------------
- **Deterministic & simple**: pure storage operations and explicit events.
- **Stable ordering**: first-write index per key for reproducible scans.
- **Chunkable listing**: scan by raw index, skip tombstones; caller controls limits.
- **Library-only permissions**: we *store* the owner but do not read the caller.
  Your contract should enforce authorization (e.g., require caller==owner) before
  invoking mutators here. Helpers like `owner(id)` are provided to aid checks.

ID & storage layout
-------------------
- `id = keccak256(namespace | owner | nonce)`
- Keys (prefixes shown; `id` is the registry id; `i` is index; `name` is bytes):
    "rg:ex:"  + id             -> b"\x01" (exists)
    "rg:own:" + id             -> owner (bytes address)
    "rg:meta:"+ id             -> bytes (opaque)
    "rg:ni:"  + id             -> u256 nextIndex (monotonic counter)
    "rg:cnt:" + id             -> u256 present (non-tombstoned) count
    "rg:k:"   + id + uvar(i)   -> key name at first-write index i
    "rg:ix:"  + id + name      -> u256 (i+1) reverse map (0 => never written)
    "rg:ok:"  + id + name      -> b"\x01" if present, empty otherwise
    "rg:val:" + id + name      -> value bytes (empty if deleted)

Events
------
- b"RegistryCreated"   {id, owner, meta}
- b"RegistrySet"       {id, name, valueLen, created}      # created ∈ {0,1}
- b"RegistryDeleted"   {id, name}
- b"RegistryOwnerSet"  {id, old, new}
- b"RegistryMetaSet"   {id, len}

Reverts
-------
- b"REG:BAD_INPUT"
- b"REG:NOT_FOUND"
- b"REG:EXISTS"
- b"REG:ZERO_NAME"

Typical usage
-------------
    from stdlib import abi
    from contracts.stdlib.registry import id_from, create, owner, put, get, delete, has

    def my_create(ns: bytes, o: bytes, nonce: bytes, meta: bytes=b"") -> bytes:
        rid = create(ns, o, nonce, meta)
        return rid

    def my_set(rid: bytes, caller: bytes, name: bytes, value: bytes) -> None:
        # enforce permissions in *your* contract:
        abi.require(caller == owner(rid), b"ONLY_OWNER")
        put(rid, name, value)

    def my_get(rid: bytes, name: bytes) -> bytes:
        return get(rid, name)  # reverts if not found

    def my_delete(rid: bytes, caller: bytes, name: bytes) -> int:
        abi.require(caller == owner(rid), b"ONLY_OWNER")
        return delete(rid, name)

    def list_page(rid: bytes, start_index: int, limit: int) -> tuple[list[bytes], int]:
        # returns present names (values can be fetched separately), and next index cursor
        return list_present_names(rid, start_index, limit)

Notes
-----
- Enumeration APIs operate on raw write-indices and **skip tombstones**.
- All counters are u256 at the storage boundary; bounds are checked.
"""
from __future__ import annotations

from typing import Final, List, Tuple

from stdlib import storage  # type: ignore
from stdlib import abi, events  # type: ignore
from stdlib import hash as _hash  # type: ignore

_U256_MAX: Final[int] = (1 << 256) - 1

# -------- Storage prefixes --------
_P_EX: Final[bytes] = b"rg:ex:"
_P_OWN: Final[bytes] = b"rg:own:"
_P_META: Final[bytes] = b"rg:meta:"
_P_NI: Final[bytes] = b"rg:ni:"
_P_CNT: Final[bytes] = b"rg:cnt:"
_P_K: Final[bytes] = b"rg:k:"
_P_IX: Final[bytes] = b"rg:ix:"
_P_OK: Final[bytes] = b"rg:ok:"
_P_VAL: Final[bytes] = b"rg:val:"


# -------- Helpers: bytes/u256, keys --------
def _u256_to_bytes(x: int) -> bytes:
    if x < 0 or x > _U256_MAX:
        abi.revert(b"REG:BAD_INPUT")
    return int(x).to_bytes(32, "big")


def _bytes_to_u256(b: bytes) -> int:
    if len(b) == 0:
        return 0
    if len(b) != 32:
        abi.revert(b"REG:NOT_FOUND")
    return int.from_bytes(b, "big")


def _get_u256(k: bytes) -> int:
    return _bytes_to_u256(storage.get(k))


def _set_u256(k: bytes, v: int) -> None:
    storage.set(k, _u256_to_bytes(int(v)))


def _setb(k: bytes, v: bytes) -> None:
    storage.set(k, bytes(v))


def _getb(k: bytes) -> bytes:
    return storage.get(k)


def _k(prefix: bytes, id_: bytes) -> bytes:
    return prefix + id_


def _kname(prefix: bytes, id_: bytes, name: bytes) -> bytes:
    return prefix + id_ + name


def _ki(prefix: bytes, id_: bytes, i: int) -> bytes:
    if i < 0:
        abi.revert(b"REG:BAD_INPUT")
    if i == 0:
        idx = b"\x00"
    else:
        sz = (i.bit_length() + 7) // 8
        idx = i.to_bytes(sz, "big")
    return prefix + id_ + idx


def _ensure_name(name: bytes) -> None:
    if not isinstance(name, (bytes, bytearray)) or len(name) == 0:
        abi.revert(b"REG:ZERO_NAME")


def _exists(id_: bytes) -> bool:
    return storage.get(_k(_P_EX, id_)) == b"\x01"


# -------- ID & creation --------
def id_from(namespace: bytes, owner: bytes, nonce: bytes) -> bytes:
    """
    Deterministic registry id = keccak256(namespace | owner | nonce).
    """
    return _hash.keccak256(bytes(namespace) + bytes(owner) + bytes(nonce))


def create(namespace: bytes, owner_: bytes, nonce: bytes, meta: bytes = b"") -> bytes:
    """
    Create a new registry, persisting owner and optional metadata. Returns `id`.
    """
    id_ = id_from(namespace, owner_, nonce)
    if _exists(id_):
        abi.revert(b"REG:EXISTS")

    _setb(_k(_P_OWN, id_), bytes(owner_))
    _setb(_k(_P_META, id_), bytes(meta))
    _set_u256(_k(_P_NI, id_), 0)  # next index
    _set_u256(_k(_P_CNT, id_), 0)  # present count
    _setb(_k(_P_EX, id_), b"\x01")

    events.emit(
        b"RegistryCreated",
        {
            b"id": id_,
            b"owner": bytes(owner_),
            b"meta": bytes(meta),
        },
    )
    return id_


# -------- Owner & metadata --------
def owner(id_: bytes) -> bytes:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return _getb(_k(_P_OWN, id_))


def set_owner(id_: bytes, new_owner: bytes) -> None:
    """
    Set owner (no internal auth; enforce in your contract before calling).
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    old = _getb(_k(_P_OWN, id_))
    _setb(_k(_P_OWN, id_), bytes(new_owner))
    events.emit(
        b"RegistryOwnerSet",
        {
            b"id": id_,
            b"old": old,
            b"new": bytes(new_owner),
        },
    )


def get_meta(id_: bytes) -> bytes:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return _getb(_k(_P_META, id_))


def set_meta(id_: bytes, meta: bytes) -> None:
    """
    Set/replace metadata blob (no internal auth).
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    _setb(_k(_P_META, id_), bytes(meta))
    events.emit(
        b"RegistryMetaSet",
        {
            b"id": id_,
            b"len": _u256_to_bytes(len(meta)),
        },
    )


# -------- Basic KV --------
def has(id_: bytes, name: bytes) -> bool:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return storage.get(_kname(_P_OK, id_, name)) == b"\x01"


def get(id_: bytes, name: bytes) -> bytes:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    if not has(id_, name):
        abi.revert(b"REG:NOT_FOUND")
    return _getb(_kname(_P_VAL, id_, name))


def size(id_: bytes) -> int:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return _get_u256(_k(_P_CNT, id_))


def put(id_: bytes, name: bytes, value: bytes) -> int:
    """
    Insert or update a record. Returns 1 if created, 0 if updated.
    Emits RegistrySet with created flag.
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    _ensure_name(name)

    ix1 = _get_u256(_kname(_P_IX, id_, name))  # (i+1) or 0
    created = 0
    if ix1 == 0:
        # First time this key is seen — allocate a new write-index.
        i = _get_u256(_k(_P_NI, id_))
        _setb(_ki(_P_K, id_, i), bytes(name))
        _set_u256(_kname(_P_IX, id_, name), i + 1)
        _set_u256(_k(_P_NI, id_), i + 1)
        # mark as present
        _setb(_kname(_P_OK, id_, name), b"\x01")
        # increment present count
        cnt = _get_u256(_k(_P_CNT, id_))
        _set_u256(_k(_P_CNT, id_), cnt + 1)
        created = 1
    else:
        # Existing key: ensure present flag set (it could be a tombstone).
        if storage.get(_kname(_P_OK, id_, name)) != b"\x01":
            _setb(_kname(_P_OK, id_, name), b"\x01")
            cnt = _get_u256(_k(_P_CNT, id_))
            _set_u256(_k(_P_CNT, id_), cnt + 1)
            created = 1  # resurrecting a tombstoned key counts as (re)created

    # Set/replace value
    _setb(_kname(_P_VAL, id_, name), bytes(value))

    events.emit(
        b"RegistrySet",
        {
            b"id": id_,
            b"name": bytes(name),
            b"valueLen": _u256_to_bytes(len(value)),
            b"created": _u256_to_bytes(created),
        },
    )
    return created


def delete(id_: bytes, name: bytes) -> int:
    """
    Tombstone a record if present. Returns 1 if deleted, 0 if it was absent.
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    _ensure_name(name)
    if not has(id_, name):
        return 0
    # mark absent
    _setb(_kname(_P_OK, id_, name), b"")  # presence flag cleared
    _setb(
        _kname(_P_VAL, id_, name), b""
    )  # clear value (keeps storage key deterministic)
    cnt = _get_u256(_k(_P_CNT, id_))
    _set_u256(_k(_P_CNT, id_), cnt - 1 if cnt > 0 else 0)

    events.emit(
        b"RegistryDeleted",
        {
            b"id": id_,
            b"name": bytes(name),
        },
    )
    return 1


# -------- Enumeration (chunkable) --------
def next_index(id_: bytes) -> int:
    """
    Returns the current *next* write-index (i.e., total number of ever-seen keys).
    Useful as an upper bound for raw index scans.
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return _get_u256(_k(_P_NI, id_))


def name_at_index(id_: bytes, i: int) -> bytes:
    """
    Returns the key name at write-index `i` (may be empty if index never written).
    Does not check tombstone state.
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    return _getb(_ki(_P_K, id_, i))


def list_present_names(
    id_: bytes, start_index: int, limit: int
) -> Tuple[List[bytes], int]:
    """
    Scan forward from `start_index` over write-indices, collecting up to `limit`
    **present** (non-tombstoned) names. Returns (names, next_index_cursor).

    Caller can resume by passing the returned cursor. If `limit` <= 0, zero items
    are returned with the input cursor echoed back.
    """
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    if start_index < 0 or limit < 0:
        abi.revert(b"REG:BAD_INPUT")

    hi = next_index(id_)
    if start_index >= hi or limit == 0:
        return [], start_index

    out: List[bytes] = []
    i = start_index
    remaining = limit
    while i < hi and remaining > 0:
        nm = _getb(_ki(_P_K, id_, i))
        if len(nm) != 0 and has(id_, nm):
            out.append(nm)
            remaining -= 1
        i += 1
    return out, i


# Convenience: get value for a batch of names (e.g., result of list_present_names).
def get_many(id_: bytes, names: List[bytes]) -> List[bytes]:
    if not _exists(id_):
        abi.revert(b"REG:NOT_FOUND")
    out: List[bytes] = []
    for nm in names:
        if has(id_, nm):
            out.append(_getb(_kname(_P_VAL, id_, nm)))
        else:
            out.append(b"")
    return out


__all__ = [
    # id & create
    "id_from",
    "create",
    # owner/meta
    "owner",
    "set_owner",
    "get_meta",
    "set_meta",
    # kv
    "has",
    "get",
    "put",
    "delete",
    "size",
    # enumeration
    "next_index",
    "name_at_index",
    "list_present_names",
    "get_many",
]
