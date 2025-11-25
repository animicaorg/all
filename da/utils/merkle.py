"""
Animica • DA utilities — Generic Merkle helpers

This module provides small, dependency-free helpers for working with binary
Merkle trees using arbitrary hash functions. It is *not* NMT-specific; the
Namespaced Merkle Tree (NMT) logic lives under `da.nmt.*` and consumes these
generic utilities for basic branch construction and verification.

Design choices
--------------
• Leaves are provided as *already-hashed* 32-byte digests by default. This
  keeps the helpers generic; callers decide how to hash leaves.
• Inner-node hashing defaults to `sha3_256(left || right)`, but can be
  overridden via `combine` (callable).
• Odd-node handling duplicates the last node in a layer (Bitcoin-style) by
  default; pass `duplicate_last=False` to error on odd counts instead.

Key functions
-------------
- merkle_root(leaf_hashes)
- build_proof(leaf_hashes, index)
- verify_proof(leaf_hash, index, proof, root)
- build_compact_proofs(leaf_hashes, indices)
- verify_compact_proof(leaf_hash, index, nodes, path, root)

Where a *proof* is a list of `ProofStep(dir, sibling)` entries and a *compact
proof* deduplicates sibling nodes across many proofs, returning `(nodes, paths)`
where each path is a list of `PathStepRef(dir, ref)` referencing `nodes[ref]`.

These helpers are used by:

• da.nmt.verify      (to stitch together inclusion checks)
• da.sampling.verify (to verify sets of samples)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, List, Sequence, Tuple

from .hash import sha3_256

Hash = bytes  # convention: 32-byte digests unless caller overrides

# --------------------------------------------------------------------------- #
# Hash combiner
# --------------------------------------------------------------------------- #

def default_combine(left: Hash, right: Hash) -> Hash:
    """
    Default inner-node combiner: SHA3-256(left || right).

    Preconditions:
        - len(left) == len(right) (typically 32)
    """
    if len(left) != len(right):
        raise ValueError("left/right hash length mismatch")
    return sha3_256(left + right)


# --------------------------------------------------------------------------- #
# Proof step data structures
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ProofStep:
    """
    One hop in a classic inclusion proof.

    dir: 0 if the current hash was on the left (sibling is right),
         1 if the current hash was on the right (sibling is left).
    sibling: the sibling node's hash bytes.
    """
    dir: int  # 0 = current-left, 1 = current-right
    sibling: Hash

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.dir not in (0, 1):
            raise ValueError("ProofStep.dir must be 0 or 1")
        if not isinstance(self.sibling, (bytes, bytearray, memoryview)):
            raise TypeError("ProofStep.sibling must be bytes-like")


@dataclass(frozen=True)
class PathStepRef:
    """
    One hop in a compact proof path, referencing a shared node pool.

    dir: 0 if current hash was left (sibling on right), 1 if current was right.
    ref: index into the `nodes` array (shared across proofs).
    """
    dir: int
    ref: int

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.dir not in (0, 1):
            raise ValueError("PathStepRef.dir must be 0 or 1")
        if self.ref < 0:
            raise ValueError("PathStepRef.ref must be >= 0")


# --------------------------------------------------------------------------- #
# Root / tree building
# --------------------------------------------------------------------------- #

def merkle_root(
    leaf_hashes: Sequence[Hash],
    *,
    combine: Callable[[Hash, Hash], Hash] = default_combine,
    duplicate_last: bool = True,
) -> Hash:
    """
    Compute the Merkle root from a sequence of leaf hashes.

    Args:
        leaf_hashes: sequence of pre-hashed leaves (len >= 1).
        combine:     inner node combiner (default sha3_256(left||right)).
        duplicate_last: if True, duplicate odd last node in a layer.

    Returns:
        Root hash bytes.

    Raises:
        ValueError if leaf_hashes is empty or odd count with duplicate_last=False.
    """
    if not leaf_hashes:
        raise ValueError("cannot compute root over empty leaf set")

    layer: List[Hash] = list(leaf_hashes)

    while len(layer) > 1:
        n = len(layer)
        next_layer: List[Hash] = []
        i = 0
        while i < n:
            j = i + 1
            left = layer[i]
            if j < n:
                right = layer[j]
            else:
                if not duplicate_last:
                    raise ValueError("odd node count without duplicate_last")
                right = left
            next_layer.append(combine(left, right))
            i += 2
        layer = next_layer

    return layer[0]


# --------------------------------------------------------------------------- #
# Classic (non-compact) proofs
# --------------------------------------------------------------------------- #

def build_proof(
    leaf_hashes: Sequence[Hash],
    index: int,
    *,
    combine: Callable[[Hash, Hash], Hash] = default_combine,
    duplicate_last: bool = True,
) -> List[ProofStep]:
    """
    Build a classic inclusion proof for `leaf_hashes[index]`.

    Returns:
        List of ProofStep from leaf layer up towards the root (excluding root).
    """
    n = len(leaf_hashes)
    if n == 0:
        raise ValueError("cannot build proof over empty leaf set")
    if not (0 <= index < n):
        raise IndexError("index out of range")

    # Work on a copy
    layer: List[Hash] = list(leaf_hashes)
    idx = index
    proof: List[ProofStep] = []

    while len(layer) > 1:
        n = len(layer)
        next_layer: List[Hash] = []
        next_idx = idx // 2

        for i in range(0, n, 2):
            left = layer[i]
            right: Hash
            if i + 1 < n:
                right = layer[i + 1]
            else:
                if not duplicate_last:
                    raise ValueError("odd node count without duplicate_last")
                right = left

            # If our index is within this pair, record sibling accordingly.
            if i == idx or i + 1 == idx:
                if idx == i:  # current at left
                    proof.append(ProofStep(dir=0, sibling=right))
                else:          # current at right
                    proof.append(ProofStep(dir=1, sibling=left))

            next_layer.append(combine(left, right))

        layer = next_layer
        idx = next_idx

    return proof


def verify_proof(
    leaf_hash: Hash,
    index: int,
    proof: Sequence[ProofStep],
    root: Hash,
    *,
    combine: Callable[[Hash, Hash], Hash] = default_combine,
) -> bool:
    """
    Verify an inclusion proof for a leaf at `index`.

    This does not check `index` against the tree size; it is only used to
    decide left/right ordering while walking the proof.
    """
    acc = leaf_hash
    idx = index
    for step in proof:
        if step.dir == 0:  # current at left, sibling at right
            acc = combine(acc, _b(step.sibling))
        else:              # current at right, sibling at left
            acc = combine(_b(step.sibling), acc)
        idx //= 2
    return acc == root


# --------------------------------------------------------------------------- #
# Compact multi-proof builder / verifier
# --------------------------------------------------------------------------- #

def build_compact_proofs(
    leaf_hashes: Sequence[Hash],
    indices: Sequence[int],
    *,
    combine: Callable[[Hash, Hash], Hash] = default_combine,
    duplicate_last: bool = True,
) -> Tuple[List[Hash], List[List[PathStepRef]]]:
    """
    Build *compact* proofs for many leaves by deduplicating sibling nodes.

    Args:
        leaf_hashes: pre-hashed leaves.
        indices:     leaf indices to prove (unique or not; duplicates allowed).
        combine:     inner combiner.
        duplicate_last: odd-node handling (see merkle_root).

    Returns:
        (nodes, paths) where:
          - nodes: array of unique sibling hashes (Hash)
          - paths: for each index in `indices`, a list of PathStepRef(dir, ref)

    Notes:
        - The order of `nodes` is deterministic given the inputs.
        - Paths reference `nodes` via indices. Safe to serialize as per
          da/schemas/retrieval_api.schema.json (ProofCompact).
    """
    if not leaf_hashes:
        raise ValueError("cannot build proofs over empty leaf set")
    if not indices:
        return [], []

    n = len(leaf_hashes)
    for idx in indices:
        if not (0 <= idx < n):
            raise IndexError("proof index out of range")

    # Prepare mutable copies
    layer: List[Hash] = list(leaf_hashes)
    working_indices = list(indices)
    # For each requested index, keep an accumulating path (of node *values*);
    # we'll convert to refs at the end after deduplication.
    raw_paths: List[List[Tuple[int, Hash]]] = [[] for _ in working_indices]

    while len(layer) > 1:
        n = len(layer)
        next_layer: List[Hash] = []
        # Map from current-layer leaf index -> pair base (i) to speed lookups
        pair_base_for_index = {i: i - (i % 2) for i in range(n)}
        # Build all pairs
        for i in range(0, n, 2):
            left = layer[i]
            if i + 1 < n:
                right = layer[i + 1]
            else:
                if not duplicate_last:
                    raise ValueError("odd node count without duplicate_last")
                right = left
            next_layer.append(combine(left, right))

        # Record siblings for all tracked indices at this layer
        for p, idx in enumerate(working_indices):
            base = pair_base_for_index[idx]
            left_i, right_i = base, min(base + 1, n - 1)
            if idx == left_i:
                sibling = layer[right_i]
                raw_paths[p].append((0, sibling))  # current-left
            else:
                sibling = layer[left_i]
                raw_paths[p].append((1, sibling))  # current-right

        # Move indices up to next layer
        working_indices = [i // 2 for i in working_indices]
        layer = next_layer

    # Deduplicate nodes across all raw_paths
    nodes: List[Hash] = []
    to_ref: dict[Hash, int] = {}
    paths: List[List[PathStepRef]] = []

    for rp in raw_paths:
        path_refs: List[PathStepRef] = []
        for d, sib in rp:
            ref = to_ref.get(sib)
            if ref is None:
                ref = len(nodes)
                nodes.append(sib)
                to_ref[sib] = ref
            path_refs.append(PathStepRef(dir=d, ref=ref))
        paths.append(path_refs)

    return nodes, paths


def verify_compact_proof(
    leaf_hash: Hash,
    index: int,
    nodes: Sequence[Hash],
    path: Sequence[PathStepRef],
    root: Hash,
    *,
    combine: Callable[[Hash, Hash], Hash] = default_combine,
) -> bool:
    """
    Verify one compact proof path against the shared `nodes` table.
    """
    acc = leaf_hash
    idx = index
    for step in path:
        try:
            sibling = nodes[step.ref]
        except IndexError:
            return False
        if step.dir == 0:
            acc = combine(acc, _b(sibling))
        else:
            acc = combine(_b(sibling), acc)
        idx //= 2
    return acc == root


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
    return bytes(x)  # pragma: no cover - defensive fallback


__all__ = [
    "Hash",
    "default_combine",
    "ProofStep",
    "PathStepRef",
    "merkle_root",
    "build_proof",
    "verify_proof",
    "build_compact_proofs",
    "verify_compact_proof",
]
