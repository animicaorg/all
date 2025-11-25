"""
Animica • DA • NMT — Index math (leaf ↔ share, paths, range cover)

This module centralizes the "index arithmetic" for the Namespaced Merkle Tree
(NMT) layer. It uses a classic *heap-style* array layout for a *perfect* binary
tree whose leaf count is padded up to a power of two.

Conventions
-----------
• Leaves are 0-based, left→right:   leaf_idx ∈ [0, leaf_count_padded)
• Heap indices are 0-based with root at 0. For a tree with L = log2(leaf_count_padded)
  leaf level = L, and the first leaf node lives at heap index:
      base = 2**L - 1
  so: node_index_of_leaf(i) = base + i

• In *NMT context*, a "share" is exactly one encoded leaf (namespace || len || data).
  Therefore, at this layer:    share_index == leaf_index (identity mapping).
  The erasure-coded *matrix* layout (row/column, extended width) lives in
  `da/erasure/layout.py`. Keep the two concerns separate.

What you can do here
--------------------
• Compute padding, heights, base offsets
• Convert leaf <-> heap node indices
• Walk a leaf's path to root, siblings included
• Compute the minimal set of tree nodes that *covers* a contiguous leaf range
  (useful when constructing compact multi-proofs)
• Identity “share ↔ leaf” helpers for NMT

All helpers are pure and bounds-checked. Invalid inputs raise ValueError.

Example
-------
>>> padded = padded_leaf_count(5)      # -> 8
>>> height = tree_height_from_leaves(padded)  # -> 3
>>> base = leaf_base_index(padded)     # -> 7
>>> node_index_of_leaf(2, padded)      # -> 9
>>> list(path_to_root_leaf_nodes(2, padded))
[(9, 8), (4, 5), (1, 2)]  # (node, sibling) pairs up to but not including root

>>> cover = cover_range_as_nodes(leaf_start=2, leaf_count=3, padded_leaves=8)
# minimal set of nodes whose union covers leaves [2,5)
# returns a list of (level, offset) pairs (level 0 = root)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generator, Iterable, Iterator, List, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Basic tree arithmetic
# --------------------------------------------------------------------------- #

def is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def next_power_of_two(n: int) -> int:
    if n <= 0:
        raise ValueError("n must be positive")
    return 1 << (n - 1).bit_length()


def padded_leaf_count(leaf_count: int) -> int:
    """
    Return the leaf count padded up to the next power of two (identity if already power-of-two).
    """
    if leaf_count <= 0:
        raise ValueError("leaf_count must be positive")
    return leaf_count if is_power_of_two(leaf_count) else next_power_of_two(leaf_count)


def tree_height_from_leaves(padded_leaves: int) -> int:
    """
    Height (number of *edges* from root to leaf) for a perfect binary tree with `padded_leaves`.
    For padded_leaves = 2**L  => height = L.
    """
    if not is_power_of_two(padded_leaves):
        raise ValueError("padded_leaves must be a power of two")
    return padded_leaves.bit_length() - 1


def leaf_base_index(padded_leaves: int) -> int:
    """
    Heap-array index of the *first* leaf node (i = 0).
    """
    L = tree_height_from_leaves(padded_leaves)
    return (1 << L) - 1  # 2**L - 1


# --------------------------------------------------------------------------- #
# Leaf/node index conversions (heap layout)
# --------------------------------------------------------------------------- #

def node_index_of_leaf(leaf_idx: int, padded_leaves: int) -> int:
    if leaf_idx < 0 or leaf_idx >= padded_leaves:
        raise ValueError("leaf_idx out of range")
    return leaf_base_index(padded_leaves) + leaf_idx


def leaf_index_of_node(node_idx: int, padded_leaves: int) -> int:
    """
    Convert a heap node index to leaf index if it is a leaf node; else raises ValueError.
    """
    base = leaf_base_index(padded_leaves)
    if node_idx < base or node_idx >= base + padded_leaves:
        raise ValueError("node_idx is not a leaf node for this tree")
    return node_idx - base


def parent_index(node_idx: int) -> int:
    if node_idx <= 0:
        raise ValueError("root has no parent")
    return (node_idx - 1) // 2


def sibling_index(node_idx: int) -> int:
    if node_idx <= 0:
        raise ValueError("root has no sibling")
    return node_idx - 1 if (node_idx % 2) else node_idx + 1


# --------------------------------------------------------------------------- #
# Level/offset representation <-> heap index
# --------------------------------------------------------------------------- #

def node_index_from_level_offset(level: int, offset: int) -> int:
    """
    Level 0 is root, level L is leaves. Offset ∈ [0, 2**level).
    """
    if level < 0 or offset < 0 or offset >= (1 << level):
        raise ValueError("invalid level/offset")
    return (1 << level) - 1 + offset


def level_offset_from_index(node_idx: int) -> Tuple[int, int]:
    """
    Inverse of node_index_from_level_offset.
    """
    if node_idx < 0:
        raise ValueError("node_idx must be non-negative")
    # Largest power-of-two minus one that is <= node_idx gives the level start
    level = (node_idx + 1).bit_length() - 1
    start = (1 << level) - 1
    # If node_idx == start, we guessed level correctly; else node lives at previous level
    if node_idx < start:
        level -= 1
        start = (1 << level) - 1
    offset = node_idx - start
    return level, offset


# --------------------------------------------------------------------------- #
# Paths & coverage
# --------------------------------------------------------------------------- #

def path_to_root_leaf_nodes(leaf_idx: int, padded_leaves: int) -> Iterator[Tuple[int, int]]:
    """
    Yield (node_idx, sibling_idx) pairs for the path from the leaf to just below the root.
    Does not include the root itself. Order: bottom-up.

    Example (padded_leaves=8, leaf_idx=2):
        yields: (9, 8), (4, 5), (1, 2)
    """
    cur = node_index_of_leaf(leaf_idx, padded_leaves)
    while cur > 0:
        sib = sibling_index(cur)
        yield (cur, sib)
        cur = parent_index(cur)


def cover_range_as_level_offsets(
    leaf_start: int,
    leaf_count: int,
    padded_leaves: int,
) -> List[Tuple[int, int]]:
    """
    Decompose the half-open range [leaf_start, leaf_start+leaf_count) into a
    *minimal* set of full subtrees, returned as (level, offset) pairs
    (level 0 = root). This is the standard segment-tree decomposition.

    Preconditions:
      • 0 <= leaf_start < padded_leaves
      • 1 <= leaf_count <= padded_leaves - leaf_start
      • padded_leaves is a power of two
    """
    if not is_power_of_two(padded_leaves):
        raise ValueError("padded_leaves must be a power of two")
    if leaf_start < 0 or leaf_start >= padded_leaves:
        raise ValueError("leaf_start out of range")
    if leaf_count <= 0 or leaf_start + leaf_count > padded_leaves:
        raise ValueError("leaf_count out of range")

    L = tree_height_from_leaves(padded_leaves)
    res: List[Tuple[int, int]] = []

    left = leaf_start
    right = leaf_start + leaf_count  # exclusive

    # We operate at the leaf level then climb up while aligning bounds.
    # Represent ranges as offsets at level 'lvl'.
    lvl = L
    left_off = left
    right_off = right

    while left_off < right_off:
        # Greedily take aligned blocks at current level.
        # If left is odd, take the single block covering it.
        if left_off & 1:
            res.append((lvl, left_off))
            left_off += 1
        # If right is odd (i.e. last block is a left child), take the preceding block.
        if right_off & 1:
            right_off -= 1
            res.append((lvl, right_off))

        # Move up a level (merge pairs).
        left_off >>= 1
        right_off >>= 1
        lvl -= 1

    # We built blocks from various levels; convert offsets at each level into (level, offset_at_level)
    # Note: above, offsets were actually at the current 'lvl'. That's what we want already.
    # We only need to normalize that offsets are within [0, 2**level). They are by construction.

    # Sort parents before children for deterministic order (rootmost first).
    res.sort(key=lambda t: (t[0], t[1]))
    return res


def cover_range_as_nodes(
    leaf_start: int,
    leaf_count: int,
    padded_leaves: int,
) -> List[int]:
    """
    Same as `cover_range_as_level_offsets` but returns *heap node indices*.
    """
    los = cover_range_as_level_offsets(leaf_start, leaf_count, padded_leaves)
    return [node_index_from_level_offset(level, offset) for level, offset in los]


# --------------------------------------------------------------------------- #
# Share ↔ leaf helpers (identity at NMT layer)
# --------------------------------------------------------------------------- #

def share_index_from_leaf_index(leaf_idx: int) -> int:
    """
    In NMT, a "share" is a leaf. Identity mapping.
    """
    if leaf_idx < 0:
        raise ValueError("leaf_idx must be non-negative")
    return leaf_idx


def leaf_index_from_share_index(share_idx: int) -> int:
    """
    In NMT, a "share" is a leaf. Identity mapping.
    """
    if share_idx < 0:
        raise ValueError("share_idx must be non-negative")
    return share_idx


__all__ = [
    # power-of-two & padding
    "is_power_of_two",
    "next_power_of_two",
    "padded_leaf_count",
    "tree_height_from_leaves",
    "leaf_base_index",
    # conversions & relatives
    "node_index_of_leaf",
    "leaf_index_of_node",
    "parent_index",
    "sibling_index",
    "node_index_from_level_offset",
    "level_offset_from_index",
    # paths & cover
    "path_to_root_leaf_nodes",
    "cover_range_as_level_offsets",
    "cover_range_as_nodes",
    # share ↔ leaf
    "share_index_from_leaf_index",
    "leaf_index_from_share_index",
]
