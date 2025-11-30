"""
core.utils.hash
===============

Thin, fast wrappers for common hashes plus canonical tree-hash helpers.

Provided digests (all return `bytes`):
- sha3_256(data)
- sha3_512(data)
- blake3(data, digest_size=32)         # optional; raises if not available
- keccak256(data)                      # optional; raises if not available

Tree/Merkle helpers (domain-separated with leaf/node prefixes):
- merkle_root(leaves, *, leaf_hash=sha3_256, node_hash=sha3_256, duplicate_odd=True)
- merkle_proof(leaves, index, *, leaf_hash=..., node_hash=..., duplicate_odd=True)
- merkle_verify(root, leaf, index, proof, *, leaf_hash=..., node_hash=..., duplicate_odd=True)

Determinism notes
-----------------
- We prepend a 1-byte domain tag to every node prior to hashing:
    LEAF = 0x00 || leaf_bytes
    NODE = 0x01 || left_node_hash || right_node_hash
  This avoids ambiguous trees and cross-protocol collisions.
- Empty tree root is defined as: leaf_hash(0x00 || b"")  (a single empty leaf).

No external deps are required; BLAKE3/Keccak are optional-fastpaths.

"""

from __future__ import annotations

import hashlib
from typing import Iterable, List, Tuple

from .bytes import BytesLike
from .bytes import b as _b

# Common constants
ZERO32 = b"\x00" * 32

# -----------------------
# Optional accelerated algs
# -----------------------

# BLAKE3 is optional (pip install blake3). We expose a nice error if missing.
try:
    import blake3 as _blake3  # type: ignore

    _HAS_BLAKE3 = True
except Exception:  # pragma: no cover - environment-dependent
    _blake3 = None  # type: ignore
    _HAS_BLAKE3 = False

# Keccak-256 is available if `pysha3` is installed; importing `sha3` monkey-patches hashlib.
# (On some platforms hashlib already provides it.)
_HAS_KECCAK = False
try:  # pragma: no cover - environment-dependent
    import sha3 as _sha3  # type: ignore  # noqa: F401

    _HAS_KECCAK = hasattr(hashlib, "keccak_256")
except Exception:  # pragma: no cover - environment-dependent
    _HAS_KECCAK = hasattr(hashlib, "keccak_256")


# ------------
# Hashes (bytes in → bytes out)
# ------------


def sha3_256(data: BytesLike) -> bytes:
    """SHA3-256 digest."""
    return hashlib.sha3_256(_b(data)).digest()


def sha3_512(data: BytesLike) -> bytes:
    """SHA3-512 digest."""
    return hashlib.sha3_512(_b(data)).digest()


def blake3(data: BytesLike, *, digest_size: int = 32) -> bytes:
    """BLAKE3 digest (variable output). Requires the `blake3` package."""
    if not _HAS_BLAKE3:
        raise RuntimeError(
            "blake3() requires the 'blake3' package (pip install blake3)"
        )
    h = _blake3.blake3(_b(data), digest_size=digest_size)  # type: ignore[attr-defined]
    return h.digest()


def keccak256(data: BytesLike) -> bytes:
    """Keccak-256 digest (Ethereum-style). Requires hashlib.keccak_256 support or pysha3."""
    if not _HAS_KECCAK:
        raise RuntimeError(
            "keccak256() requires hashlib.keccak_256 (install `pysha3` if missing)"
        )
    return hashlib.keccak_256(_b(data)).digest()  # type: ignore[attr-defined]


# ------------
# Tree / Merkle helpers
# ------------

_LEAF_TAG = b"\x00"
_NODE_TAG = b"\x01"


def _leaf_hash(leaf: BytesLike, hfunc) -> bytes:
    return hfunc(_LEAF_TAG + _b(leaf))


def _node_hash(left_hash: bytes, right_hash: bytes, hfunc) -> bytes:
    return hfunc(_NODE_TAG + left_hash + right_hash)


