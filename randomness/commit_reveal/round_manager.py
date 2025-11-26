# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
Round manager for commit–reveal randomness.

This module defines window/phase calculations and timing guards for:
- Commit phase: accepts commitments for a round.
- Reveal phase: accepts reveals for a round (optionally with a grace tail).
- VDF phase: optional delay/proof period after reveals close.

Windows are derived from a single *anchor timestamp* (round 0 start) and the
configured durations for each phase. All calculations use integer seconds.

Typical usage
-------------
    cfg = RandConfig(...)  # see randomness.config
    rm  = RoundManager(cfg)

    now = int(time.time())
    rid = rm.round_id_for_time(now)

    # Enforce timing for a commit targeting `rid`
    rm.enforce_commit_timing(now, rid)  # raises CommitTooLate if closed

    # Enforce timing for a reveal
    rm.enforce_reveal_timing(now, rid)  # raises RevealTooEarly / BadReveal

Notes
-----
- All helpers are pure/time-passive: the caller provides `now` (block-time or
  wall-clock) to make unit tests deterministic.
- We raise project-wide errors from `randomness.errors`.

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple

# ---- Imports with safe fallbacks (to keep early bootstraps flexible) ----

try:
    # Recommended config shape (documented in randomness/config.py):
    #   - round_anchor_s: int (UNIX seconds for round 0 start)
    #   - commit_phase_s: int
    #   - reveal_phase_s: int
    #   - vdf_phase_s: int
    #   - reveal_grace_s: int
    from randomness.config import RandConfig  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - fallback typing shim
    from dataclasses import dataclass as _dc

    @_dc
    class RandConfig:  # type: ignore
        round_anchor_s: int = 0
        commit_phase_s: int = 10
        reveal_phase_s: int = 10
        vdf_phase_s: int = 10
        reveal_grace_s: int = 0

try:
    from randomness.errors import CommitTooLate, RevealTooEarly, BadReveal
except Exception:  # pragma: no cover - define minimal shims for local tests
    class CommitTooLate(RuntimeError): ...
    class RevealTooEarly(RuntimeError): ...
    class BadReveal(RuntimeError): ...


RoundId = int


class Phase(Enum):
    """High-level phase within a round."""
    PRE = auto()      # before round start (for the given round-id)
    COMMIT = auto()
    REVEAL = auto()
    VDF = auto()
    CLOSED = auto()


@dataclass(frozen=True, slots=True)
class RoundBoundaries:
    """Absolute boundaries (UNIX seconds) for a round."""
    start_s: int
    commit_end_s: int
    reveal_end_s: int
    vdf_end_s: int

    @property
    def durations(self) -> Tuple[int, int, int]:
        return (
            self.commit_end_s - self.start_s,
            self.reveal_end_s - self.commit_end_s,
            self.vdf_end_s - self.reveal_end_s,
        )

    @property
    def round_end_s(self) -> int:
        return self.vdf_end_s


