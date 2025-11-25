"""
randomness.beacon.schedule
==========================

Helpers to compute the current beacon round, per-phase deadlines, and
ETAs to the next event in the commit→reveal→(grace)→VDF→mix lifecycle.

The math is purely arithmetic on epoch seconds; callers should decide
what "now" means (wall clock vs. latest block timestamp).

Typical usage
-------------
    from randomness.beacon.schedule import (
        RoundSchedule, schedule_for_time, current_round_id,
        next_event_eta,
    )

    # Network parameters (usually come from randomness.config or state):
    GENESIS_T0 = 1_725_000_000  # epoch seconds the beacon started
    COMMIT_SEC = 12
    REVEAL_SEC = 12
    REVEAL_GRACE_SEC = 6
    VDF_SEC = 24

    rid = current_round_id(now_ts=time.time(), genesis_t0=GENESIS_T0,
                           commit_sec=COMMIT_SEC, reveal_sec=REVEAL_SEC,
                           vdf_sec=VDF_SEC, reveal_grace_sec=REVEAL_GRACE_SEC)
    sched = schedule_for_time(now_ts=time.time(), genesis_t0=GENESIS_T0,
                              commit_sec=COMMIT_SEC, reveal_sec=REVEAL_SEC,
                              vdf_sec=VDF_SEC, reveal_grace_sec=REVEAL_GRACE_SEC)
    event, eta_s = next_event_eta(time.time(), sched)

Integration notes
-----------------
- To avoid a hard dependency, we do not import randomness.config or
  randomness.types.* directly. Instead, we expose convenience helpers that take
  primitive parameters. Adapters can wrap these using their own config/state types.
- Timestamps are in UNIX epoch seconds (float or int); results are returned
  as ints (floor) for stable boundaries.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

Phase = Literal["commit", "reveal", "reveal_grace", "vdf", "mix_ready"]


@dataclass(frozen=True)
class RoundSchedule:
    """Computed boundaries for a beacon round.

    All times are UNIX epoch seconds (ints; closed-open intervals).
    Layout (C = commit, R = reveal, G = reveal grace, V = VDF):

        [C open .................. C close) =
        [t_commit_open ........... t_commit_close)

        [R open .................. R close) =
        [t_reveal_open ........... t_reveal_close)

        [G open .................. G close) =
        [t_reveal_grace_open ..... t_reveal_grace_close)

        [V start ................. V deadline) =
        [t_vdf_start ............. t_vdf_deadline)

        Mix output is ready at t_mix_ready == t_vdf_deadline.
    """

    round_id: int

    t_commit_open: int
    t_commit_close: int

    t_reveal_open: int
    t_reveal_close: int

    t_reveal_grace_open: int
    t_reveal_grace_close: int

    t_vdf_start: int
    t_vdf_deadline: int

    t_mix_ready: int

    # Durations (seconds)
    commit_sec: int
    reveal_sec: int
    reveal_grace_sec: int
    vdf_sec: int

    @property
    def total_sec(self) -> int:
        return self.commit_sec + self.reveal_sec + self.reveal_grace_sec + self.vdf_sec

    def phase_at(self, ts: float | int) -> Phase:
        t = int(ts)
        if t < self.t_commit_close:
            return "commit"
        if t < self.t_reveal_close:
            return "reveal"
        if t < self.t_reveal_grace_close:
            return "reveal_grace"
        if t < self.t_vdf_deadline:
            return "vdf"
        return "mix_ready"


def _round_zero_aligned_time(
    round_id: int,
    genesis_t0: int,
    commit_sec: int,
    reveal_sec: int,
    vdf_sec: int,
    reveal_grace_sec: int,
) -> int:
    """Return the epoch second at which the given round starts (commit open)."""
    per_round = commit_sec + reveal_sec + reveal_grace_sec + vdf_sec
    if per_round <= 0:
        raise ValueError("Per-round duration must be positive")
    if round_id < 0:
        round_id = 0
    return int(genesis_t0) + round_id * per_round


def current_round_id(
    *,
    now_ts: float | int,
    genesis_t0: float | int,
    commit_sec: int,
    reveal_sec: int,
    vdf_sec: int,
    reveal_grace_sec: int = 0,
) -> int:
    """Compute the round id active at 'now_ts'."""
    per_round = commit_sec + reveal_sec + reveal_grace_sec + vdf_sec
    if per_round <= 0:
        raise ValueError("Per-round duration must be positive")
    delta = int(now_ts) - int(genesis_t0)
    if delta <= 0:
        return 0
    return delta // per_round  # floor division


def schedule_for_round(
    *,
    round_id: int,
    genesis_t0: float | int,
    commit_sec: int,
    reveal_sec: int,
    vdf_sec: int,
    reveal_grace_sec: int = 0,
) -> RoundSchedule:
    """Get the full schedule (boundaries & durations) for a specific round."""
    t0 = _round_zero_aligned_time(
        round_id=round_id,
        genesis_t0=int(genesis_t0),
        commit_sec=commit_sec,
        reveal_sec=reveal_sec,
        vdf_sec=vdf_sec,
        reveal_grace_sec=reveal_grace_sec,
    )

    t_commit_open = t0
    t_commit_close = t_commit_open + commit_sec

    t_reveal_open = t_commit_close
    t_reveal_close = t_reveal_open + reveal_sec

    t_reveal_grace_open = t_reveal_close
    t_reveal_grace_close = t_reveal_grace_open + reveal_grace_sec

    t_vdf_start = t_reveal_grace_close
    t_vdf_deadline = t_vdf_start + vdf_sec

    t_mix_ready = t_vdf_deadline

    return RoundSchedule(
        round_id=int(round_id),
        t_commit_open=t_commit_open,
        t_commit_close=t_commit_close,
        t_reveal_open=t_reveal_open,
        t_reveal_close=t_reveal_close,
        t_reveal_grace_open=t_reveal_grace_open,
        t_reveal_grace_close=t_reveal_grace_close,
        t_vdf_start=t_vdf_start,
        t_vdf_deadline=t_vdf_deadline,
        t_mix_ready=t_mix_ready,
        commit_sec=int(commit_sec),
        reveal_sec=int(reveal_sec),
        reveal_grace_sec=int(reveal_grace_sec),
        vdf_sec=int(vdf_sec),
    )


def schedule_for_time(
    *,
    now_ts: float | int,
    genesis_t0: float | int,
    commit_sec: int,
    reveal_sec: int,
    vdf_sec: int,
    reveal_grace_sec: int = 0,
) -> RoundSchedule:
    """Return the active round's schedule at 'now_ts'."""
    rid = current_round_id(
        now_ts=now_ts,
        genesis_t0=genesis_t0,
        commit_sec=commit_sec,
        reveal_sec=reveal_sec,
        vdf_sec=vdf_sec,
        reveal_grace_sec=reveal_grace_sec,
    )
    return schedule_for_round(
        round_id=rid,
        genesis_t0=genesis_t0,
        commit_sec=commit_sec,
        reveal_sec=reveal_sec,
        vdf_sec=vdf_sec,
        reveal_grace_sec=reveal_grace_sec,
    )


