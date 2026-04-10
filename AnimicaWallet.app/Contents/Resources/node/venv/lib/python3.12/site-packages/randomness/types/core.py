from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, Optional

"""
Core typed primitives for the randomness beacon.

These are intentionally minimal and free of heavy dependencies so they can be
shared across submodules (commit/reveal collection, VDF verification, mixing,
RPC surface, and tests).

Types provided:
  • RoundId       — integer-typed identifier for a beacon round
  • CommitRecord  — participant's commit hash bound to a specific round
  • RevealRecord  — participant's reveal value bound to a specific round
  • VDFInput      — seed and work factor provided to the VDF
  • VDFProof      — VDF output and proof blob
  • BeaconOut     — finalized mixed randomness for a round
"""

# ---- Simple newtypes ---------------------------------------------------------

RoundId = NewType("RoundId", int)

# Internal constants (kept local to avoid import cycles)
_HASH32 = 32


def _require_len(name: str, b: bytes, n: int) -> None:
    if len(b) != n:
        raise ValueError(f"{name} must be exactly {n} bytes (got {len(b)})")


def _require_nonneg(name: str, v: int) -> None:
    if v < 0:
        raise ValueError(f"{name} must be non-negative (got {v})")


# ---- Records -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """
    A participant's commitment for a given round.

    Fields:
      round    — target beacon round
      participant — opaque participant identifier (e.g., 32-byte account id)
      commit   — commitment hash (domain-separated, 32 bytes)
    """

    round: RoundId
    participant: bytes
    commit: bytes

    def __post_init__(self) -> None:  # type: ignore[override]
        if not isinstance(self.round, int):
            raise TypeError("round must be an int (RoundId)")
        _require_nonneg("round", int(self.round))
        if not isinstance(self.participant, (bytes, bytearray)):
            raise TypeError("participant must be bytes")
        if not isinstance(self.commit, (bytes, bytearray)):
            raise TypeError("commit must be bytes")
        _require_len("commit", self.commit, _HASH32)


@dataclass(frozen=True, slots=True)
class RevealRecord:
    """
    A participant's reveal for a given round.

    Fields:
      round    — target beacon round
      participant — opaque participant identifier (e.g., 32-byte account id)
      reveal   — preimage value (32 bytes) that should match the commitment
    """

    round: RoundId
    participant: bytes
    reveal: bytes

    def __post_init__(self) -> None:  # type: ignore[override]
        if not isinstance(self.round, int):
            raise TypeError("round must be an int (RoundId)")
        _require_nonneg("round", int(self.round))
        if not isinstance(self.participant, (bytes, bytearray)):
            raise TypeError("participant must be bytes")
        if not isinstance(self.reveal, (bytes, bytearray)):
            raise TypeError("reveal must be bytes")
        _require_len("reveal", self.reveal, _HASH32)


# ---- VDF inputs & proofs -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class VDFInput:
    """
    Input to the VDF stage for a round.

    Fields:
      round       — beacon round
      seed        — seed value to feed into the VDF (32 bytes), typically
                    derived from the commit/reveal aggregate/mix
      iterations  — VDF work factor / time parameter (non-negative)
    """

    round: RoundId
    seed: bytes
    iterations: int

    def __post_init__(self) -> None:  # type: ignore[override]
        if not isinstance(self.round, int):
            raise TypeError("round must be an int (RoundId)")
        _require_nonneg("round", int(self.round))
        if not isinstance(self.seed, (bytes, bytearray)):
            raise TypeError("seed must be bytes")
        _require_len("seed", self.seed, _HASH32)
        if not isinstance(self.iterations, int):
            raise TypeError("iterations must be int")
        _require_nonneg("iterations", self.iterations)


@dataclass(frozen=True, slots=True)
class VDFProof:
    """
    A VDF evaluation result.

    Fields:
      round       — beacon round
      y           — VDF output element (byte-form; length depends on construction)
      pi          — proof blob (scheme-specific)
      iterations  — work factor used (must match VDFInput.iterations)
      verified    — optional cached verification result (None if unknown)
    """

    round: RoundId
    y: bytes
    pi: bytes
    iterations: int
    verified: Optional[bool] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if not isinstance(self.round, int):
            raise TypeError("round must be an int (RoundId)")
        _require_nonneg("round", int(self.round))
        if not isinstance(self.y, (bytes, bytearray)):
            raise TypeError("y must be bytes")
        if not isinstance(self.pi, (bytes, bytearray)):
            raise TypeError("pi must be bytes")
        if not isinstance(self.iterations, int):
            raise TypeError("iterations must be int")
        _require_nonneg("iterations", self.iterations)


# ---- Final beacon output -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class BeaconOut:
    """
    Final mixed randomness for a beacon round.

    Fields:
      round       — beacon round
      value       — final randomness output (32 bytes)
      n_commits   — number of commit records included
      n_reveals   — number of reveal records included
      vdf_y       — VDF output included in the mix (optional if VDF disabled)
    """

    round: RoundId
    value: bytes
    n_commits: int
    n_reveals: int
    vdf_y: Optional[bytes] = None

    def __post_init__(self) -> None:  # type: ignore[override]
        if not isinstance(self.round, int):
            raise TypeError("round must be an int (RoundId)")
        _require_nonneg("round", int(self.round))
        if not isinstance(self.value, (bytes, bytearray)):
            raise TypeError("value must be bytes")
        _require_len("value", self.value, _HASH32)
        if not isinstance(self.n_commits, int) or self.n_commits < 0:
            raise ValueError("n_commits must be a non-negative int")
        if not isinstance(self.n_reveals, int) or self.n_reveals < 0:
            raise ValueError("n_reveals must be a non-negative int")
        if self.vdf_y is not None and not isinstance(self.vdf_y, (bytes, bytearray)):
            raise TypeError("vdf_y must be bytes when provided")


__all__ = [
    "RoundId",
    "CommitRecord",
    "RevealRecord",
    "VDFInput",
    "VDFProof",
    "BeaconOut",
]
