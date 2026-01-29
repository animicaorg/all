"""
Animica • DA • NMT — Inclusion & namespace-range proofs (builders)

This module constructs Merkle proofs for the Animica Namespaced Merkle Tree.

What’s here
-----------
• InclusionProof: proof for a single leaf at index i (with namespace tagging)
• RangeProof:     compact multi-proof for a *contiguous* span of leaves
                  [start, start+count) — typically “all leaves of namespace X”

Notes & assumptions
-------------------
• The tree MUST be built over leaves ordered by non-decreasing namespace id
  (standard NMT invariant). Builders in this module do not re-order leaves.
• Proof verification lives in `da.nmt.verify`. This module only builds proofs
  from a finalized or previewed set of layers (`NMT.layers()`).
• When a layer has an odd number of nodes, the last node is duplicated
  (Bitcoin-style). We include such duplicated siblings explicitly in proofs to
  keep verification straightforward and independent of duplication policy.

Typical usage
-------------
    from da.nmt.tree import NMT
    from da.nmt.proofs import build_inclusion, build_namespace_range

    t = NMT()
    # ... append leaves ...
    root = t.finalize()

    # 1) Single-leaf inclusion
    inc = build_inclusion(t, index=5)

    # 2) All leaves for namespace 24
    rng = build_namespace_range(t, ns=24)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .namespace import NamespaceId, NamespaceRange
from .node import Node
from .tree import NMT

# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SiblingStep:
    """
    One step along the Merkle path:

      • level  : 0 = leaf layer, 1 = parents, ... up to root-1
      • side   : "L" if sibling is LEFT of the running hash,
                 "R" if sibling is RIGHT of the running hash
      • hash   : 32-byte sibling hash
      • ns_min / ns_max : namespace range covered by the sibling subtree
    """

    level: int
    side: str  # "L" | "R"
    hash: bytes
    ns_min: NamespaceId
    ns_max: NamespaceId


@dataclass(frozen=True)
class InclusionProof:
    leaf_index: int
    leaf_ns: NamespaceId
    leaf_payload_hash: bytes  # H(serialized payload) as used by leaf hashing
    siblings: List[SiblingStep]


@dataclass(frozen=True)
class RangeProof:
    """
    Multi-proof for a contiguous span of leaves [start, start+count).

    The verifier will be provided the *leaf payload hashes* for the span in
    order (or the full encoded leaves to hash), plus this proof to reconstruct
    the root.
    """

    start: int
    count: int
    ns_range: NamespaceRange  # union namespace range of the covered leaves
    siblings: List[SiblingStep]


# --------------------------------------------------------------------------- #
# Inclusion (single leaf)
# --------------------------------------------------------------------------- #


def build_inclusion(
    tree: NMT, index: int, *, leaf_payload_hash: Optional[bytes] = None
) -> InclusionProof:
    """
    Build an inclusion proof for the leaf at `index`.

    If `leaf_payload_hash` is provided, it is embedded in the proof object
    (useful for downstream verifiers). Otherwise, the caller can fill/compute
    it later.

    Raises:
        IndexError if index is out of range
        ValueError on malformed tree layers
    """
    layers = tree.layers()
    leaves = layers[0]
    n_leaves = len(leaves)
    if index < 0 or index >= n_leaves:
        raise IndexError(f"leaf index {index} out of range [0, {n_leaves})")

    siblings: List[SiblingStep] = []
    cur_index = index

    for level in range(len(layers) - 1):  # up to parent of root
        layer = layers[level]
        n = len(layer)

        if cur_index % 2 == 0:
            # current is LEFT child
            sib_index = cur_index + 1
            side = "R"  # sibling is on the RIGHT of current
            if sib_index >= n:
                # duplicate last
                sib_node = layer[cur_index]
            else:
                sib_node = layer[sib_index]
        else:
            # current is RIGHT child
            sib_index = cur_index - 1
            side = "L"  # sibling is on the LEFT of current
            sib_node = layer[sib_index]

        siblings.append(
            SiblingStep(
                level=level,
                side=side,
                hash=sib_node.hash,
                ns_min=sib_node.ns_min,
                ns_max=sib_node.ns_max,
            )
        )
        # ascend
        cur_index //= 2

    leaf_ns = leaves[index].ns_min  # == ns_max for a leaf
    return InclusionProof(
        leaf_index=index,
        leaf_ns=leaf_ns,
        leaf_payload_hash=leaf_payload_hash or b"",
        siblings=siblings,
    )


# --------------------------------------------------------------------------- #
# Namespace-range (contiguous span) multi-proof
# --------------------------------------------------------------------------- #


def build_range(tree: NMT, start: int, count: int) -> RangeProof:
    """
    Build a compact multi-proof for the contiguous span of leaves
    [start, start+count). This is the typical form for proving "all leaves
    belonging to namespace X" when leaves are grouped by namespace.

    The proof consists of the minimal set of sibling nodes required to
    reconstitute the root when the caller provides the leaf hashes for the
    span (or encoded leaves to hash).

    Raises:
        ValueError if count <= 0
        IndexError if the span is out of range
    """
    if count <= 0:
        raise ValueError("count must be > 0")
    layers = tree.layers()
    leaves = layers[0]
    n_leaves = len(leaves)
    end = start + count
    if start < 0 or end > n_leaves:
        raise IndexError(f"span [{start}, {end}) out of range [0, {n_leaves})")

    # Determine namespace range for the span from leaves.
    ns_min = leaves[start].ns_min
    ns_max = leaves[end - 1].ns_max
    ns_range = NamespaceRange(ns_min, ns_max)

    # Active set at the current level: indices of nodes that belong to the span.
    active = set(range(start, end))
    siblings: List[SiblingStep] = []

    # For each level, produce next-level active set and record needed siblings.
    for level in range(len(layers) - 1):
        layer = layers[level]
        next_active = set()

        visited = set()
        for i in sorted(active):
            if i in visited:
                continue
            # Determine sibling index for i at this level.
            if i % 2 == 0:
                sib_i = i + 1
                side = "R"
            else:
                sib_i = i - 1
                side = "L"

            # If sibling exists and is also active, we don't need to add a
            # sibling hash; we will carry the parent as active.
            if sib_i in active and sib_i < len(layer):
                # mark both visited and carry their parent
                visited.add(i)
                visited.add(sib_i)
                parent_index = i // 2
                next_active.add(parent_index)
            else:
                # We need the sibling hash (or a duplicate if missing)
                if 0 <= sib_i < len(layer):
                    sib_node = layer[sib_i]
                else:
                    # duplicate the current node when sibling is missing
                    sib_node = layer[i]
                siblings.append(
                    SiblingStep(
                        level=level,
                        side=side,
                        hash=sib_node.hash,
                        ns_min=sib_node.ns_min,
                        ns_max=sib_node.ns_max,
                    )
                )
                visited.add(i)
                parent_index = i // 2
                next_active.add(parent_index)

        active = next_active

    return RangeProof(
        start=start,
        count=count,
        ns_range=ns_range,
        siblings=siblings,
    )


def find_namespace_span(tree: NMT, ns: int | NamespaceId) -> Tuple[int, int]:
    """
    Scan the leaf layer to locate the contiguous span of leaves that have
    namespace == `ns`. Returns (start, count).

    Raises:
        ValueError if no leaves with the namespace are present.
    """
    target = NamespaceId(int(ns))
    leaves = tree.layers()[0]
    start = -1
    end = -1
    for i, lf in enumerate(leaves):
        leaf_ns = lf.ns_min  # leaf: min==max
        if int(leaf_ns) == int(target):
            if start == -1:
                start = i
            end = i + 1
        elif start != -1 and end == -1:
            # should not happen; kept for clarity
            end = i
    if start == -1:
        raise ValueError(f"namespace {int(target)} not present in leaves")
    if end == -1:
        end = start + 1
    # extend forward to include any subsequent equal-namespace leaves
    i = end
    while i < len(leaves) and int(leaves[i].ns_min) == int(target):
        i += 1
    end = i
    return start, end - start


def build_namespace_range(tree: NMT, ns: int | NamespaceId) -> RangeProof:
    """
    Convenience wrapper: build a RangeProof that covers *all* leaves whose
    namespace equals `ns`.

    Raises:
        ValueError if the namespace is absent from the tree.
    """
    start, count = find_namespace_span(tree, ns)
    return build_range(tree, start=start, count=count)


__all__ = [
    "SiblingStep",
    "InclusionProof",
    "RangeProof",
    "build_inclusion",
    "build_range",
    "find_namespace_span",
    "build_namespace_range",
]