def next_event_eta(now_ts: float | int, sched: RoundSchedule) -> Tuple[Phase, int]:
    """Return (next_event, eta_seconds) from the given 'now_ts' within a schedule.

    If the current phase ended exactly at 'now_ts', we return the start of the
    next phase with ETA 0.

    Events reported:
      - "commit" → ETA until commit close (i.e., last moment to accept commits)
      - "reveal" → ETA until reveal close
      - "reveal_grace" → ETA until reveal grace close
      - "vdf" → ETA until VDF deadline (mix ready)
      - "mix_ready" → 0 if already past deadline, otherwise time to deadline
    """
    t = int(now_ts)

    if t < sched.t_commit_close:
        return ("commit", sched.t_commit_close - t)
    if t < sched.t_reveal_close:
        return ("reveal", sched.t_reveal_close - t)
    if t < sched.t_reveal_grace_close:
        return ("reveal_grace", sched.t_reveal_grace_close - t)
    if t < sched.t_vdf_deadline:
        return ("vdf", sched.t_vdf_deadline - t)
    return ("mix_ready", 0)


def time_to_round_start(
    *,
    target_round_id: int,
    now_ts: float | int,
    genesis_t0: float | int,
    commit_sec: int,
    reveal_sec: int,
    vdf_sec: int,
    reveal_grace_sec: int = 0,
) -> int:
    """ETA in seconds from 'now_ts' to the start of 'target_round_id'."""
    t0 = _round_zero_aligned_time(
        round_id=target_round_id,
        genesis_t0=int(genesis_t0),
        commit_sec=commit_sec,
        reveal_sec=reveal_sec,
        vdf_sec=vdf_sec,
        reveal_grace_sec=reveal_grace_sec,
    )
    eta = int(t0) - int(now_ts)
    return 0 if eta < 0 else eta


# Optional: light integration helpers (do not introduce hard deps)

def schedule_from_state(
    *,
    state: object,
    now_ts: Optional[float | int] = None,
    default_genesis_attr: str = "genesis_time",
    default_commit_attr: str = "commit_sec",
    default_reveal_attr: str = "reveal_sec",
    default_grace_attr: str = "reveal_grace_sec",
    default_vdf_attr: str = "vdf_sec",
) -> RoundSchedule:
    """Build a schedule using a BeaconState-like object.

    We access attributes by name to avoid a hard import. If 'now_ts' is None,
    callers should pass a block timestamp or wall clock (time.time()).
    """
    g = int(getattr(state, default_genesis_attr))
    c = int(getattr(state, default_commit_attr))
    r = int(getattr(state, default_reveal_attr))
    gr = int(getattr(state, default_grace_attr, 0))
    v = int(getattr(state, default_vdf_attr))

    if now_ts is None:
        # Intentionally avoid importing a util; caller supplies now_ts.
        raise ValueError("now_ts is required when using schedule_from_state")

    return schedule_for_time(
        now_ts=now_ts,
        genesis_t0=g,
        commit_sec=c,
        reveal_sec=r,
        reveal_grace_sec=gr,
        vdf_sec=v,
    )


__all__ = [
    "Phase",
    "RoundSchedule",
    "current_round_id",
    "schedule_for_round",
    "schedule_for_time",
    "next_event_eta",
    "time_to_round_start",
    "schedule_from_state",
]
