# Copyright (c) Animica.
# SPDX-License-Identifier: MIT
"""
randomness.utils.time
=====================

Helpers for computing **round boundaries** in the randomness beacon,
purely from block time (no wall clock). The schedule is defined by:

- an *anchor* timestamp (e.g., genesis time) in epoch seconds,
- a fixed round length (seconds),
- a reveal grace window after each round for reveal submissions, and
- an optional VDF window after reveals close.

Lifecycle per round `r` (block-time aware):
    [commit) ----- round ----- [reveal) ---- grace ---- [VDF) ----

Where:
- Commit window:  [round_start(r), round_end(r))
- Reveal window:  [round_end(r), reveal_end(r))     with reveal_end = round_end + reveal_grace
- VDF window:     [reveal_end(r), vdf_end(r))       with vdf_end = reveal_end + vdf_window

These helpers do **not** read system time; callers must pass the canonical
block timestamp.

The module is dependency-light and safe for deterministic contexts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, Optional

# We only import error types to surface precise reasons when desired.
# Keep this import shallow to avoid cycles elsewhere.
try:  # pragma: no cover - soft dependency for nicer errors
    from randomness.errors import CommitTooLate, RevealTooEarly, BadReveal  # type: ignore
except Exception:  # pragma: no cover
    class CommitTooLate(RuntimeError):  # type: ignore
        pass

    class RevealTooEarly(RuntimeError):  # type: ignore
        pass

    class BadReveal(RuntimeError):  # type: ignore
        pass


__all__ = [
    "RoundSchedule",
    "round_id_for_time",
    "round_start",
    "round_end",
    "reveal_window",
    "vdf_window",
    "commit_window",
    "is_within",
    "assert_commit_time",
    "assert_reveal_time",
    "round_info_for_time",
]


@dataclass(frozen=True)
class RoundSchedule:
    """
    Round schedule parameters.

    Attributes
    ----------
    anchor_time : int
        Epoch seconds for round 0 start (e.g., genesis timestamp).
    round_seconds : int
        Length of one round in seconds (must be > 0).
    reveal_grace_seconds : int
        Extra seconds after a round ends during which reveals are accepted (>= 0).
    vdf_window_seconds : int
        Optional seconds allocated after reveals close for VDF proof window (>= 0).
    """

    anchor_time: int
    round_seconds: int
    reveal_grace_seconds: int = 0
    vdf_window_seconds: int = 0

    @staticmethod
    def _normalize_epoch_seconds(x: int | float) -> int:
        """
        Normalize various timestamp units to **seconds**.
        Accepts seconds, milliseconds, microseconds, or nanoseconds based on magnitude.
        """
        # Accept floats (floor to int seconds)
        if isinstance(x, float):
            return int(x)
        v = int(x)
        # Heuristic unit detection by magnitude
        if v >= 10**18:  # ns
            return v // 10**9
        if v >= 10**15:  # µs
            return v // 10**6
        if v >= 10**12:  # ms
            return v // 10**3
        return v  # seconds

    @classmethod
    def from_config(cls, cfg: object) -> "RoundSchedule":
        """
        Construct a schedule from a config-like object with attributes:
        - anchor_time / genesis_time
        - round_seconds / round_len_seconds
        - reveal_grace_seconds
        - vdf_window_seconds

        Unknown fields are ignored; missing ones must be provided via synonyms.
        """
        # Fetch with common synonyms
        anchor = getattr(cfg, "anchor_time", getattr(cfg, "genesis_time", 0))
        round_s = getattr(cfg, "round_seconds", getattr(cfg, "round_len_seconds", None))
        reveal = getattr(cfg, "reveal_grace_seconds", 0)
        vdfw = getattr(cfg, "vdf_window_seconds", 0)

        if round_s is None or int(round_s) <= 0:
            raise ValueError("round_seconds must be > 0")
        anchor = cls._normalize_epoch_seconds(anchor)
        return cls(
            anchor_time=anchor,
            round_seconds=int(round_s),
            reveal_grace_seconds=int(reveal),
            vdf_window_seconds=int(vdfw),
        )


# -----------------
# Core computations
# -----------------

def round_id_for_time(s: RoundSchedule, block_time: int | float) -> int:
    """
    Compute the round id for a given *block_time* (epoch seconds or ms/µs/ns).

    Rounds are zero-based. Times before the anchor clamp to round 0.
    """
    t = RoundSchedule._normalize_epoch_seconds(block_time)
    if t <= s.anchor_time:
        return 0
    return (t - s.anchor_time) // s.round_seconds


def round_start(s: RoundSchedule, rid: int) -> int:
    """Start timestamp (epoch seconds) of round *rid*."""
    if rid < 0:
        raise ValueError("round id must be non-negative")
    return s.anchor_time + rid * s.round_seconds


def round_end(s: RoundSchedule, rid: int) -> int:
    """End timestamp (epoch seconds, exclusive) of round *rid*."""
    return round_start(s, rid) + s.round_seconds


def commit_window(s: RoundSchedule, rid: int) -> Tuple[int, int]:
    """
    Commit window [start, end) for round *rid*.
    Commits are accepted during the round proper.
    """
    return round_start(s, rid), round_end(s, rid)


def reveal_window(s: RoundSchedule, rid: int) -> Tuple[int, int]:
    """
    Reveal window [start, end) for round *rid*.
    Begins when the round ends and lasts for the configured grace period.
    """
    start = round_end(s, rid)
    end = start + max(0, s.reveal_grace_seconds)
    return start, end


def vdf_window(s: RoundSchedule, rid: int) -> Tuple[int, int]:
    """
    VDF window [start, end) for round *rid* (optional).
    Begins after reveals close and lasts for the configured VDF window seconds.
    """
    r_start, r_end = reveal_window(s, rid)
    return r_end, r_end + max(0, s.vdf_window_seconds)


# -------------
# Validations
# -------------

def is_within(ts: int | float, window: Tuple[int, int]) -> bool:
    """Return True if *ts* falls within [start, end) of *window* (unit-agnostic)."""
    t = RoundSchedule._normalize_epoch_seconds(ts)
    start, end = window
    return start <= t < end


def assert_commit_time(s: RoundSchedule, rid: int, ts: int | float) -> None:
    """
    Validate *ts* is within the commit window for round *rid*.

    Raises:
        CommitTooLate: if the commit arrives after the commit window closed.
        ValueError: if ts is before round start (too early).
    """
    t = RoundSchedule._normalize_epoch_seconds(ts)
    start, end = commit_window(s, rid)
    if t < start:
        raise ValueError("commit too early for this round")
    if t >= end:
        # Use specific error to aid callers that map to rejection codes/metrics.
        raise CommitTooLate("commit window closed for this round")


def assert_reveal_time(s: RoundSchedule, rid: int, ts: int | float) -> None:
    """
    Validate *ts* is within the reveal window for round *rid*.

    Raises:
        RevealTooEarly: if before reveal window opens.
        BadReveal: if after reveal window closes.
    """
    t = RoundSchedule._normalize_epoch_seconds(ts)
    start, end = reveal_window(s, rid)
    if t < start:
        raise RevealTooEarly("reveal before window opened")
    if t >= end:
        raise BadReveal("reveal window closed")


# ----------------
# Convenience info
# ----------------

def round_info_for_time(
    s: RoundSchedule, block_time: int | float
) -> dict[str, int]:
    """
    Produce a small dictionary with boundary timestamps for the
    round that contains *block_time*.

    Keys: rid, start, end, reveal_start, reveal_end, vdf_start, vdf_end
    """
    rid = round_id_for_time(s, block_time)
    rs = round_start(s, rid)
    re = round_end(s, rid)
    rev_s, rev_e = reveal_window(s, rid)
    vdf_s, vdf_e = vdf_window(s, rid)
    return {
        "rid": int(rid),
        "start": rs,
        "end": re,
        "reveal_start": rev_s,
        "reveal_end": rev_e,
        "vdf_start": vdf_s,
        "vdf_end": vdf_e,
    }
