"""
Fork Choice (PoIES)
===================

This module implements a *weight-aware* and *deterministic* fork-choice rule:

1) Prefer the tip with *highest cumulative weight* (Σ weightMicro along the chain).
   - In PoIES, a natural per-block weight is Θ (acceptance threshold, µ-nats) or
     any monotone function of validated difficulty/score. Callers supply the
     `weight_micro` for each header inserted (usually the block's Θ at seal time).

2) On equal cumulative weight, prefer *greater height*.

3) If still tied, choose the *lexicographically smallest block hash* (bytes-wise).
   This is deterministic and avoids oscillations.

The structure is self-contained, has no DB dependency, and exposes:
- `ForkChoice.add_block(...)` to insert a header (parent-first or orphan-ok).
- `ForkChoice.best_tip` to read the selected canonical head.
- `ForkChoice.reorg_path(old_tip, new_tip)` to compute detach/attach sets.
- Optional `max_reorg_depth` guard to avoid deep reorgs in live serving mode.

Types
-----
- `weight_micro` is an `int` (µ-nats). It should be *non-negative*.
- Hashes are hex-strings `"0x..."` or raw `bytes`. The module normalizes to `bytes`.

Integration
-----------
- Consensus validators should call `add_block` *after* header validation
  (policy roots, S recompute, Θ schedule, nullifiers, etc.).
- Core persistence can mirror the chosen best tip and commit detach/attach.

The code is pure Python and deterministic. No clocks or randomness used.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass, field
from typing import (Dict, Iterable, Iterator, List, Optional, Sequence, Set,
                    Tuple)

# Optional alias import (keeps decoupled from consensus.types)
try:
    from .types import MicroNat as WeightMicro
except Exception:  # pragma: no cover - when running standalone
    WeightMicro = int  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _hex_to_bytes(h: str | bytes) -> bytes:
    if isinstance(h, bytes):
        return h
    s = h.lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2:
        s = "0" + s
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"invalid hex: {h!r}") from e


def _bytes_to_hex(b: bytes) -> str:
    return "0x" + binascii.hexlify(b).decode("ascii")


# Deterministic hash comparator (lexicographic)
def _hash_lt(a: bytes, b: bytes) -> bool:
    return a < b


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Node:
    h: bytes  # block hash (bytes)
    parent: Optional[bytes]  # parent hash or None (genesis)
    height: int  # genesis=0 or 1, depending on caller convention
    weight_micro: WeightMicro  # per-block weight (non-negative)
    cum_weight_micro: WeightMicro  # cumulative weight up to this node (inclusive)
    # metadata
    children: Set[bytes] = field(default_factory=set)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"Node(h={_bytes_to_hex(self.h)[:10]}…, "
            f"height={self.height}, weight={self.weight_micro}, "
            f"cum={self.cum_weight_micro})"
        )


@dataclass(frozen=True)
class BestTip:
    h: bytes
    height: int
    cum_weight_micro: WeightMicro

    @property
    def hex(self) -> str:
        return _bytes_to_hex(self.h)


@dataclass(frozen=True)
class AddResult:
    accepted: bool  # inserted into the tree (or updated from orphan)
    became_best: bool  # True if the best tip changed to this node
    best: BestTip  # current best tip after the insert
    reorg_depth: int  # depth of reorg (0 if none)
    detached: Tuple[bytes, ...]  # old tip → LCA exclusive
    attached: Tuple[bytes, ...]  # LCA → new tip inclusive


# ---------------------------------------------------------------------------
# Fork choice engine
# ---------------------------------------------------------------------------


class ForkChoice:
    """
    Weight-aware fork choice with deterministic tie-breakers.

    Args
    ----
    genesis_hash : str|bytes
        Canonical genesis block hash. Must be unique.
    genesis_weight_micro : int
        The weight to attribute to genesis (often 0). Must be ≥ 0.
    genesis_height : int
        Height for genesis (0 or 1). Defaults to 0.
    max_reorg_depth : Optional[int]
        If set, adding a better tip that would cause a reorg deeper than this
        threshold is *ignored* (remains non-canonical) and `became_best=False`.
        Set to None to allow arbitrary reorg (default).
    """

    def __init__(
        self,
        *,
        genesis_hash: str | bytes,
        genesis_weight_micro: WeightMicro = 0,
        genesis_height: int = 0,
        max_reorg_depth: Optional[int] = None,
    ) -> None:
        g = _hex_to_bytes(genesis_hash)
        if genesis_weight_micro < 0:
            raise ValueError("genesis_weight_micro must be non-negative")
        self.nodes: Dict[bytes, Node] = {}
        self.orphans: Dict[bytes, List[Tuple[bytes, int, WeightMicro]]] = (
            {}
        )  # parent -> [(h, height, weight), ...]
        self._best: BestTip = BestTip(
            h=g, height=int(genesis_height), cum_weight_micro=int(genesis_weight_micro)
        )
        self.max_reorg_depth = max_reorg_depth

        self.nodes[g] = Node(
            h=g,
            parent=None,
            height=int(genesis_height),
            weight_micro=int(genesis_weight_micro),
            cum_weight_micro=int(genesis_weight_micro),
        )

    # ----------------------- public API -----------------------

    @property
    def best_tip(self) -> BestTip:
        return self._best

    def has(self, h: str | bytes) -> bool:
        return _hex_to_bytes(h) in self.nodes

    def add_block(
        self,
        *,
        h: str | bytes,
        parent: str | bytes,
        height: int,
        weight_micro: WeightMicro,
    ) -> AddResult:
        """
        Insert a header (already validated) and update best tip if it wins.

        If `parent` is unknown, the block is buffered as an orphan. When the
        parent arrives, the child is connected and evaluated then.

        Returns an `AddResult` describing whether the best tip changed and the
        reorg path (detach/attach) if any.
        """
        if weight_micro < 0:
            raise ValueError("weight_micro must be non-negative")

        hh = _hex_to_bytes(h)
        ph = _hex_to_bytes(parent)

        if hh in self.nodes:
            # Duplicate insert; compute current best vs old best (no change).
            return self._result(accepted=False, new_tip=self._best.h)

        if ph not in self.nodes:
            # Buffer as orphan
            self.orphans.setdefault(ph, []).append((hh, int(height), int(weight_micro)))
            return self._result(accepted=False, new_tip=self._best.h)

        # Attach to known parent
        node = self._attach_known_parent(hh, ph, height, weight_micro)
        became_best, reorg_depth, detached, attached = self._maybe_update_best(node)

        # Resolve descendants waiting on this node
        self._connect_orphans(hh)

        return AddResult(
            accepted=True,
            became_best=became_best,
            best=self._best,
            reorg_depth=reorg_depth,
            detached=tuple(detached),
            attached=tuple(attached),
        )

    # ----------------------- internal helpers -----------------------

    def _attach_known_parent(
        self, hh: bytes, ph: bytes, height: int, weight_micro: WeightMicro
    ) -> Node:
        parent_node = self.nodes[ph]
        if height <= parent_node.height:
            # Non-monotone height; allow but correct it (defensive) to parent+1
            height = parent_node.height + 1

        cum = parent_node.cum_weight_micro + int(weight_micro)
        node = Node(
            h=hh,
            parent=ph,
            height=int(height),
            weight_micro=int(weight_micro),
            cum_weight_micro=cum,
        )
        self.nodes[hh] = node
        parent_node.children.add(hh)
        return node

    def _connect_orphans(self, parent: bytes) -> None:
        """Attach all immediate children waiting on `parent` (BFS)."""
        queue: List[bytes] = [parent]
        while queue:
            p = queue.pop(0)
            waiting = self.orphans.pop(p, [])
            for hh, height, weight in waiting:
                if hh in self.nodes:  # was possibly attached by earlier pass
                    continue
                child = self._attach_known_parent(hh, p, height, weight)
                self._maybe_update_best(child)
                queue.append(hh)

    # Deterministic comparison of tips
    @staticmethod
    def _better(a: Node, b: Node) -> bool:
        """
        True if `a` is strictly better than `b`:
          1) higher cumulative weight
          2) higher height
          3) lexicographically smaller hash
        """
        if a.cum_weight_micro != b.cum_weight_micro:
            return a.cum_weight_micro > b.cum_weight_micro
        if a.height != b.height:
            return a.height > b.height
        return _hash_lt(a.h, b.h)

    def _collect_tip(self, h: bytes) -> Node:
        return self.nodes[h]

    def _maybe_update_best(
        self, candidate: Node
    ) -> Tuple[bool, int, List[bytes], List[bytes]]:
        old_best = self._collect_tip(self._best.h)
        if not self._better(candidate, old_best):
            return False, 0, [], []

        # Reorg guard?
        detached, attached = self.reorg_path(old_best.h, candidate.h)
        depth = len(detached)
        if self.max_reorg_depth is not None and depth > self.max_reorg_depth:
            # Ignore excessive reorgs; keep best unchanged.
            return False, depth, [], []

        self._best = BestTip(
            h=candidate.h,
            height=candidate.height,
            cum_weight_micro=candidate.cum_weight_micro,
        )
        return True, depth, detached, attached

    # LCA & reorg path
    def reorg_path(
        self, from_h: bytes | str, to_h: bytes | str
    ) -> Tuple[List[bytes], List[bytes]]:
        """
        Compute the detach/attach path to move the canonical head from `from_h` to `to_h`.

        Returns (detached, attached) where:
          - `detached`: list of hashes from `from_h` descending to (but NOT including) LCA
          - `attached`: list of hashes from LCA's *child towards* `to_h` (inclusive of `to_h`)
        """
        a = _hex_to_bytes(from_h)
        b = _hex_to_bytes(to_h)
        if a == b:
            return [], []

        path_a = []
        path_b = []

        va = self.nodes[a]
        vb = self.nodes[b]

        # Ascend taller side until same height
        while va.height > vb.height:
            path_a.append(va.h)
            va = self.nodes[va.parent] if va.parent else va
        while vb.height > va.height:
            path_b.append(vb.h)
            vb = self.nodes[vb.parent] if vb.parent else vb

        # Walk up together until LCA found
        while va.h != vb.h:
            path_a.append(va.h)
            path_b.append(vb.h)
            va = self.nodes[va.parent] if va.parent else va
            vb = self.nodes[vb.parent] if vb.parent else vb

        # Now va == vb == LCA
        path_a_detach = path_a  # from old tip down to (excl) LCA
        path_b_attach = list(reversed(path_b))  # from LCA child up to new tip (incl)
        return path_a_detach, path_b_attach

    # ----------------------- debug & iteration -----------------------

    def iter_chain_back(self, tip: bytes | str) -> Iterator[bytes]:
        """Iterate hashes from `tip` back to genesis (inclusive)."""
        h = _hex_to_bytes(tip)
        n = self.nodes[h]
        while True:
            yield n.h
            if n.parent is None:
                break
            n = self.nodes[n.parent]

    def tip_set(self) -> List[bytes]:
        """Return hashes with no children (current tips of all branches)."""
        # Efficient enough for modest graphs; can be cached if needed.
        has_child: Set[bytes] = set()
        for n in self.nodes.values():
            has_child |= n.children
        return [h for h, n in self.nodes.items() if h not in has_child]


# ---------------------------------------------------------------------------
# Example usage (manual test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    fc = ForkChoice(genesis_hash="0x00", genesis_weight_micro=0, genesis_height=0)

    def add(h, p, ht, w):
        r = fc.add_block(h=h, parent=p, height=ht, weight_micro=w)
        print(
            f"add {h} parent={p} h={ht} w={w} → best={r.best.hex} "
            f"(cum={r.best.cum_weight_micro}, became_best={r.became_best}, reorg={r.reorg_depth})"
        )
        if r.detached or r.attached:
            print("  detach:", [_bytes_to_hex(x) for x in r.detached])
            print("  attach:", [_bytes_to_hex(x) for x in r.attached])

    # Build two branches with different weights
    add("0x01", "0x00", 1, 1_500_000)  # cum 1.5
    add("0x02", "0x01", 2, 1_500_000)  # cum 3.0  -> best=0x02
    add(
        "0x0a", "0x00", 1, 2_800_000
    )  # heavier sibling of 0x01 (cum 2.8) -> reorg to branch 0x0a
    add("0x0b", "0x0a", 2, 2_000_000)  # cum 4.8 -> remains best

    # Equal weight + height tie resolved by lexicographic hash
    add("0x10", "0x0b", 3, 100_000)  # cum 4.9
    add("0x11", "0x0b", 3, 100_000)  # cum 4.9   → best becomes 0x10 (smaller hash)
    print("tips:", [_bytes_to_hex(h) for h in fc.tip_set()])
