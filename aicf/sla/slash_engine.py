from __future__ import annotations
"""
Slash engine: compute penalties, emit SlashEvent, reduce stake, and jail on repeats.

This module applies deterministic, policy-driven slashing for SLA violations.
It integrates with the registry/staking/penalties layers and emits a typed
SlashEvent for downstream consumers (metrics, audit logs, dashboards).

Design goals:
  * Pure calculation of slash amounts given current stake and severity.
  * Defensive integration with dependencies (staking/penalties/metrics).
  * Simple repeat-offense counter in a sliding window -> optional jailing.

Typical flow:
  1) detect policy violation elsewhere (e.g., SLA evaluator or proof checker),
  2) call SlashEngine.record_violation(provider_id, reason, severity),
  3) engine computes amount, reduces stake, emits SlashEvent, and may jail.

Notes:
  - `severity` is a float in [0, 1], scaling the base penalty.
  - Slash amounts are clamped: [min_slash, max_slash] and to available stake.
  - Jailing triggers when recent violation count >= `jail_after` within `window_s`.
"""


from dataclasses import dataclass
from typing import Deque, Dict, Optional, Protocol, Any
from collections import deque
import time

# Types from AICF
try:
    from aicf.aitypes.provider import ProviderId
except Exception:  # pragma: no cover - fallback for static tools
    ProviderId = str  # type: ignore[misc,assignment]

try:
    from aicf.aitypes.events import SlashEvent  # expected event type
except Exception:  # pragma: no cover - minimal fallback to keep module importable
    @dataclass(frozen=True)
    class SlashEvent:  # type: ignore[no-redef]
        provider_id: ProviderId
        amount: int
        reason: str
        new_stake: int
        jailed: bool
        violations_in_window: int
        ts_s: float


# Optional metrics (counters/histograms) — resolved dynamically
def _inc(counter: Optional[Any], **labels: object) -> None:
    try:
        if counter is not None:
            counter.labels(**labels).inc()  # type: ignore[attr-defined]
    except Exception:
        pass


def _observe(hist: Optional[Any], value: float, **labels: object) -> None:
    try:
        if hist is not None:
            hist.labels(**labels).observe(value)  # type: ignore[attr-defined]
    except Exception:
        pass


# Registry / staking / penalties interfaces (narrow protocols)
class StakingAPI(Protocol):
    def get_stake(self, provider_id: ProviderId) -> int: ...
    def slash(self, provider_id: ProviderId, amount: int, reason: str) -> int: ...
    # returns new stake after slashing


class PenaltiesAPI(Protocol):
    def jail(self, provider_id: ProviderId, reason: str) -> None: ...
    def is_jailed(self, provider_id: ProviderId) -> bool: ...


@dataclass(frozen=True)
class SlashPolicy:
    """
    Slashing policy knobs.

    Attributes:
        base_bps: base penalty in basis points (1/100 of a percent) applied
                  to current stake; scaled by `severity` in [0,1].
        min_slash: minimum absolute slash amount (in stake units).
        max_slash: maximum absolute slash amount (cap).
        jail_after: number of violations within `window_s` to trigger jailing.
        window_s: sliding window (seconds) for repeat offense counting.
        clamp_to_stake: if True, clamp slash to available stake.
    """
    base_bps: int = 50               # 0.50% baseline
    min_slash: int = 10_000          # in smallest token units
    max_slash: int = 1_000_000_000   # cap to avoid catastrophic errors
    jail_after: int = 3
    window_s: float = 3_600.0        # 1 hour window
    clamp_to_stake: bool = True


class SlashEngine:
    """
    Engine that applies stake reductions and emits SlashEvent for violations.

    Dependencies:
      - staking: manages stake balances and performs the slash mutation.
      - penalties: handles jailing / jail-state queries.
      - metrics (optional): object exposing Prometheus counters/histograms with
        label()-capable .inc()/.observe() methods. Expected (optional) fields:
          * metrics.slash_events            (Counter)
          * metrics.slash_amounts           (Histogram)
          * metrics.jail_events             (Counter)
    """

    def __init__(
        self,
        staking: StakingAPI,
        penalties: PenaltiesAPI,
        *,
        policy: SlashPolicy = SlashPolicy(),
        metrics: Optional[Any] = None,
        clock_fn=time.time,
    ) -> None:
        self._staking = staking
        self._penalties = penalties
        self._policy = policy
        self._metrics = metrics
        self._clock = clock_fn

        # (in-memory) recent-violations window per provider
        self._recent: Dict[ProviderId, Deque[float]] = {}

        # Pre-bind metric handles if available
        self._m_slash_events = getattr(metrics, "slash_events", None) if metrics else None
        self._m_slash_amounts = getattr(metrics, "slash_amounts", None) if metrics else None
        self._m_jail_events = getattr(metrics, "jail_events", None) if metrics else None

    # ----------------------------
    # Public API
    # ----------------------------

    def record_violation(
        self,
        provider_id: ProviderId,
        *,
        reason: str,
        severity: float = 1.0,
        ts_s: Optional[float] = None,
    ) -> SlashEvent:
        """
        Record a violation for `provider_id`, apply slash, maybe jail, and return SlashEvent.

        Args:
            provider_id: offending provider.
            reason: short string reason code (e.g., "SLA_TRAPS_LOW", "QOS_FAIL").
            severity: multiplier in [0,1], scales the base penalty.
            ts_s: optional event timestamp (seconds). Defaults to now().

        Returns:
            SlashEvent reflecting the mutation (new stake and jail status).
        """
        t = self._clock() if ts_s is None else float(ts_s)
        sev = min(1.0, max(0.0, float(severity)))

        # Compute slash amount from policy and current stake
        stake = max(0, int(self._staking.get_stake(provider_id)))
        base = (stake * self._policy.base_bps) // 10_000
        raw_amount = int(round(base * sev))
        amount = max(self._policy.min_slash, min(self._policy.max_slash, raw_amount))

        if self._policy.clamp_to_stake:
            amount = min(amount, stake)

        # Apply slash (no-op if amount == 0)
        new_stake = self._staking.slash(provider_id, amount, reason) if amount > 0 else stake

        # Update sliding window and check jail threshold
        win = self._recent.setdefault(provider_id, deque())
        self._prune_old(win, t)
        win.append(t)
        jailed_before = self._penalties.is_jailed(provider_id)
        jailed_now = jailed_before

        if len(win) >= self._policy.jail_after and not jailed_before:
            # Trigger jail
            self._penalties.jail(provider_id, reason=f"repeat_violations:{reason}")
            jailed_now = True
            _inc(self._m_jail_events, reason=reason)

        # Emit metrics
        _inc(self._m_slash_events, reason=reason)
        _observe(self._m_slash_amounts, float(amount), reason=reason)

        ev = SlashEvent(
            provider_id=provider_id,
            amount=amount,
            reason=reason,
            new_stake=new_stake,
            jailed=jailed_now,
            violations_in_window=len(win),
            ts_s=t,
        )
        return ev

    # ----------------------------
    # Helpers
    # ----------------------------

    def _prune_old(self, q: Deque[float], now_s: float) -> None:
        """Drop timestamps older than the configured sliding window."""
        cutoff = now_s - self._policy.window_s
        while q and q[0] < cutoff:
            q.popleft()

    # Expose policy for observability
    @property
    def policy(self) -> SlashPolicy:
        return self._policy
