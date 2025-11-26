"""
Animica • DA • NMT — Incremental tree builder

This module provides a small, dependency-light Namespaced Merkle Tree (NMT)
builder with an *append → finalize* workflow.

It does not perform proof construction (see `da.nmt.proofs`) and it does not
impose a particular leaf encoding beyond the canonical hashing rules defined in
`da.nmt.node`. Convenience helpers are provided to append:

  • pre-hashed payloads:   append_hashed(ns, payload_hash)
  • raw payload bytes:     append_data(ns, payload_bytes)
  • encoded leaves:        append_encoded(encoded_leaf)

For encoded leaves we assume the canonical encoding:
    leaf := ns_be || uvarint(len) || data
and compute the leaf payload hash over the serialized payload:
    payload_serialized := uvarint(len) || data
so that leaf_hash = H(0x00 || ns_be || H(payload_serialized))
matching `da.nmt.node.leaf_hash`.

The builder duplicates the last node on odd node counts (Bitcoin-style) which
keeps tree sizes power-of-two aligned and proofs short.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

try:  # prefer canonical limits if available
    from ..constants import NAMESPACE_BITS as _NAMESPACE_BITS  # type: ignore
except Exception:  # pragma: no cover
    _NAMESPACE_BITS = 32

_NS_BYTES = (_NAMESPACE_BITS + 7) // 8

from .namespace import NamespaceId, NamespaceRange
from .node import Node, make_leaf, make_parent, NMTNodeError
from .codec import encode_leaf
from ..utils.hash import sha3_256
from ..utils.bytes import read_uvarint


@dataclass(frozen=True)
class TreeStats:
    leaves: int
    height: int  # number of layers including the leaf layer (>=1)
    ns_range: NamespaceRange


class NMT:
    """
    Append-only NMT builder with a simple finalize() step.

    Typical usage
    -------------
        t = NMT()
        idx = t.append_data(24, b"hello")
        root = t.finalize()

    Notes
    -----
    • The builder keeps leaf nodes and (on finalize) computes inner layers.
    • After finalize(), further appends are rejected. Create a new NMT for a
      new blob/chunk set.
    """

    def __init__(self, *, duplicate_last: bool = True, ns_bytes: int | None = None) -> None:
        self._duplicate_last = duplicate_last
        self._ns_bytes = int(ns_bytes) if ns_bytes is not None else _NS_BYTES
        self._leaves: List[Node] = []
        self._layers: Optional[List[List[Node]]] = None
        self._root: Optional[Node] = None
        self._finalized: bool = False

    # ------------------------------------------------------------------ #
    # Appends
    # ------------------------------------------------------------------ #

    def append_hashed(self, ns: int | NamespaceId, payload_hash: bytes) -> int:
        """
        Append a leaf given a namespace id and the *hash* of the serialized
        payload (see module docstring). Returns the leaf index.
        """
        self._ensure_not_finalized()
        ns_id = NamespaceId(int(ns))
        leaf = make_leaf(ns_id, _hash32(payload_hash, where="payload_hash"))
        self._leaves.append(leaf)
        return len(self._leaves) - 1

    def append_data(self, ns: int | NamespaceId, payload_bytes: bytes) -> int:
        """
        Convenience wrapper: hash the provided payload bytes directly as the
        serialized payload. If you use a structured leaf encoding, prefer
        `append_encoded`.
        """
        encoded = encode_leaf(ns, _b(payload_bytes), self._ns_bytes)
        leaf = _leaf_from_encoded(encoded, ns_bytes=self._ns_bytes)
        self._leaves.append(leaf)
        return len(self._leaves) - 1

    # Compatibility alias: several tests look for a generic ``append(ns, data)``
    # entry-point. Delegate to ``append_data`` to avoid duplicating logic.
    def append(self, ns: int | NamespaceId, payload_bytes: bytes) -> int:  # pragma: no cover - thin wrapper
        return self.append_data(ns, payload_bytes)

    def append_encoded(self, encoded_leaf: bytes) -> int:
        """
        Append an *encoded* leaf of the form:
            ns_be || uvarint(len) || data

        We parse just enough to extract the namespace and compute the payload
        hash over (uvarint(len)||data). This keeps the builder independent of
        the codec implementation details while matching the hashing rules.
        """
        self._ensure_not_finalized()
        b = _b(encoded_leaf)
        if len(b) < self._ns_bytes + 1:
            raise NMTNodeError("encoded leaf too short to contain namespace and length")
        leaf = _leaf_from_encoded(b, ns_bytes=self._ns_bytes)
        self._leaves.append(leaf)
        return len(self._leaves) - 1

    # ------------------------------------------------------------------ #
    # Finalize / root / stats
    # ------------------------------------------------------------------ #

    def finalize(self) -> bytes:
        """
        Compute the full set of inner layers and freeze the tree. Returns the
        Merkle root (32 bytes).
        """
        if self._finalized:
            assert self._root is not None
            return self._root.hash
        if not self._leaves:
            raise NMTNodeError("cannot finalize an empty tree")

        layers: List[List[Node]] = []
        cur: List[Node] = list(self._leaves)
        layers.append(cur)

        while len(cur) > 1:
            nxt: List[Node] = []
            n = len(cur)
            i = 0
            while i < n:
                left = cur[i]
                right: Optional[Node]
                if i + 1 < n:
                    right = cur[i + 1]
                else:
                    right = None
                parent = make_parent(left, right, duplicate_right_if_none=self._duplicate_last)
                nxt.append(parent)
                i += 2
            layers.append(nxt)
            cur = nxt

        self._layers = layers
        self._root = layers[-1][0]
        self._finalized = True
        return self._root.hash

    @property
    def root(self) -> bytes:
        """Return the Merkle root, finalizing if needed."""
        if not self._finalized:
            return self.finalize()
        assert self._root is not None
        return self._root.hash

    def stats(self) -> TreeStats:
        """Return simple statistics (leaves, height, namespace range)."""
        if not self._leaves:
            raise NMTNodeError("empty tree has no stats")
        rng = NamespaceRange(self._leaves[0].ns_min, self._leaves[0].ns_max)
        for lf in self._leaves[1:]:
            rng = NamespaceRange(
                rng.min if int(rng.min) <= int(lf.ns_min) else lf.ns_min,
                rng.max if int(rng.max) >= int(lf.ns_max) else lf.ns_max,
            )
        height = 1 if len(self._leaves) == 1 else len(self._compute_layers_preview())
        return TreeStats(leaves=len(self._leaves), height=height, ns_range=rng)

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    def layers(self) -> List[List[Node]]:
        """
        Return the computed layers from leaves up to the root.
        If not finalized yet, a temporary set is built (not cached).
        """
        if self._finalized and self._layers is not None:
            return self._layers
        return self._compute_layers_preview()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _ensure_not_finalized(self) -> None:
        if self._finalized:
            raise NMTNodeError("tree already finalized; cannot append")

    def _compute_layers_preview(self) -> List[List[Node]]:
        """Build layers without mutating internal state."""
        if not self._leaves:
            raise NMTNodeError("empty tree")
        layers: List[List[Node]] = []
        cur: List[Node] = list(self._leaves)
        layers.append(cur)
        while len(cur) > 1:
            nxt: List[Node] = []
            n = len(cur)
            i = 0
            while i < n:
                left = cur[i]
                right: Optional[Node]
                if i + 1 < n:
                    right = cur[i + 1]
                else:
                    right = None
                parent = make_parent(left, right, duplicate_right_if_none=self._duplicate_last)
                nxt.append(parent)
                i += 2
            layers.append(nxt)
            cur = nxt
        return layers


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #

def _b(x: bytes | bytearray | memoryview) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return x.tobytes()
    return bytes(x)  # pragma: no cover - defensive

def _hash32(h: bytes, *, where: str = "hash") -> bytes:
    b = _b(h)
    if len(b) != 32:
        raise NMTNodeError(f"{where} must be 32 bytes, got {len(b)}")
    return b


def _leaf_from_encoded(encoded_leaf: bytes, *, ns_bytes: int = _NS_BYTES) -> Node:
    """Create a :class:`Node` directly from an encoded leaf buffer."""
    if len(encoded_leaf) < ns_bytes + 1:
        raise NMTNodeError("encoded leaf too short to contain namespace and length")
    ns = int.from_bytes(encoded_leaf[:ns_bytes], "big")
    _, off_after_len = read_uvarint(encoded_leaf, offset=ns_bytes)
    if off_after_len > len(encoded_leaf):
        raise NMTNodeError("encoded leaf length varint overruns buffer")
    ns_id = NamespaceId(ns)
    rng = NamespaceRange(ns_id, ns_id)
    h = sha3_256(encoded_leaf)
    return Node(hash=h, ns_range=rng)


__all__ = ["NMT", "TreeStats"]
