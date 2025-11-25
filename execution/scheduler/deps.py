"""
execution.scheduler.deps — minimal dependency graph from access lists.

This module builds a conservative *precedence graph* (DAG) for a sequence of
transactions or tasks using either:

  • Declared access lists (pre-execution; conservative), or
  • Captured LockSets (post-simulation; precise),

then derives parallel-execution batches (topological layers) that **preserve the
original order** and avoid read/write conflicts.

Design goals
------------
- Pure functions, no side-effects.
- O(n²) construction (simple and fine for block-sized n).
- Stable tie-breaking by original index when producing layers.
- Works even when some items have no information (treated as fully conflicting
  with all earlier items to preserve safety).

Key notions
-----------
We consider conflicts at the granularity of (address, storage_key?):
  • W/W, W/R, and R/W overlap ⇒ conflict
  • R/R overlap ⇒ no conflict

Access lists vs locksets
------------------------
- Access lists (EIP-2930-like) are *hints*, not exact. By default we treat
  listed keys as **writes** (conservative), but you can flip to reads if your
  pipeline guarantees write sets elsewhere.
- LockSets are exact captures from the executor’s access tracker.

Typical usage
-------------
    # From declared access lists (pre-exec planning)
    graph = build_graph_from_access_lists(access_lists, conservative_writes=True)
    batches = topo_layers(graph)   # [[0,2,5], [1,4], [3], ...]

    # From captured locksets (post-sim precise plan)
    graph = build_graph_from_locksets(locksets)
    batches = topo_layers(graph)

API
---
- build_graph_from_access_lists(access_lists, conservative_writes=True) -> DepGraph
- build_graph_from_locksets(locksets) -> DepGraph
- topo_layers(graph) -> List[List[int]]
- conflicts_matrix(locksets|access_lists) -> Set[Tuple[int,int]]  (diagnostics)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

from .lockset import LockSet, lockset_from_access_list, normalize_key


# ------------------------------ Data model ----------------------------------


@dataclass
class DepGraph:
    """Directed acyclic graph over indices [0..n-1] with adjacency lists."""
    n: int
    edges: Dict[int, Set[int]] = field(default_factory=dict)  # i -> {j,...}
    indeg: List[int] = field(default_factory=list)

    def copy(self) -> "DepGraph":
        return DepGraph(
            n=self.n,
            edges={u: set(vs) for u, vs in self.edges.items()},
            indeg=list(self.indeg),
        )

    def successors(self, u: int) -> Set[int]:
        return self.edges.get(u, set())


# ------------------------------ Helpers -------------------------------------


def _mk_indeg(n: int, edges: Dict[int, Set[int]]) -> List[int]:
    indeg = [0] * n
    for u, vs in edges.items():
        for v in vs:
            indeg[v] += 1
    return indeg


def _conflict_locksets(a: LockSet, b: LockSet) -> bool:
    return a.conflicts_with(b)


def _lockset_from_read_only_access_list(access_list: Sequence) -> LockSet:
    """
    Treat entries as *reads* (less conservative). Structure mirrors
    lockset_from_access_list (which treats entries as writes).
    """
    reads: Set[Tuple[bytes, Optional[bytes]]] = set()
    writes: Set[Tuple[bytes, Optional[bytes]]] = set()
    for ent in access_list:
        if isinstance(ent, dict):
            addr = ent.get("address")
            slots = ent.get("storageKeys") or []
        else:
            addr, slots = ent
        if not slots:
            reads.add(normalize_key(addr, None))
        else:
            for s in slots:
                reads.add(normalize_key(addr, s))
    return LockSet.from_pairs(reads=reads, writes=writes)


def _full_write_lockset() -> LockSet:
    """
    Sentinel 'unknown' lockset that conservatively conflicts with everything
    by using a special wildcard key. We model it as writing a unique address '*'.
    """
    wildcard = (b"*", None)
    return LockSet.from_pairs(writes=[wildcard])


# --------------------------- Graph construction -----------------------------


def build_graph_from_locksets(locksets: Sequence[Optional[LockSet]]) -> DepGraph:
    """
    Build i→j edges for i<j if locksets[i] conflicts with locksets[j].
    Missing (None) entries are treated as fully-conflicting.
    """
    n = len(locksets)
    edges: Dict[int, Set[int]] = {i: set() for i in range(n)}
    # Pre-normalize: replace None with full-write wildcard
    norm: List[LockSet] = [ls if ls is not None else _full_write_lockset() for ls in locksets]

    for i in range(n):
        for j in range(i + 1, n):
            if _conflict_locksets(norm[i], norm[j]):
                edges[i].add(j)
    indeg = _mk_indeg(n, edges)
    return DepGraph(n=n, edges=edges, indeg=indeg)


AccessListType = Sequence  # list[dict|tuple], see lockset_from_access_list docstring


def build_graph_from_access_lists(
    access_lists: Sequence[Optional[AccessListType]],
    *,
    conservative_writes: bool = True,
) -> DepGraph:
    """
    Construct a precedence graph from declared access lists.

    - conservative_writes=True: treat all listed keys as WRITES (max safety).
    - conservative_writes=False: treat all listed keys as READS (more parallelism).

    Missing (None) entries are treated as unknown and fully-conflicting.
    """
    locksets: List[LockSet] = []
    for acc in access_lists:
        if acc is None:
            locksets.append(_full_write_lockset())
        else:
            if conservative_writes:
                ls = lockset_from_access_list(acc)
            else:
                ls = _lockset_from_read_only_access_list(acc)
            locksets.append(ls)
    return build_graph_from_locksets(locksets)


# ------------------------------ Scheduling ----------------------------------


def topo_layers(graph: DepGraph) -> List[List[int]]:
    """
    Kahn topological layering with stable ordering by original index.

    Returns a list of layers; items within a layer are independent by the
    precedence constraints captured in `graph`. This does not guarantee they
    are *truly* non-conflicting if your inputs were too permissive, so prefer
    locksets when possible.
    """
    n = graph.n
    edges = {u: set(vs) for u, vs in graph.edges.items()}
    indeg = list(graph.indeg)

    # Initial frontier (in-degree 0), stable order
    frontier: List[int] = [i for i in range(n) if indeg[i] == 0]
    layers: List[List[int]] = []
    visited = 0

    while frontier:
        frontier.sort()  # stable by idx
        layer = frontier
        layers.append(layer)
        next_frontier: List[int] = []
        # "Remove" this layer
        for u in layer:
            visited += 1
            for v in edges.get(u, ()):
                indeg[v] -= 1
                if indeg[v] == 0:
                    next_frontier.append(v)
            edges[u].clear()
        frontier = next_frontier

    if visited != n:
        # Cycle detected — should not happen if conflicts only point forward (i<j).
        # Fallback: make each remaining node its own layer in index order.
        remaining = [i for i in range(n) if any(edges.get(i, ())) or indeg[i] > 0]
        remaining.sort()
        for i in remaining:
            layers.append([i])

    return layers


# ------------------------------ Diagnostics ---------------------------------


def conflicts_matrix_from_locksets(locksets: Sequence[Optional[LockSet]]) -> Set[Tuple[int, int]]:
    """
    Return the set of conflict pairs (i, j) for i<j using locksets.
    """
    n = len(locksets)
    pairs: Set[Tuple[int, int]] = set()
    norm: List[LockSet] = [ls if ls is not None else _full_write_lockset() for ls in locksets]
    for i in range(n):
        for j in range(i + 1, n):
            if _conflict_locksets(norm[i], norm[j]):
                pairs.add((i, j))
    return pairs


def conflicts_matrix_from_access_lists(
    access_lists: Sequence[Optional[AccessListType]],
    *,
    conservative_writes: bool = True,
) -> Set[Tuple[int, int]]:
    """
    Return the set of conflict pairs (i, j) for i<j using declared access lists.
    """
    if conservative_writes:
        locksets = [
            _full_write_lockset() if acc is None else lockset_from_access_list(acc)
            for acc in access_lists
        ]
    else:
        locksets = [
            _full_write_lockset() if acc is None else _lockset_from_read_only_access_list(acc)
            for acc in access_lists
        ]
    return conflicts_matrix_from_locksets(locksets)


__all__ = [
    "DepGraph",
    "build_graph_from_locksets",
    "build_graph_from_access_lists",
    "topo_layers",
    "conflicts_matrix_from_locksets",
    "conflicts_matrix_from_access_lists",
]
