"""
core.utils.merkle
=================

Canonical Merkle helpers built on core.utils.hash:
- list_merkle_root([...])                  → root over arbitrary byte leaves
- kv_merkle_root({key: value, ...})        → root over a *sorted* map of key→value
- kv_merkle_proof(items, key[, value])     → membership proof for a KV entry
- kv_merkle_verify(root, key, value, ...)  → verify a KV proof

Design notes
------------
* We rely on domain-separated hashing in core.utils.hash:
  LEAF = 0x00 || leaf_bytes, NODE = 0x01 || left || right.
* For KV leaves we encode payload = 0x02 || u32be(len(key)) || key ||
                                  u32be(len(vhash)) || vhash,
  where vhash = sha3_256(value). This keeps leaves small and stable.
* KV entries are **sorted by raw key bytes (ascending)** prior to building the tree.
* Duplicate keys are rejected.
* An empty tree root is defined exactly as in core.utils.hash: leaf_hash(0x00 || b"").
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from .bytes import b as _b, BytesLike
from .hash import sha3_256, merkle_root as _merkle_root, merkle_proof as _merkle_proof, merkle_verify as _merkle_verify


# -----------------
# Leaf encodings
# -----------------

_KV_PAYLOAD_TAG = b"\x02"  # internal discriminator for KV payloads (in addition to outer LEAF tag)


def _u32be(n: int) -> bytes:
    if n < 0 or n > 0xFFFF_FFFF:
        raise ValueError("length out of range for u32")
    return n.to_bytes(4, "big")


def kv_leaf_bytes(key: BytesLike, value: BytesLike) -> bytes:
    """
    Encode a KV entry's *payload* bytes (before the LEAF domain tag).
    We hash the value (sha3_256) to cap leaf size and make proofs compact.
    """
    k = _b(key)
    vhash = sha3_256(_b(value))
    return _KV_PAYLOAD_TAG + _u32be(len(k)) + k + _u32be(len(vhash)) + vhash


# -----------------
# Public helpers
# -----------------

def list_merkle_root(leaves: Iterable[BytesLike]) -> bytes:
    """
    Root over arbitrary leaves (each leaf is raw bytes).
    """
    return _merkle_root(leaves, leaf_hash=sha3_256, node_hash=sha3_256, duplicate_odd=True)


def merkle_root(leaves: Iterable[BytesLike]) -> bytes:
    """Compatibility alias for list_merkle_root."""
    return list_merkle_root(leaves)


def kv_merkle_root(items: Mapping[BytesLike, BytesLike] | Iterable[Tuple[BytesLike, BytesLike]]) -> bytes:
    """
    Root over a set of key→value bindings.

    Accepts either a Mapping or an Iterable[(key, value)].
    Keys are sorted lexicographically (ascending) by raw bytes.
    Raises on duplicate keys (for Iterable input).
    """
    if isinstance(items, Mapping):
        pairs: List[Tuple[bytes, bytes]] = [( _b(k), _b(v) ) for k, v in items.items()]
    else:
        pairs = [( _b(k), _b(v) ) for k, v in items]

    # Sort & dedupe
    pairs.sort(key=lambda kv: kv[0])
    for i in range(1, len(pairs)):
        if pairs[i-1][0] == pairs[i][0]:
            raise ValueError("duplicate key in KV set")

    leaves = (kv_leaf_bytes(k, v) for (k, v) in pairs)
    return list_merkle_root(leaves)


def kv_merkle_proof(
    items: Mapping[BytesLike, BytesLike] | Iterable[Tuple[BytesLike, BytesLike]],
    key: BytesLike,
    value: BytesLike | None = None,
) -> Tuple[bytes, List[bytes], List[int]]:
    """
    Construct a membership proof for `key` in the given KV set.
    If `value` is provided, the proof binds to that exact value hash.
    If `value` is None and `items` is a Mapping, the value is looked up from it.

    Returns: (root, proof_hashes, directions)
    """
    # Normalize to sorted list of pairs
    if isinstance(items, Mapping):
        # If value not supplied, fetch from mapping (raises KeyError if missing)
        if value is None:
            value = items[_b(key)]
        pairs: List[Tuple[bytes, bytes]] = [( _b(k), _b(v) ) for k, v in items.items()]
    else:
        if value is None:
            raise ValueError("value must be provided when items is an Iterable")
        pairs = [( _b(k), _b(v) ) for k, v in items]

    pairs.sort(key=lambda kv: kv[0])
    # Locate index of the target key
    k_target = _b(key)
    idx = -1
    for i, (k, _v) in enumerate(pairs):
        if k == k_target:
            idx = i
            break
    if idx < 0:
        raise KeyError("key not present in items")

    # Build leaf sequence
    leaves = [kv_leaf_bytes(k, v) for (k, v) in pairs]
    # If caller supplied a value, make sure it matches the set (proof must bind to the chosen value)
    if value is not None:
        if sha3_256(_b(value)) != sha3_256(_b(pairs[idx][1])):
            # Still allow building a proof for the supplied (key, value) pair by replacing the leaf,
            # but this will verify to a *different* root than the set's root. Most callers want strict match:
            raise ValueError("supplied value does not match items' value for key")

    root, proof, dirs = _merkle_proof(leaves, idx, leaf_hash=sha3_256, node_hash=sha3_256, duplicate_odd=True)
    return root, proof, dirs


def kv_merkle_verify(
    root: bytes,
    key: BytesLike,
    value: BytesLike,
    proof: List[bytes],
    directions: List[int],
) -> bool:
    """
    Verify a KV membership proof against `root`.
    """
    leaf = kv_leaf_bytes(key, value)
    # The index is not required by the verifier for our domain-separated construction,
    # but we keep the API aligned with the low-level verify (it ignores shifting aside).
    # A caller may pass any non-negative placeholder; we use 0.
    return _merkle_verify(root, leaf, 0, proof, directions, leaf_hash=sha3_256, node_hash=sha3_256)


__all__ = [
    "merkle_root",
    "list_merkle_root",
    "kv_merkle_root",
    "kv_merkle_proof",
    "kv_merkle_verify",
    "kv_leaf_bytes",
]
