from __future__ import annotations

"""
AICF Penalties: jailing, cooldowns, and slashing reason codes.

This module centralizes provider penalty logic. It is storage-agnostic and
keeps a minimal in-memory state (which callers may persist externally if
desired). It exposes:
  - SlashReason: canonical reason codes (stable for metrics/auditing)
  - PenaltyConfig: tunables for slash ratios, jail durations, cooldowns
  - PenaltyEngine: helper with record-keeping + slash/jail/cooldown decisions

Design goals
------------
- Deterministic: same inputs => same outputs. All time is passed in (or
  derived from a provided clock) to avoid hidden nondeterminism.
- Pluggable: actual stake deduction is delegated to a provided hook.
- Observability: returns rich outcomes so callers can emit events/metrics.
- Safety: clamps and per-reason bounds to avoid catastrophic slashes.

Typical usage
-------------
    from aicf.registry.penalties import (
        PenaltyEngine, PenaltyConfig, SlashReason
    )

    engine = PenaltyEngine(config=PenaltyConfig())

    # When a violation is detected:
    outcome = engine.apply_slash_and_penalties(
        provider_id="prov_123",
        reason=SlashReason.MISSED_DEADLINE,
        stake_reader=lambda pid: registry.get(pid).stake_total,
        slash_hook=lambda pid, amt: staking.slash(pid, amt),
        now=time.time(),
    )
    if outcome.jailed_until and outcome.jailed_until > now:
        registry.set_status(provider_id, "JAILED")
    if outcome.cooldown_until and outcome.cooldown_until > now:
        scheduler.set_cooldown(provider_id, outcome.cooldown_until)

Notes
-----
- Cooldown: provider is temporarily deprioritized/blocked from new assignments,
  but not fully jailed. Jailing: provider is hard-disabled until time passes.
- Consecutive offenses inside `offense_window_seconds` increase penalties.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import math
import time as _time


# ---- Reason codes ------------------------------------------------------------

class SlashReason(str, Enum):
    """Stable reason codes for slashing/jailing decisions."""
    INVALID_PROOF = "INVALID_PROOF"           # proof doesn't verify / forged
    MISSED_DEADLINE = "MISSED_DEADLINE"       # exceeded SLA time window
    LEASE_VIOLATION = "LEASE_VIOLATION"       # lost lease / double-work issues
    DOUBLE_SUBMIT = "DOUBLE_SUBMIT"           # duplicate/conflicting claim
    BAD_ATTESTATION = "BAD_ATTESTATION"       # attestation invalid / mismatched
    MALFORMED_RESULT = "MALFORMED_RESULT"     # cannot parse / deviates from schema
    UNAUTHORIZED_REGION = "UNAUTHORIZED_REGION"  # violated geo policy
    DOS_ABUSE = "DOS_ABUSE"                   # spam / abusive behavior
    HEALTH_TIMEOUT = "HEALTH_TIMEOUT"         # repeated heartbeat timeouts
    OTHER = "OTHER"                           # reserved / future use


# ---- Configuration -----------------------------------------------------------

@dataclass(frozen=True)
class PenaltyConfig:
    """
    Tunable policy knobs for penalties.

    Ratios are applied to the current stake_total unless an absolute override
    is provided by the caller (rare). Results are clamped to [min_slash, max_slash].
    Jail/cooldown durations increase with consecutive offenses.
    """
    # Base slash ratios by reason (fraction of stake_total)
    slash_ratio_by_reason: Dict[SlashReason, float] = field(
        default_factory=lambda: {
            SlashReason.INVALID_PROOF: 0.10,
            SlashReason.MISSED_DEADLINE: 0.01,
            SlashReason.LEASE_VIOLATION: 0.04,
            SlashReason.DOUBLE_SUBMIT: 0.06,
            SlashReason.BAD_ATTESTATION: 0.08,
            SlashReason.MALFORMED_RESULT: 0.02,
            SlashReason.UNAUTHORIZED_REGION: 0.015,
            SlashReason.DOS_ABUSE: 0.05,
            SlashReason.HEALTH_TIMEOUT: 0.005,
            SlashReason.OTHER: 0.01,
        }
    )

    # Jail durations by reason (seconds)
    jail_seconds_by_reason: Dict[SlashReason, int] = field(
        default_factory=lambda: {
            SlashReason.INVALID_PROOF: 24 * 3600,
            SlashReason.MISSED_DEADLINE: 2 * 3600,
            SlashReason.LEASE_VIOLATION: 6 * 3600,
            SlashReason.DOUBLE_SUBMIT: 12 * 3600,
            SlashReason.BAD_ATTESTATION: 24 * 3600,
            SlashReason.MALFORMED_RESULT: 1 * 3600,
            SlashReason.UNAUTHORIZED_REGION: 3 * 3600,
            SlashReason.DOS_ABUSE: 24 * 3600,
            SlashReason.HEALTH_TIMEOUT: 30 * 60,
            SlashReason.OTHER: 2 * 3600,
        }
    )

    # Cooldown base seconds by reason (lighter than jail; can be 0)
    cooldown_seconds_by_reason: Dict[SlashReason, int] = field(
        default_factory=lambda: {
            SlashReason.INVALID_PROOF: 6 * 3600,
            SlashReason.MISSED_DEADLINE: 30 * 60,
            SlashReason.LEASE_VIOLATION: 2 * 3600,
            SlashReason.DOUBLE_SUBMIT: 3 * 3600,
            SlashReason.BAD_ATTESTATION: 3 * 3600,
            SlashReason.MALFORMED_RESULT: 15 * 60,
            SlashReason.UNAUTHORIZED_REGION: 45 * 60,
            SlashReason.DOS_ABUSE: 6 * 3600,
            SlashReason.HEALTH_TIMEOUT: 10 * 60,
            SlashReason.OTHER: 30 * 60,
        }
    )

    # Global clamps for slash amounts
    min_slash: float = 0.0
    max_slash: float = 100_000.0  # conservative default; set to network value

    # Offense aggregation window and multiplicative ramp
    offense_window_seconds: int = 24 * 3600
    consecutive_multiplier: float = 1.35  # ^(consecutive_offenses - 1)

    # Optional absolute jail upper bound (seconds). 0 = no extra cap.
    max_jail_seconds: int = 7 * 24 * 3600

    # Optional absolute cooldown upper bound (seconds). 0 = no extra cap.
    max_cooldown_seconds: int = 24 * 3600


# ---- Internal state ----------------------------------------------------------

@dataclass
class PenaltyRecord:
    provider_id: str
    jailed_until: float = 0.0
    cooldown_until: float = 0.0
    total_slashed: float = 0.0
    # offense history: list of (ts, reason)
    offenses: List[Tuple[float, SlashReason]] = field(default_factory=list)

    def purge_old(self, now: float, window: int) -> None:
        if not self.offenses:
            return
        cutoff = now - float(window)
        # Keep only recent offenses within the window
        self.offenses = [(ts, r) for (ts, r) in self.offenses if ts >= cutoff]


@dataclass
class SlashOutcome:
    provider_id: str
    reason: SlashReason
    slashed_amount: float
    consecutive_count: int
    jailed_until: float
    cooldown_until: float
    notes: str = ""


# Hooks used by the engine (duck-typed callables)
StakeReader = Callable[[str], float]
SlashHook = Callable[[str, float], None]


# ---- Engine -----------------------------------------------------------------

class PenaltyEngine:
    """
    Stateless policy + small state tracker for jailing/cooldowns.

    State layout is minimal; callers can snapshot/restore externally. To
    integrate with a persistent registry, forward slash deductions to a
    staking module using `slash_hook`.
    """

    def __init__(self, config: PenaltyConfig, *, clock: Callable[[], float] | None = None) -> None:
        self.config = config
        self._clock = clock or _time.time
        self._records: Dict[str, PenaltyRecord] = {}

    # --- Public queries -------------------------------------------------------

    def record_for(self, provider_id: str) -> PenaltyRecord:
        rec = self._records.get(provider_id)
        if rec is None:
            rec = PenaltyRecord(provider_id=provider_id)
            self._records[provider_id] = rec
        return rec

    def is_jailed(self, provider_id: str, now: Optional[float] = None) -> bool:
        now = self._now(now)
        return self.record_for(provider_id).jailed_until > now

    def is_on_cooldown(self, provider_id: str, now: Optional[float] = None) -> bool:
        now = self._now(now)
        return self.record_for(provider_id).cooldown_until > now

    # --- Mutations ------------------------------------------------------------

    def jail(self, provider_id: str, seconds: int, *, now: Optional[float] = None) -> float:
        now = self._now(now)
        rec = self.record_for(provider_id)
        rec.jailed_until = max(rec.jailed_until, self._cap_jail(now + float(seconds)))
        return rec.jailed_until

    def unjail(self, provider_id: str) -> None:
        self.record_for(provider_id).jailed_until = 0.0

    def set_cooldown(self, provider_id: str, seconds: int, *, now: Optional[float] = None) -> float:
        now = self._now(now)
        rec = self.record_for(provider_id)
        rec.cooldown_until = max(rec.cooldown_until, self._cap_cooldown(now + float(seconds)))
        return rec.cooldown_until

    def clear_cooldown(self, provider_id: str) -> None:
        self.record_for(provider_id).cooldown_until = 0.0

    # --- Core path ------------------------------------------------------------

    def apply_slash_and_penalties(
        self,
        provider_id: str,
        reason: SlashReason,
        *,
        stake_reader: StakeReader,
        slash_hook: SlashHook | None = None,
        explicit_slash_amount: float | None = None,
        now: Optional[float] = None,
    ) -> SlashOutcome:
        """
        Compute and apply slashing + jail + cooldown for a detected violation.

        - Records offense in the rolling window.
        - Computes consecutive offense count to scale penalties.
        - Deducts stake via `slash_hook` (if provided).
        - Sets jail/cooldown timers; returns a structured outcome.
        """
        cfg = self.config
        now = self._now(now)
        rec = self.record_for(provider_id)

        # Maintain rolling window
        rec.purge_old(now, cfg.offense_window_seconds)

        # Register the new offense
        rec.offenses.append((now, reason))
        consecutive = self._consecutive_offenses(rec)

        # Slash calculation
        base_ratio = cfg.slash_ratio_by_reason.get(reason, 0.0)
        stake = max(0.0, float(stake_reader(provider_id)))
        raw = explicit_slash_amount if explicit_slash_amount is not None else stake * base_ratio
        scaled = self._scale_by_consecutive(raw, consecutive, cfg.consecutive_multiplier)
        slashed = self._clamp(scaled, cfg.min_slash, cfg.max_slash)

        # Apply slash via hook (caller handles persistence/ledger)
        if slashed > 0 and slash_hook is not None:
            slash_hook(provider_id, slashed)
        rec.total_slashed += slashed

        # Jail & cooldown
        jail_base = cfg.jail_seconds_by_reason.get(reason, 0)
        jail_seconds = self._scale_duration(jail_base, consecutive, cfg.consecutive_multiplier, cfg.max_jail_seconds)
        jailed_until = self.jail(provider_id, jail_seconds, now=now) if jail_seconds > 0 else rec.jailed_until

        cd_base = cfg.cooldown_seconds_by_reason.get(reason, 0)
        cooldown_seconds = self._scale_duration(cd_base, consecutive, cfg.consecutive_multiplier, cfg.max_cooldown_seconds)
        cooldown_until = self.set_cooldown(provider_id, cooldown_seconds, now=now) if cooldown_seconds > 0 else rec.cooldown_until

        notes = f"stake={stake:.6f}, base_ratio={base_ratio:.4f}, consecutive={consecutive}, jail={jail_seconds}s, cooldown={cooldown_seconds}s"
        return SlashOutcome(
            provider_id=provider_id,
            reason=reason,
            slashed_amount=slashed,
            consecutive_count=consecutive,
            jailed_until=jailed_until,
            cooldown_until=cooldown_until,
            notes=notes,
        )

    # --- Utilities ------------------------------------------------------------

    def _now(self, now: Optional[float]) -> float:
        return float(now) if now is not None else float(self._clock())

    @staticmethod
    def _scale_by_consecutive(amount: float, consecutive: int, multiplier: float) -> float:
        if consecutive <= 1:
            return amount
        return amount * (multiplier ** (consecutive - 1))

    @staticmethod
    def _scale_duration(base_seconds: int, consecutive: int, multiplier: float, max_cap: int) -> int:
        if base_seconds <= 0:
            return 0
        if consecutive <= 1:
            dur = float(base_seconds)
        else:
            dur = float(base_seconds) * (multiplier ** (consecutive - 1))
        if max_cap > 0:
            dur = min(dur, float(max_cap))
        # Deterministic rounding up to ensure penalties are not under-applied
        return int(math.ceil(dur))

    @staticmethod
    def _clamp(x: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, x))

    def _cap_jail(self, until_ts: float) -> float:
        if self.config.max_jail_seconds <= 0:
            return until_ts
        now = self._now(None)
        return min(until_ts, now + float(self.config.max_jail_seconds))

    def _cap_cooldown(self, until_ts: float) -> float:
        if self.config.max_cooldown_seconds <= 0:
            return until_ts
        now = self._now(None)
        return min(until_ts, now + float(self.config.max_cooldown_seconds))

    @staticmethod
    def _consecutive_offenses(rec: PenaltyRecord) -> int:
        """
        Count consecutive offenses (most recent contiguous sequence).
        If multiple different reasons appear consecutively, they still count as
        consecutive within the window.
        """
        if not rec.offenses:
            return 0
        # Offenses are appended in time order; count from the end backwards until a gap.
        count = 0
        last_ts = None
        for ts, _reason in reversed(rec.offenses):
            if last_ts is None or ts <= last_ts:
                count += 1
                last_ts = ts
            else:
                break
        return count


# ---- Optional event integration (duck-typed) --------------------------------
# We don't import the project's event bus here to avoid circular deps.
# Callers can translate `SlashOutcome` into their own Event objects.


__all__ = [
    "SlashReason",
    "PenaltyConfig",
    "PenaltyRecord",
    "SlashOutcome",
    "PenaltyEngine",
]
