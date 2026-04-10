"""
Animica • DA • NMT — Internal node structure

This module defines the minimal internal structure used by the Namespaced
Merkle Tree (NMT) and the canonical hashing rules for both leaf and inner
nodes.

Hashing domains
---------------
We use single-byte domain tags to avoid ambiguity:

    LEAF_TAG  = 0x00
    NODE_TAG  = 0x01

Canonical bytes fed to SHA3-256:

  • Leaf:
        H( LEAF_TAG || ns_be || leaf_payload_hash )
    where:
        - ns_be is the namespace id in big-endian, fixed width
        - leaf_payload_hash is the hash of the *serialized* leaf payload
          (serialization is defined in da.nmt.codec)

  • Inner node:
        H( NODE_TAG || left_hash || right_hash || ns_min_be || ns_max_be )

Namespace byte width is derived from NAMESPACE_BITS (defaults to 32 if
da.constants is not available). All hashes are 32-byte digests by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

try:  # prefer canonical limits if available
    from ..constants import NAMESPACE_BITS as _NAMESPACE_BITS  # type: ignore
except Exception:  # pragma: no cover
    _NAMESPACE_BITS = 32

_NS_BYTES = (_NAMESPACE_BITS + 7) // 8

from ..utils.bytes import bytes_to_hex
from ..utils.hash import sha3_256
from .namespace import NamespaceId, NamespaceRange

LEAF_TAG = b"\x00"
NODE_TAG = b"\x01"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class NMTNodeError(ValueError):
    """Raised for malformed node inputs (hash sizes, ranges, etc.)."""


# --------------------------------------------------------------------------- #
# Hash helpers
# --------------------------------------------------------------------------- #


def _ns_to_be(ns: int | NamespaceId) -> bytes:
    """Encode namespace id as fixed-width big-endian bytes."""
    n = int(ns)
    if n < 0 or n >= (1 << _NAMESPACE_BITS):
        raise NMTNodeError(
            f"namespace {n} out of range for NAMESPACE_BITS={_NAMESPACE_BITS}"
        )
    return n.to_bytes(_NS_BYTES, "big")


def leaf_hash(ns: int | NamespaceId, leaf_payload_hash: bytes) -> bytes:
    """
    Compute canonical NMT leaf hash:
        H(0x00 || ns_be || leaf_payload_hash)
    """
    h = _coerce_hash32(leaf_payload_hash, where="leaf_payload_hash")
    return sha3_256(LEAF_TAG + _ns_to_be(ns) + h)


def inner_hash(
    left_hash: bytes,
    right_hash: bytes,
    ns_min: int | NamespaceId,
    ns_max: int | NamespaceId,
) -> bytes:
    """
    Compute canonical NMT inner-node hash:
        H(0x01 || left_hash || right_hash || ns_min_be || ns_max_be)
    """
    lh = _coerce_hash32(left_hash, where="left_hash")
    rh = _coerce_hash32(right_hash, where="right_hash")
    if int(ns_min) > int(ns_max):
        raise NMTNodeError("ns_min must be <= ns_max")
    return sha3_256(NODE_TAG + lh + rh + _ns_to_be(ns_min) + _ns_to_be(ns_max))


# --------------------------------------------------------------------------- #
# Node type
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Node:
    """
    Internal NMT node.

    Attributes:
        hash:     32-byte node hash (see hashing rules above)
        ns_range: NamespaceRange covered by this node
        left:     Optional reference (index or None) to left child for builders
        right:    Optional reference (index or None) to right child for builders

    Note: Tree builders typically store nodes in arrays and use integer indices
    for left/right to avoid recursion; verifiers usually only need `hash` and
    `ns_range`.
    """

    hash: bytes
    ns_range: NamespaceRange
    left: Optional[int] = None
    right: Optional[int] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        _coerce_hash32(self.hash, where="Node.hash")  # validates length

    @property
    def ns_min(self) -> NamespaceId:
        return self.ns_range.min

    @property
    def ns_max(self) -> NamespaceId:
        return self.ns_range.max

    def __repr__(self) -> str:  # compact debug view
        return (
            f"Node(hash={bytes_to_hex(self.hash)[:10]}…, "
            f"ns=[{int(self.ns_min)}..{int(self.ns_max)}], "
            f"left={self.left}, right={self.right})"
        )


# --------------------------------------------------------------------------- #
# Constructors
# --------------------------------------------------------------------------- #


def make_leaf(ns: int | NamespaceId, leaf_payload_hash: bytes) -> Node:
    """
    Construct a leaf node from a namespace id and the hash of the serialized
    leaf payload.
    """
    ns_id = NamespaceId(int(ns))
    h = leaf_hash(ns_id, leaf_payload_hash)
    return Node(hash=h, ns_range=NamespaceRange(ns_id, ns_id), left=None, right=None)


def make_parent(
    left: Node,
    right: Optional[Node],
    *,
    duplicate_right_if_none: bool = True,
) -> Node:
    """
    Construct an inner node from left/right children.

    If `right` is None and `duplicate_right_if_none` is True, the right child
    is treated as an identical copy of the left (Bitcoin-style odd node rule).
    """
    if right is None:
        if not duplicate_right_if_none:
            raise NMTNodeError("right child missing and duplication not allowed")
        right = left

    # Range = union of children ranges (min of mins, max of maxes)
    ns_min = left.ns_min if int(left.ns_min) <= int(right.ns_min) else right.ns_min
    ns_max = left.ns_max if int(left.ns_max) >= int(right.ns_max) else right.ns_max
    rng = NamespaceRange(ns_min, ns_max)

    h = inner_hash(left.hash, right.hash, rng.min, rng.max)

    # In array-backed builders, left/right are indices; here we keep None to
    # avoid mixing object-graph with index-graph. Builders may replace.
    return Node(hash=h, ns_range=rng)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


def _coerce_hash32(h: bytes, *, where: str = "hash") -> bytes:
    if not isinstance(h, (bytes, bytearray, memoryview)):
        raise NMTNodeError(f"{where} must be bytes-like")
    b = bytes(h)
    if len(b) != 32:
        raise NMTNodeError(f"{where} must be 32 bytes, got {len(b)}")
    return b


__all__ = [
    "LEAF_TAG",
    "NODE_TAG",
    "NMTNodeError",
    "Node",
    "leaf_hash",
    "inner_hash",
    "make_leaf",
    "make_parent",
]
