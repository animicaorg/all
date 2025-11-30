from __future__ import annotations

"""
Minimal fork choice
===================

Rule:
  1) Prefer the candidate with the higher `height`.
  2) If heights are equal, prefer the candidate with the larger block hash when
     interpreted as a big-endian integer (equivalently: lexicographically larger
     bytes for equal-length digests).

Notes
-----
- This module is intentionally small and dependency-free so it can be used by
  block import, P2P header sync, and tests without pulling the whole stack.
- Hashes are treated as opaque bytes; callers should provide canonical header
  digests (e.g., sha3_256 over the canonical header encoding).
- If hash lengths differ, the longer length wins (to avoid accidental prefix
  ordering), then bytes lexicographically.

The policy is deterministic and stable across processes and platforms.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class HeadCandidate:
    """A minimal head descriptor used for fork choice."""

    height: int
    block_hash: bytes  # canonical digest (e.g., 32B sha3_256)

    def __post_init__(self) -> None:
        if self.height < 0:
            raise ValueError("height must be non-negative")
        if not isinstance(self.block_hash, (bytes, bytearray)):
            raise TypeError("block_hash must be bytes-like")
        object.__setattr__(self, "block_hash", bytes(self.block_hash))


def _hash_better(a: bytes, b: bytes) -> bool:
    """
    Return True iff `a` is strictly preferred to `b` under the tie-break policy.

    Policy:
      - Prefer longer length.
      - If equal length, prefer lexicographically larger bytes (which equals
        larger big-endian integer).
    """
    if len(a) != len(b):
        return len(a) > len(b)
    # same length -> lexicographic (big-endian numeric) compare
    return a > b


def better(a: HeadCandidate, b: HeadCandidate) -> bool:
    """
    Return True iff candidate `a` is strictly preferred to `b`.
    """
    if a.height != b.height:
        return a.height > b.height
    return _hash_better(a.block_hash, b.block_hash)


class ForkChoice:
    """
    Minimal fork-choice tracker. Holds only the current best head.

    Usage:
        fc = ForkChoice()
        changed = fc.consider(height=h, block_hash=H)
        best = fc.best()  # -> (height, hash) or None
    """

    __slots__ = ("_best",)

    def __init__(self) -> None:
        self._best: Optional[HeadCandidate] = None

    # --- Public API ---------------------------------------------------------

    def consider(self, *, height: int, block_hash: bytes) -> bool:
        """
        Consider a new candidate head. Returns True if it replaces the current best.
        """
        cand = HeadCandidate(height=height, block_hash=block_hash)
        if self._best is None or better(cand, self._best):
            self._best = cand
            return True
        return False

    def best(self) -> Optional[Tuple[int, bytes]]:
        """
        Get the current best (height, hash) or None if unset.
        """
        if self._best is None:
            return None
        return (self._best.height, self._best.block_hash)

    def best_candidate(self) -> Optional[HeadCandidate]:
        """
        Get the current best candidate object (or None).
        """
        return self._best

    # --- Utilities ----------------------------------------------------------

    def reset(self) -> None:
        """Forget the current best."""
        self._best = None

    def __repr__(self) -> str:  # pragma: no cover - trivial
        if self._best is None:
            return "ForkChoice(best=None)"
        h = self._best
        return f"ForkChoice(best=height:{h.height} hash:{h.block_hash.hex()[:16]}…)"
