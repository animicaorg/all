from __future__ import annotations

"""
KV interface & namespace prefixes
=================================

This module defines a backend-agnostic Key–Value interface used throughout
Animica, plus canonical key prefixes for major logical buckets:

- STATE   (b"s:")  : account/state trie snapshots, balances, nonces, storage
- HEADERS (b"h:")  : canonical headers by height/hash, header roots
- BLOCKS  (b"b:")  : full blocks by hash, body/txs/proofs
- TXIDX   (b"x:")  : transaction index (txHash → (height, idx))
- META    (b"m:")  : miscellaneous chain metadata (chainId, bestHead, params)

Backends (sqlite, rocksdb) implement this interface and the batch semantics.
This file is *pure interface + helpers* and contains no I/O.

Key building helpers
--------------------
We offer a small, safe DSL to construct lexicographically sortable keys:

- Prefix(ns=b"s") produces a prefix object:
    STATE.key(b"acct", addr_bytes) → b"s:" + len|data + len|data ...
- For integers you likely want big-endian fixed-width encodings:
    be_u32(height), be_u64(nonce), be_u256(number_as_int)

These helpers avoid delimiter-escaping pitfalls and ensure stable ordering.

Example
-------
>>> from core.db.kv import Prefix, STATE, be_u32
>>> k = STATE.key(b"acct", b"\x01\x02", be_u32(100))
>>> k.startswith(STATE.raw)
True

Iterators
---------
`iter_prefix(prefix: bytes)` yields `(key, value)` pairs in lexicographic order.
Use `iter_prefixed(kv, STATE.raw)` for a portable helper.

Batching
--------
`KV.batch()` returns a context manager. Use it to atomically put/delete:

>>> with kv.batch() as b:
...     b.put(STATE.key(b"a"), b"1")
...     b.delete(STATE.key(b"b"))

Typing
------
We expose Protocols (PEP 544) so backends can be duck-typed.
"""

from typing import (Iterable, Iterator, List, Optional, Protocol, Tuple, Union,
                    runtime_checkable)

# ---------------------------------------------------------------------------
# Prefix helpers
# ---------------------------------------------------------------------------

NS_SEP = b":"  # namespace separator used only once after the leading ns byte


