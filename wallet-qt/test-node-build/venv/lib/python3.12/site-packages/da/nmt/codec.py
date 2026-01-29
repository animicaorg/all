"""
Animica • DA • NMT — Leaf codec
================================

Canonical on-wire leaf encoding used by the DA Namespaced Merkle Tree (NMT).

Format (per leaf)
-----------------
    leaf := ns_be || uvarint(len) || data

Where:
  • ns_be          — namespace id as big-endian unsigned integer with a fixed
                     width derived from `da.constants.NAMESPACE_BITS`
  • uvarint(len)   — unsigned LEB128-style length of `data`
  • data           — opaque payload bytes

Hashing rule (for Merkle leaves)
--------------------------------
The *payload hash* used by NMT leaf hashing is
    H( uvarint(len) || data )
i.e. the namespace is **not** included in the payload hash. See `da.nmt.node`
for the full leaf hashing domain (which then combines the namespace + payload
hash with a leaf domain tag).

This module only serializes/deserializes leaf blobs and exposes helpers to
compute the payload hash from an encoded leaf.

API
---
    encode_leaf(ns: int|NamespaceId, data: bytes) -> bytes
    decode_leaf(encoded: bytes) -> tuple[NamespaceId, bytes]        # strict: one whole leaf
    decode_one(buf: bytes, offset=0) -> (NamespaceId, bytes, int)   # streaming
    iter_leaves(buf: bytes) -> Iterator[tuple[NamespaceId, bytes]]
    payload_hash_from_encoded(encoded_leaf: bytes) -> bytes

Errors raise `NMTCodecError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Tuple

try:
    from ..constants import NAMESPACE_BITS  # type: ignore
except Exception:  # pragma: no cover
    NAMESPACE_BITS = 32

_NS_BYTES = (NAMESPACE_BITS + 7) // 8

from ..utils.bytes import read_uvarint, write_uvarint
from ..utils.hash import sha3_256
from .namespace import NamespaceId

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class NMTCodecError(ValueError):
    """Raised on malformed or inconsistent leaf encodings."""


# --------------------------------------------------------------------------- #
# Encode / Decode
# --------------------------------------------------------------------------- #


def encode_leaf(
    ns: int | NamespaceId, data: bytes, ns_bytes: int | None = None
) -> bytes:
    """
    Serialize one leaf as: ``ns_be || uvarint(len) || data``.

    The optional ``ns_bytes`` parameter keeps compatibility with older call-sites
    that explicitly passed the namespace width; when omitted we fall back to the
    configured width derived from :data:`NAMESPACE_BITS`.
    """
    ns_id = NamespaceId(int(ns))
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise NMTCodecError("data must be bytes-like")
    width = _NS_BYTES if ns_bytes is None else int(ns_bytes)
    if width <= 0:
        raise NMTCodecError("ns_bytes must be positive")
    ns_be = int(ns_id).to_bytes(width, "big")
    return (
        ns_be
        + write_uvarint(len(data))
        + (bytes(data) if not isinstance(data, bytes) else data)
    )


def decode_one(buf: bytes, *, offset: int = 0) -> Tuple[NamespaceId, bytes, int]:
    """
    Parse a single leaf from `buf` starting at `offset`.

    Returns:
        (ns_id, payload_bytes, new_offset)

    Raises:
        NMTCodecError for malformed inputs or truncated buffers.
    """
    if offset < 0 or offset > len(buf):
        raise NMTCodecError("offset out of range")

    # Need at least namespace + 1 byte for length
    if len(buf) - offset < _NS_BYTES + 1:
        raise NMTCodecError("buffer too small for namespace and length")

    ns_be = buf[offset : offset + _NS_BYTES]
    ns = int.from_bytes(ns_be, "big")
    ns_id = NamespaceId(ns)

    length, off_after_len = read_uvarint(buf, offset=offset + _NS_BYTES)
    if length < 0:
        raise NMTCodecError("negative length (invalid varint)")
    end = off_after_len + length
    if end > len(buf):
        raise NMTCodecError("declared length exceeds buffer")

    payload = buf[off_after_len:end]
    return ns_id, payload, end


def decode_leaf(encoded: bytes) -> Tuple[NamespaceId, bytes]:
    """
    Strict decoder for a buffer that should contain exactly one leaf.
    """
    ns, payload, end = decode_one(encoded, offset=0)
    if end != len(encoded):
        raise NMTCodecError("extra trailing bytes after a single leaf")
    return ns, payload


def iter_leaves(buf: bytes) -> Iterator[Tuple[NamespaceId, bytes]]:
    """
    Iterate all leaves contained in the concatenated buffer `buf`.
    """
    off = 0
    n = len(buf)
    while off < n:
        ns, payload, off = decode_one(buf, offset=off)
        yield ns, payload


# --------------------------------------------------------------------------- #
# Hash helper
# --------------------------------------------------------------------------- #


def payload_hash_from_encoded(encoded_leaf: bytes) -> bytes:
    """
    Compute H(uvarint(len) || data) directly from an encoded leaf.
    """
    if len(encoded_leaf) < _NS_BYTES + 1:
        raise NMTCodecError("encoded leaf too short")
    # Hash the *payload serialization* (length varint + data), which starts
    # immediately after the fixed-size namespace field.
    return sha3_256(encoded_leaf[_NS_BYTES:])


__all__ = [
    "NMTCodecError",
    "encode_leaf",
    "decode_one",
    "decode_leaf",
    "iter_leaves",
    "payload_hash_from_encoded",
]
