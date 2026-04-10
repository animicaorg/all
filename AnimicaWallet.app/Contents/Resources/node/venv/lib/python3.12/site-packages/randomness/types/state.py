from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .core import BeaconOut, RoundId


class Phase(str, Enum):
    """Lifecycle phases of a beacon round."""

    COMMIT = "commit"
    REVEAL = "reveal"
    VDF = "vdf"
    IDLE = "idle"  # Between VDF end and next round start (or when VDF disabled)


@dataclass(frozen=True, slots=True)
class BeaconParams:
    """
    Static parameters that define the timing and behavior of the beacon.

    All durations are specified in seconds. The total active time
    (commit + reveal + vdf) must be <= round_length.

    Fields:
      t0               — UNIX timestamp anchor for round 0 start
      round_length     — length of a full round in seconds
      commit_duration  — commit window length
      reveal_duration  — reveal window length (not including grace)
      vdf_duration     — optional VDF window length (0 if disabled)
      reveal_grace     — extra time after reveal window where late reveals are accepted
      vdf_iterations   — work factor for the VDF (scheme-specific)
      vdf_enabled      — whether the VDF phase is expected/used
      qrng_enabled     — whether QRNG is mixed in (handled elsewhere; tracked for visibility)
    """

    t0: int
    round_length: int
    commit_duration: int
    reveal_duration: int
    vdf_duration: int
    reveal_grace: int
    vdf_iterations: int
    vdf_enabled: bool = True
    qrng_enabled: bool = False

    def __post_init__(self) -> None:  # type: ignore[override]
        for name in (
            "t0",
            "round_length",
            "commit_duration",
            "reveal_duration",
            "vdf_duration",
            "reveal_grace",
            "vdf_iterations",
        ):
            v = getattr(self, name)
            if not isinstance(v, int):
                raise TypeError(f"{name} must be int")
            if v < 0:
                raise ValueError(f"{name} must be non-negative")
        active = (
            self.commit_duration
            + self.reveal_duration
            + (self.vdf_duration if self.vdf_enabled else 0)
        )
        if active > self.round_length:
            raise ValueError(
                "commit_duration + reveal_duration + vdf_duration must be <= round_length"
            )


@dataclass(slots=True)
class BeaconState:
    """
    Beacon state machine snapshot.

    Tracks:
      • params        — static timing/behavior parameters
      • current_round — the round identifier being processed "now"
      • current_out   — finalized output for current_round (None until finalized)
      • previous_out  — finalized output of the previous round (if any)

    Helper methods provide phase/round calculations and timing windows.
    """

    params: BeaconParams
    current_round: RoundId
    current_out: Optional[BeaconOut] = None
    previous_out: Optional[BeaconOut] = None

    # ---------- Round/phase time math -----------------------------------------

    def round_start_time(self, r: RoundId) -> int:
        """UNIX timestamp when round r starts."""
        if int(r) < 0:
            raise ValueError("round must be non-negative")
        return self.params.t0 + int(r) * self.params.round_length

    def round_for_time(self, now_ts: int) -> RoundId:
        """Return the round active at the given UNIX timestamp."""
        if now_ts < self.params.t0:
            return RoundId(0)
        delta = now_ts - self.params.t0
        return RoundId(delta // self.params.round_length)

    def commit_window(self, r: RoundId) -> Tuple[int, int]:
        """Inclusive start, exclusive end timestamps for the commit window."""
        start = self.round_start_time(r)
        end = start + self.params.commit_duration
        return start, end

    def reveal_window(self, r: RoundId) -> Tuple[int, int]:
        """Inclusive start, exclusive end timestamps for the reveal window."""
        c_start, c_end = self.commit_window(r)
        start = c_end
        end = start + self.params.reveal_duration
        return start, end

    def reveal_grace_window(self, r: RoundId) -> Tuple[int, int]:
        """Grace window where late reveals are still accepted (best-effort)."""
        r_start, r_end = self.reveal_window(r)
        return r_end, r_end + self.params.reveal_grace

    def vdf_window(self, r: RoundId) -> Tuple[int, int]:
        """
        Inclusive start, exclusive end timestamps for the VDF window.
        If VDF is disabled, returns a zero-length window at the reveal end.
        """
        r_start, r_end = self.reveal_window(r)
        start = r_end
        if not self.params.vdf_enabled or self.params.vdf_duration == 0:
            return start, start
        end = start + self.params.vdf_duration
        return start, end

    def phase_at(self, now_ts: int) -> Phase:
        """Phase for the current wall time."""
        r = self.round_for_time(now_ts)
        start, c_end = self.commit_window(r)
        r_start, r_end = self.reveal_window(r)
        g_start, g_end = self.reveal_grace_window(r)
        v_start, v_end = self.vdf_window(r)
        if start <= now_ts < c_end:
            return Phase.COMMIT
        if r_start <= now_ts < r_end or (r_end <= now_ts < g_end):
            # Grace is treated as part of REVEAL phase from the perspective of accept rules.
            return Phase.REVEAL
        if self.params.vdf_enabled and v_start <= now_ts < v_end:
            return Phase.VDF
        return Phase.IDLE

    # ---------- Accept rules ---------------------------------------------------

    def can_accept_commit(self, round_id: RoundId, now_ts: int) -> bool:
        """True if a commit targeting round_id is timely at now_ts."""
        s, e = self.commit_window(round_id)
        return s <= now_ts < e

    def can_accept_reveal(self, round_id: RoundId, now_ts: int) -> bool:
        """True if a reveal targeting round_id is timely at now_ts (incl. grace)."""
        s, e = self.reveal_window(round_id)
        g_s, g_e = self.reveal_grace_window(round_id)
        return (s <= now_ts < e) or (e <= now_ts < g_e)

    def can_accept_vdf(self, round_id: RoundId, now_ts: int) -> bool:
        """True if a VDF proof for round_id is timely at now_ts (if enabled)."""
        if not self.params.vdf_enabled:
            return False
        s, e = self.vdf_window(round_id)
        return s <= now_ts < e

    # ---------- State transitions ---------------------------------------------

    def advance_to_round(self, new_round: RoundId) -> None:
        """
        Advance the state machine to a new round.
        Moves current_out → previous_out and clears current_out.
        """
        if int(new_round) < int(self.current_round):
            raise ValueError("cannot go backwards in rounds")
        if int(new_round) == int(self.current_round):
            return
        self.previous_out = self.current_out
        self.current_out = None
        self.current_round = RoundId(int(new_round))

    def finalize_current(self, out: BeaconOut) -> None:
        """Record the finalized BeaconOut for the current round (invariants checked)."""
        if int(out.round) != int(self.current_round):
            raise ValueError("output round does not match current_round")
        self.current_out = out

    # ---------- Convenience ----------------------------------------------------

    def windows(self, r: Optional[RoundId] = None) -> dict[str, Tuple[int, int]]:
        """
        Return all windows for a round as a dict for instrumentation/logging.
        Keys: commit, reveal, reveal_grace, vdf, round
        """
        r = self.current_round if r is None else r
        rs = self.round_start_time(r)
        return {
            "round": (rs, rs + self.params.round_length),
            "commit": self.commit_window(r),
            "reveal": self.reveal_window(r),
            "reveal_grace": self.reveal_grace_window(r),
            "vdf": self.vdf_window(r),
        }


__all__ = ["BeaconParams", "BeaconState", "Phase"]