class Prefix:
    """
    Represents a logical namespace prefix (e.g., b"s:" for STATE).

    .raw gives the raw bytes prefix.
    .key(*parts) builds a composite key: prefix + ∑ (uvarlen | part_bytes).
    """

    __slots__ = ("_raw",)

    def __init__(self, ns: Union[bytes, bytearray, memoryview, str]) -> None:
        if isinstance(ns, str):
            ns_b = ns.encode("ascii")
        else:
            ns_b = bytes(ns)
        if len(ns_b) == 0:
            raise ValueError("namespace must be non-empty")
        # Ensure we do not double include the separator
        self._raw = ns_b.rstrip(NS_SEP) + NS_SEP

    @property
    def raw(self) -> bytes:
        return self._raw

    def key(self, *parts: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
        """Build a composite key under this prefix."""
        out = bytearray(self._raw)
        for p in parts:
            pb = _part_to_bytes(p)
            out.extend(_uvarint_len(len(pb)))
            out.extend(pb)
        return bytes(out)


def _part_to_bytes(p: Union[bytes, bytearray, memoryview, str, int]) -> bytes:
    if isinstance(p, (bytes, bytearray, memoryview)):
        return bytes(p)
    if isinstance(p, str):
        return p.encode("utf-8")
    if isinstance(p, int):
        # Default int encoding: big-endian variable width without sign, minimal length
        if p < 0:
            raise ValueError("negative ints not supported in key parts")
        return _int_big_endian_minimal(p)
    raise TypeError(f"unsupported key part type: {type(p)!r}")


def _int_big_endian_minimal(n: int) -> bytes:
    if n == 0:
        return b"\x00"
    out = bytearray()
    while n:
        out.append(n & 0xFF)
        n >>= 8
    return bytes(reversed(out))


def _uvarint_len(n: int) -> bytes:
    """LEB128-like unsigned length prefix (fits typical key parts)."""
    if n < 0:
        raise ValueError("length must be non-negative")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(0x80 | b)
        else:
            out.append(b)
            break
    return bytes(out)


def be_u32(n: int) -> bytes:
    if not (0 <= n < (1 << 32)):
        raise ValueError("be_u32 out of range")
    return n.to_bytes(4, "big")


def be_u64(n: int) -> bytes:
    if not (0 <= n < (1 << 64)):
        raise ValueError("be_u64 out of range")
    return n.to_bytes(8, "big")


def be_u256(n: int) -> bytes:
    if not (0 <= n < (1 << 256)):
        raise ValueError("be_u256 out of range")
    return n.to_bytes(32, "big")


# Canonical top-level prefixes (keep small & stable; more can live in submodules)
STATE = Prefix(b"s")  # accounts, storage, balances, nonces
HEADERS = Prefix(b"h")  # headers by height/hash; roots
BLOCKS = Prefix(b"b")  # blocks by hash; bodies/txs/proofs
TXIDX = Prefix(b"x")  # tx hash → (height, index)
META = Prefix(b"m")  # chain meta (best head, params hash, counters)


# ---------------------------------------------------------------------------
# KV protocols & Batch
# ---------------------------------------------------------------------------


@runtime_checkable
class ReadOnlyKV(Protocol):
    """Minimal read-only KV surface."""

    def get(self, key: bytes) -> Optional[bytes]:
        """Fetch value or None if missing."""
        ...

    def has(self, key: bytes) -> bool:
        """Return True if key exists (cheap if backend can avoid fetching value)."""
        ...

    def iter_prefix(self, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]:
        """
        Iterate over (key, value) pairs whose key begins with `prefix`,
        in lexicographic byte-order of keys. Implementations should be snapshot-consistent
        for the duration of the iterator where possible.
        """
        ...

    def close(self) -> None:
        """Close resources (no-op for in-memory)."""
        ...


@runtime_checkable
class Batch(Protocol):
    """
    A write-batch context manager. Backend guarantees atomicity when exiting
    the context without exception. If an exception escapes, the batch is rolled back.
    """

    def put(self, key: bytes, value: bytes) -> None: ...
    def delete(self, key: bytes) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...

    def __enter__(self) -> "Batch": ...
    def __exit__(self, exc_type, exc, tb) -> Optional[bool]: ...


@runtime_checkable
class KV(ReadOnlyKV, Protocol):
    """Full RW KV surface."""

    def put(self, key: bytes, value: bytes) -> None:
        """Persist (key,value). Overwrites if exists."""
        ...

    def delete(self, key: bytes) -> None:
        """Remove key if present (idempotent)."""
        ...

    def batch(self) -> Batch:
        """Return a new write batch."""
        ...


# ---------------------------------------------------------------------------
# Portable helpers built atop the interface
# ---------------------------------------------------------------------------


def iter_prefixed(kv: ReadOnlyKV, prefix: bytes) -> Iterator[Tuple[bytes, bytes]]:
    """Portable helper that defers to kv.iter_prefix."""
    return kv.iter_prefix(prefix)


def get_or_raise(kv: ReadOnlyKV, key: bytes, err: Exception) -> bytes:
    v = kv.get(key)
    if v is None:
        raise err
    return v


def put_many(kv: KV, items: Iterable[Tuple[bytes, bytes]]) -> None:
    """Efficiently write many keys using a single batch."""
    with kv.batch() as b:
        for k, v in items:
            b.put(k, v)


def delete_many(kv: KV, keys: Iterable[bytes]) -> None:
    """Efficiently delete many keys using a single batch."""
    with kv.batch() as b:
        for k in keys:
            b.delete(k)


# Backwards-compatible shim
def open_kv(uri: str, create: bool = True) -> KV:
    """Import-time-light proxy to core.db.open_kv.

    Historically callers imported open_kv from core.db.kv; retain that path to
    avoid breaking older modules while keeping the real implementation in
    core.db.
    """

    from core.db import open_kv as _open_kv

    return _open_kv(uri, create=create)


# ---------------------------------------------------------------------------
# Common sub-prefixes (documented patterns)
# ---------------------------------------------------------------------------

# STATE sub-keys (recommended shapes)
# - STATE.key(b"acct", <addr>)                         -> account record blob
# - STATE.key(b"stor", <addr>, <slot_key>)             -> storage cell
# - STATE.key(b"code", <addr>)                         -> contract code hash / pointer

# HEADERS sub-keys
# - HEADERS.key(b"byHeight", be_u32(height))           -> headerHash
# - HEADERS.key(b"byHash", <headerHash>)               -> header CBOR
# - HEADERS.key(b"roots", <headerHash>, b"state")      -> stateRoot
# - HEADERS.key(b"roots", <headerHash>, b"txs")        -> txsRoot
# - HEADERS.key(b"roots", <headerHash>, b"proofs")     -> proofsRoot

# BLOCKS sub-keys
# - BLOCKS.key(b"byHash", <blockHash>)                 -> block CBOR
# - BLOCKS.key(b"txs", <blockHash>, be_u32(idx))       -> tx hash (or tx CBOR)
# - BLOCKS.key(b"receipts", <blockHash>, be_u32(idx))  -> receipt CBOR

# TXIDX sub-keys
# - TXIDX.key(b"byHash", <txHash>)                     -> be_u32(height) | be_u32(idx)
# - TXIDX.key(b"byAddr", <addr>, be_u32(n))            -> tx hash list (optional)

# META sub-keys
# - META.key(b"bestHeight")                            -> be_u32(height)
# - META.key(b"bestHash")                              -> header hash
# - META.key(b"paramsHash")                            -> sha3(params.yaml canonical bytes)
# - META.key(b"genesisHash")                           -> genesis header hash


__all__ = [
    # Protocols
    "ReadOnlyKV",
    "KV",
    "Batch",
    # Prefixes
    "STATE",
    "HEADERS",
    "BLOCKS",
    "TXIDX",
    "META",
    # Helpers
    "Prefix",
    "iter_prefixed",
    "get_or_raise",
    "put_many",
    "delete_many",
    "open_kv",
    # Encoders
    "be_u32",
    "be_u64",
    "be_u256",
]