def merkle_root(
    leaves: Iterable[BytesLike],
    *,
    leaf_hash=sha3_256,
    node_hash=sha3_256,
    duplicate_odd: bool = True,
) -> bytes:
    """
    Compute a canonical Merkle root over `leaves` using domain-separated hashing.

    - `duplicate_odd=True`: duplicate the last hash when a level has an odd count.
      (Deterministic; matches many blockchain trees.)
    - Empty tree root = leaf_hash(0x00 || b"")
    """
    # Prepare base level (hash leaves with LEAF tag)
    hashed: List[bytes] = [_leaf_hash(x, leaf_hash) for x in leaves]
    if not hashed:
        return _leaf_hash(b"", leaf_hash)

    # Build up the tree
    while len(hashed) > 1:
        nxt: List[bytes] = []
        n = len(hashed)
        last = n - 1
        i = 0
        while i < n:
            j = i + 1
            if j <= last:
                nxt.append(_node_hash(hashed[i], hashed[j], node_hash))
                i += 2
            else:
                if duplicate_odd:
                    nxt.append(_node_hash(hashed[i], hashed[i], node_hash))
                else:
                    # Promote single child
                    nxt.append(hashed[i])
                i += 1
        hashed = nxt
    return hashed[0]


def merkle_proof(
    leaves: Iterable[BytesLike],
    index: int,
    *,
    leaf_hash=sha3_256,
    node_hash=sha3_256,
    duplicate_odd: bool = True,
) -> Tuple[bytes, List[bytes], List[int]]:
    """
    Build a proof for leaf at `index`.

    Returns (root, proof_hashes, directions)
    - proof_hashes[i] is the sibling hash at level i, from bottom to top.
    - directions[i] is 0 if the sibling is on the RIGHT (current is left),
                     1 if the sibling is on the LEFT  (current is right).
    """
    # Materialize leaves & basic checks
    _leaves = list(leaves)
    n = len(_leaves)
    if not (0 <= index < n):
        raise IndexError(f"index {index} out of range for {n} leaves")

    # Initial layer
    layer: List[bytes] = [_leaf_hash(x, leaf_hash) for x in _leaves]
    proof: List[bytes] = []
    dirs: List[int] = []
    idx = index

    if n == 0:
        root = _leaf_hash(b"", leaf_hash)
        return root, [], []

    while len(layer) > 1:
        nxt: List[bytes] = []
        # walk pairs
        for i in range(0, len(layer), 2):
            j = i + 1
            if j < len(layer):
                left, right = layer[i], layer[j]
                nxt.append(_node_hash(left, right, node_hash))
                # if our idx is here, record sibling
                if idx == i:
                    proof.append(right)
                    dirs.append(0)  # sibling on RIGHT
                    idx = len(nxt) - 1
                elif idx == j:
                    proof.append(left)
                    dirs.append(1)  # sibling on LEFT
                    idx = len(nxt) - 1
            else:
                # odd count
                if duplicate_odd:
                    left, right = layer[i], layer[i]
                    nxt.append(_node_hash(left, right, node_hash))
                    if idx == i:
                        proof.append(right)  # which equals left
                        dirs.append(0)
                        idx = len(nxt) - 1
                else:
                    nxt.append(layer[i])
                    # if idx==i, it gets promoted; no proof element at this level
                    if idx == i:
                        idx = len(nxt) - 1
        layer = nxt

    root = layer[0]
    return root, proof, dirs


def merkle_verify(
    root: bytes,
    leaf: BytesLike,
    index: int,
    proof: List[bytes],
    directions: List[int],
    *,
    leaf_hash=sha3_256,
    node_hash=sha3_256,
) -> bool:
    """
    Verify (root, leaf, index, proof) under our domain-separated Merkle rules.
    """
    if len(proof) != len(directions):
        return False
    h = _leaf_hash(leaf, leaf_hash)
    idx = index
    for sib, d in zip(proof, directions):
        if d not in (0, 1):
            return False
        if d == 1:  # sibling on LEFT
            h = _node_hash(sib, h, node_hash)
        else:  # sibling on RIGHT
            h = _node_hash(h, sib, node_hash)
        idx >>= 1  # for completeness; not needed for computation
    return h == root


__all__ = [
    "sha3_256",
    "sha3_512",
    "blake3",
    "keccak256",
    "merkle_root",
    "merkle_proof",
    "merkle_verify",
]
