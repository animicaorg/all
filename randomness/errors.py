"""
Randomness pipeline errors.

This module defines a small, typed hierarchy of exceptions raised by the
beacon pipeline (commit → reveal → VDF → mix). Callers can catch the base
`RandError` to handle all deterministic randomness errors, or catch the
concrete subclasses for more granular control.

The errors here are intentionally lightweight and serialization-friendly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


class RandError(Exception):
    """Base class for all randomness pipeline errors."""
    pass


Number = Union[int, float]


@dataclass(frozen=True)
class CommitTooLate(RandError):
    """
    Raised when a commit for a given round arrives after the allowed commit window.

    Attributes:
        round_id: The beacon round identifier.
        commit_ts: The timestamp (seconds since epoch) when the commit was observed.
        cutoff_ts: The commit cutoff timestamp for the round.
    """
    round_id: int
    commit_ts: Number
    cutoff_ts: Number

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return (
            f"CommitTooLate: round={self.round_id} commit_ts={self.commit_ts} "
            f"> cutoff_ts={self.cutoff_ts}"
        )


@dataclass(frozen=True)
class RevealTooEarly(RandError):
    """
    Raised when a reveal is attempted before the reveal window opens.

    Attributes:
        round_id: The beacon round identifier.
        now_ts: The timestamp when the reveal was attempted.
        reveal_open_ts: The timestamp when reveals become valid for the round.
    """
    round_id: int
    now_ts: Number
    reveal_open_ts: Number

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return (
            f"RevealTooEarly: round={self.round_id} now_ts={self.now_ts} "
            f"< reveal_open_ts={self.reveal_open_ts}"
        )


@dataclass(frozen=True)
class BadReveal(RandError):
    """
    Raised when a reveal value does not match its prior commitment or otherwise fails validation.

    Attributes:
        round_id: The beacon round identifier.
        expected_commitment_hex: Hex string of the expected commitment (domain-separated).
        got_commitment_hex: Hex string of the commitment derived from the provided reveal.
        reason: Optional human-readable explanation (e.g., 'domain-mismatch', 'length', 'hash-mismatch').
    """
    round_id: int
    expected_commitment_hex: str
    got_commitment_hex: str
    reason: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        base = (
            f"BadReveal: round={self.round_id} expected={self.expected_commitment_hex} "
            f"got={self.got_commitment_hex}"
        )
        return f"{base} reason={self.reason}" if self.reason else base


@dataclass(frozen=True)
class VDFInvalid(RandError):
    """
    Raised when a VDF proof/output pair fails verification.

    Attributes:
        round_id: The beacon round identifier.
        reason: Optional explanation (e.g., 'invalid-proof', 'mismatch-y', 'wrong-iterations').
    """
    round_id: int
    reason: Optional[str] = None

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return (
            f"VDFInvalid: round={self.round_id}"
            + (f" reason={self.reason}" if self.reason else "")
        )


__all__ = [
    "RandError",
    "CommitTooLate",
    "RevealTooEarly",
    "BadReveal",
    "VDFInvalid",
]