class RoundManager:
    """
    Computes round boundaries and enforces timing rules for commit/reveal/VDF.

    Phase layout for a given round r (all times in seconds since UNIX epoch):

        [start, commit_end) → COMMIT window (inclusive start, exclusive end)
        [commit_end, reveal_end) → REVEAL window
        [reveal_end, vdf_end) → VDF window
        [vdf_end, next_start) → CLOSED (no accepts)

    A separate `reveal_grace_s` extends acceptance for *reveals* beyond
    `reveal_end` up to `reveal_end + reveal_grace_s`, but does not change the
    nominal phase classification (i.e., we can be in VDF while still allowing
    late reveals if grace > 0).
    """

    __slots__ = ("cfg", "_round_len")

    def __init__(self, cfg: RandConfig):
        self.cfg = cfg
        self._round_len = (
            int(cfg.commit_phase_s)
            + int(cfg.reveal_phase_s)
            + int(cfg.vdf_phase_s)
        )
        if self._round_len <= 0:
            raise ValueError("round length must be positive")

    # ---- Core time ↔ round mapping ----

    def boundaries(self, round_id: RoundId) -> RoundBoundaries:
        if round_id < 0:
            raise ValueError("round_id must be non-negative")
        start = int(self.cfg.round_anchor_s) + round_id * self._round_len
        commit_end = start + int(self.cfg.commit_phase_s)
        reveal_end = commit_end + int(self.cfg.reveal_phase_s)
        vdf_end = reveal_end + int(self.cfg.vdf_phase_s)
        return RoundBoundaries(start, commit_end, reveal_end, vdf_end)

    def round_id_for_time(self, now_s: int) -> RoundId:
        """Compute the round-id that contains `now_s`."""
        delta = now_s - int(self.cfg.round_anchor_s)
        if delta < 0:
            # Before anchor: clamp to round 0
            return 0
        return delta // self._round_len

    # ---- Phase calculations ----

    def phase_at(self, now_s: int, round_id: RoundId) -> Phase:
        b = self.boundaries(round_id)
        if now_s < b.start_s:
            return Phase.PRE
        if now_s < b.commit_end_s:
            return Phase.COMMIT
        if now_s < b.reveal_end_s:
            return Phase.REVEAL
        if now_s < b.vdf_end_s:
            return Phase.VDF
        return Phase.CLOSED

    def time_to_next_phase(self, now_s: int, round_id: RoundId) -> int:
        """Seconds until the next phase boundary (0 if boundary or past end)."""
        b = self.boundaries(round_id)
        if now_s < b.start_s:
            return b.start_s - now_s
        if now_s < b.commit_end_s:
            return b.commit_end_s - now_s
        if now_s < b.reveal_end_s:
            return b.reveal_end_s - now_s
        if now_s < b.vdf_end_s:
            return b.vdf_end_s - now_s
        return 0

    # ---- Acceptance rules ----

    def can_accept_commit(self, now_s: int, round_id: RoundId) -> bool:
        """True iff `now_s` lies in the commit window for `round_id`."""
        b = self.boundaries(round_id)
        return b.start_s <= now_s < b.commit_end_s

    def can_accept_reveal(self, now_s: int, round_id: RoundId) -> bool:
        """
        True iff `now_s` lies in the reveal window or its grace tail.
        """
        b = self.boundaries(round_id)
        grace_end = b.reveal_end_s + int(getattr(self.cfg, "reveal_grace_s", 0))
        return b.commit_end_s <= now_s < max(grace_end, b.commit_end_s)

    # Backwards-compatible aliases used in unit tests.
    def can_commit(self, round_id: RoundId, now_s: int) -> bool:  # pragma: no cover - thin wrapper
        return self.can_accept_commit(now_s, round_id)

    def can_reveal(self, round_id: RoundId, now_s: int, *, include_grace: bool = True) -> bool:  # pragma: no cover - thin wrapper
        if include_grace:
            return self.can_accept_reveal(now_s, round_id)
        b = self.boundaries(round_id)
        return b.commit_end_s <= now_s < b.reveal_end_s

    # ---- Enforcement helpers (raise project errors on violation) ----

    def enforce_commit_timing(self, now_s: int, round_id: RoundId) -> None:
        """
        Enforce commit acceptance timing. Raises CommitTooLate if outside window.

        We intentionally do not throw a "too early" error for commits: callers
        should not attempt to pre-commit for a future round; if they do, treat
        it as "late" for the intended round to avoid leaking timing channels.
        """
        if not self.can_accept_commit(now_s, round_id):
            b = self.boundaries(round_id)
            raise CommitTooLate(
                f"commit not accepted at t={now_s} for round={round_id} "
                f"(commit window [{b.start_s},{b.commit_end_s}))"
            )

    def enforce_reveal_timing(self, now_s: int, round_id: RoundId) -> None:
        """
        Enforce reveal acceptance timing.

        Raises:
            RevealTooEarly: if reveal arrives before commit window closes.
            BadReveal: if reveal arrives after reveal window + grace.
        """
        b = self.boundaries(round_id)
        if now_s < b.commit_end_s:
            raise RevealTooEarly(
                f"reveal too early at t={now_s} for round={round_id} "
                f"(must be >= {b.commit_end_s})"
            )
        grace_end = b.reveal_end_s + int(getattr(self.cfg, "reveal_grace_s", 0))
        if now_s >= max(grace_end, b.commit_end_s):
            raise BadReveal(
                f"reveal too late at t={now_s} for round={round_id} "
                f"(window [{b.commit_end_s},{b.reveal_end_s}), grace until {grace_end})"
            )

    # Optional strict validators for compatibility with external callers/tests.
    def ensure_can_commit(self, round_id: RoundId, now_s: int) -> None:  # pragma: no cover - delegates
        self.enforce_commit_timing(now_s, round_id)

    def ensure_can_reveal(self, round_id: RoundId, now_s: int, *, include_grace: bool = True) -> None:  # pragma: no cover - delegates
        if include_grace:
            self.enforce_reveal_timing(now_s, round_id)
        else:
            b = self.boundaries(round_id)
            if not (b.commit_end_s <= now_s < b.reveal_end_s):
                raise BadReveal("reveal outside nominal window")

    # ---- Convenience ----

    def window_remaining_commit(self, now_s: int, round_id: RoundId) -> int:
        """Seconds left in commit window (0 if closed or not yet open)."""
        b = self.boundaries(round_id)
        if not (b.start_s <= now_s < b.commit_end_s):
            return 0
        return b.commit_end_s - now_s

    def window_remaining_reveal(self, now_s: int, round_id: RoundId) -> int:
        """Seconds left for reveals including grace (0 if closed or too early)."""
        b = self.boundaries(round_id)
        if now_s < b.commit_end_s:
            return 0
        grace_end = b.reveal_end_s + int(getattr(self.cfg, "reveal_grace_s", 0))
        if now_s >= grace_end:
            return 0
        return grace_end - now_s

    def describe(self, round_id: RoundId) -> str:
        """Human-readable layout for diagnostics and logs."""
        b = self.boundaries(round_id)
        grace = int(getattr(self.cfg, "reveal_grace_s", 0))
        return (
            f"round {round_id}: start={b.start_s}, "
            f"commit_end={b.commit_end_s}, reveal_end={b.reveal_end_s} "
            f"(+grace {grace}→{b.reveal_end_s + grace}), vdf_end={b.vdf_end_s}"
        )


__all__ = [
    "Phase",
    "RoundBoundaries",
    "RoundManager",
    "RoundId",
]
