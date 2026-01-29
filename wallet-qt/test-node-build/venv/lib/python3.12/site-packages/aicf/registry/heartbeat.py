from __future__ import annotations

"""
AICF Provider Heartbeats & Health Scoring

This module tracks liveness/health pings for providers and maintains a decaying
health score in [0, 1]. It is intentionally storage-agnostic: you can wire it
to your registry or DB via simple callbacks.

Key ideas
---------
- Record pings with (ok: bool, latency_ms: float|None).
- Exponential time-decay toward 0 with configurable half-life.
- Successes push score upward (faster when latency is near/below target).
- Failures penalize the score (heavier with consecutive failures).
- Derive a status: HEALTHY / DEGRADED / UNRESPONSIVE using thresholds.
- Optional status-change hook to update the registry or emit alerts.
- Prometheus metrics for pings, latency, and per-provider score.

Usage
-----
    hb = HeartbeatMonitor()
    hb.record_ping("prov-1", ok=True, latency_ms=120.0)
    allowed = hb.current_status("prov-1")  # ("HEALTHY", score, last_seen)

Integration with registry
-------------------------
Pass a `status_hook` to mirror state into your registry, e.g.:

    def on_status_change(pid, new_status, reason):
        registry.set_status(pid, new_status, reason=reason)

    hb = HeartbeatMonitor(status_hook=on_status_change)
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

try:
    # Prometheus is optional; the monitor works without it.
    from prometheus_client import Counter, Gauge, Histogram
except Exception:  # pragma: no cover - optional dependency
    Counter = Histogram = Gauge = None  # type: ignore[misc]

# ---- Logging ----------------------------------------------------------------

log = logging.getLogger(__name__)

# ---- Configuration -----------------------------------------------------------


@dataclass
class HeartbeatConfig:
    # Time-decay half-life (seconds). Score tends toward 0 in absence of pings.
    halflife_s: float = 15 * 60.0  # 15 minutes

    # Target and tolerance for latency contribution to health impulses.
    latency_target_ms: float = 250.0
    latency_tolerance_ms: float = (
        750.0  # beyond target+tolerance, latency contributes ~0
    )

    # Upward movement aggressiveness on success: s += asc_rate * (1 - s) * impulse
    asc_rate: float = 0.5

    # Failure penalties (base + per consecutive failure), then clamped.
    fail_penalty_base: float = 0.18
    fail_penalty_per_consecutive: float = 0.08
    fail_penalty_cap: float = 0.9

    # Status thresholds
    degrade_threshold: float = 0.55
    down_threshold: float = 0.22

    # Liveness windows
    stale_timeout_s: float = 5 * 60.0  # no ping for 5 minutes ⇒ considered stale
    max_consecutive_fail_for_down: int = 5


# Attempt to import defaults from aicf.config if available
try:  # pragma: no cover - best-effort import
    from aicf.config import (AICF_DEGRADE_THRESHOLD, AICF_DOWN_THRESHOLD,
                             AICF_FAIL_PENALTY_BASE, AICF_FAIL_PENALTY_CAP,
                             AICF_FAIL_PENALTY_PER_CONSEC,
                             AICF_HEARTBEAT_ASC_RATE,
                             AICF_HEARTBEAT_HALFLIFE_S, AICF_LATENCY_TARGET_MS,
                             AICF_LATENCY_TOLERANCE_MS,
                             AICF_MAX_CONSEC_FAIL_FOR_DOWN,
                             AICF_STALE_TIMEOUT_S)

    _cfg_from_env = HeartbeatConfig(
        halflife_s=float(AICF_HEARTBEAT_HALFLIFE_S),
        latency_target_ms=float(AICF_LATENCY_TARGET_MS),
        latency_tolerance_ms=float(AICF_LATENCY_TOLERANCE_MS),
        asc_rate=float(AICF_HEARTBEAT_ASC_RATE),
        fail_penalty_base=float(AICF_FAIL_PENALTY_BASE),
        fail_penalty_per_consecutive=float(AICF_FAIL_PENALTY_PER_CONSEC),
        fail_penalty_cap=float(AICF_FAIL_PENALTY_CAP),
        degrade_threshold=float(AICF_DEGRADE_THRESHOLD),
        down_threshold=float(AICF_DOWN_THRESHOLD),
        stale_timeout_s=float(AICF_STALE_TIMEOUT_S),
        max_consecutive_fail_for_down=int(AICF_MAX_CONSEC_FAIL_FOR_DOWN),
    )
except Exception:  # pragma: no cover - config is optional
    _cfg_from_env = None


# ---- Metrics ----------------------------------------------------------------

if Counter is not None:  # pragma: no cover - trivial wiring
    HB_PINGS = Counter(
        "aicf_heartbeat_total",
        "Number of provider heartbeat results seen",
        labelnames=("provider_id", "outcome"),
    )
    HB_LAT_MS = Histogram(
        "aicf_heartbeat_latency_ms",
        "Provider heartbeat latency in milliseconds",
        buckets=(50, 100, 150, 200, 250, 350, 500, 750, 1000, 1500, 2500, 5000),
        labelnames=("provider_id",),
    )
    HB_SCORE = Gauge(
        "aicf_provider_health_score",
        "Current provider health score [0,1]",
        labelnames=("provider_id",),
    )
    HB_LASTSEEN = Gauge(
        "aicf_provider_last_seen_ts",
        "Unix timestamp of last heartbeat (seconds)",
        labelnames=("provider_id",),
    )
else:  # pragma: no cover
    HB_PINGS = HB_LAT_MS = HB_SCORE = HB_LASTSEEN = None  # type: ignore[assignment]


# ---- Types ------------------------------------------------------------------


@dataclass
class ProviderHeartbeatState:
    last_seen_ts: float = 0.0
    score: float = 0.0  # [0,1]
    success_ema: float = 0.0  # success rate soft-tracker
    latency_ema_ms: float = 0.0
    last_update_ts: float = 0.0
    consecutive_failures: int = 0
    last_status: str = "UNRESPONSIVE"

    def decay(self, now: float, halflife_s: float) -> None:
        if self.last_update_ts <= 0 or halflife_s <= 0:
            self.last_update_ts = now
            return
        dt = now - self.last_update_ts
        if dt <= 0:
            return
        # Exponential decay toward 0: s *= 0.5 ** (dt/halflife)
        factor = 0.5 ** (dt / halflife_s)
        self.score *= factor
        self.success_ema *= factor
        self.latency_ema_ms *= factor
        self.last_update_ts = now


StatusHook = Callable[[str, str, str], None]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return hi if v > hi else lo if v < lo else v


def _latency_impulse(latency_ms: Optional[float], target: float, tol: float) -> float:
    """
    Map latency to [0,1] where 1 is perfect (0 latency), ~1 near target, and
    tapers to ~0 beyond target + tol.
    """
    if latency_ms is None or not math.isfinite(latency_ms):
        return 0.0
    if latency_ms <= target:
        return 1.0
    over = max(0.0, latency_ms - target)
    if tol <= 0:
        return 0.0
    x = max(0.0, 1.0 - (over / tol))
    return _clamp(x)


# ---- Monitor ----------------------------------------------------------------


class HeartbeatMonitor:
    """
    In-memory heartbeat monitor.

    Parameters
    ----------
    cfg : HeartbeatConfig
        Tuning parameters. Defaults will be used if not provided.
    status_hook : callable(provider_id, new_status, reason)
        Optional hook fired when computed status changes.
    time_fn : callable() -> float
        Source of time (seconds). Defaults to time.time for prod; pass a
        deterministic stub in tests.
    """

    def __init__(
        self,
        cfg: Optional[HeartbeatConfig] = None,
        *,
        status_hook: Optional[StatusHook] = None,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.cfg = cfg or _cfg_from_env or HeartbeatConfig()
        self._state: Dict[str, ProviderHeartbeatState] = {}
        self._status_hook = status_hook
        self._now = time_fn

    # ---- Public API ----------------------------------------------------------

    def record_ping(
        self,
        provider_id: str,
        *,
        ok: bool,
        latency_ms: Optional[float] = None,
        now: Optional[float] = None,
    ) -> ProviderHeartbeatState:
        """
        Record a heartbeat result and update the provider's health score.

        Returns the updated ProviderHeartbeatState.
        """
        t = self._now() if now is None else now
        st = self._state.get(provider_id)
        if st is None:
            st = ProviderHeartbeatState()
            self._state[provider_id] = st

        # Decay existing state
        st.decay(t, self.cfg.halflife_s)

        # Metrics
        if HB_PINGS is not None:  # pragma: no cover - trivial path
            HB_PINGS.labels(
                provider_id=provider_id, outcome=("ok" if ok else "fail")
            ).inc()
            if ok and latency_ms is not None and math.isfinite(latency_ms):
                HB_LAT_MS.labels(provider_id=provider_id).observe(latency_ms)

        # Compute impulse from this ping
        if ok:
            st.last_seen_ts = t
            st.consecutive_failures = 0

            # Success rate moves toward 1 with exponential smoothing via decay mechanics.
            # We add a small impulse toward 1 on success.
            st.success_ema = _clamp(st.success_ema + (1.0 - st.success_ema) * 0.6)

            # Latency EMA: push toward observed latency
            if latency_ms is not None and math.isfinite(latency_ms):
                if st.latency_ema_ms <= 0:
                    st.latency_ema_ms = float(latency_ms)
                else:
                    st.latency_ema_ms += (latency_ms - st.latency_ema_ms) * 0.6

            # Health impulse blends success (1.0) with latency mapping
            imp_lat = _latency_impulse(
                latency_ms, self.cfg.latency_target_ms, self.cfg.latency_tolerance_ms
            )
            impulse = 0.5 * 1.0 + 0.5 * imp_lat  # in [0,1]

            # Move score upward proportionally to remaining headroom
            delta = self.cfg.asc_rate * (1.0 - st.score) * impulse
            st.score = _clamp(st.score + delta)

        else:
            st.consecutive_failures += 1
            st.success_ema *= 0.5  # dampen optimism faster on failures

            # Penalize score, increasing with consecutive failures
            penalty = (
                self.cfg.fail_penalty_base
                + self.cfg.fail_penalty_per_consecutive * (st.consecutive_failures - 1)
            )
            penalty = min(self.cfg.fail_penalty_cap, max(0.0, penalty))
            st.score = _clamp(st.score * (1.0 - penalty))

        # Update derived status and maybe fire hook
        new_status, reason = self._derive_status(st, t)
        if new_status != st.last_status:
            if self._status_hook:
                try:
                    self._status_hook(provider_id, new_status, reason)
                except Exception:  # pragma: no cover - defensive
                    log.exception("status_hook failed for provider_id=%s", provider_id)
            st.last_status = new_status

        # Export gauges
        if HB_SCORE is not None:  # pragma: no cover - trivial path
            HB_SCORE.labels(provider_id=provider_id).set(st.score)
        if HB_LASTSEEN is not None:  # pragma: no cover
            HB_LASTSEEN.labels(provider_id=provider_id).set(st.last_seen_ts)

        return st

    def current_status(self, provider_id: str) -> Tuple[str, float, float]:
        """
        Returns (status, score, last_seen_ts).
        """
        t = self._now()
        st = self._state.get(provider_id, ProviderHeartbeatState())
        # Decay on read so status reflects passage of time even without pings.
        st.decay(t, self.cfg.halflife_s)
        status, _ = self._derive_status(st, t)
        return status, st.score, st.last_seen_ts

    def get_state(self, provider_id: str) -> ProviderHeartbeatState:
        """Return the raw heartbeat state (decayed to 'now')."""
        t = self._now()
        st = self._state.get(provider_id)
        if st is None:
            st = ProviderHeartbeatState()
            self._state[provider_id] = st
        st.decay(t, self.cfg.halflife_s)
        return st

    def tick_all(self) -> None:
        """
        Decay all providers and emit status changes if thresholds crossed due to time.
        Call periodically if you want status to reflect staleness without new pings.
        """
        t = self._now()
        for pid, st in self._state.items():
            before_status = st.last_status
            st.decay(t, self.cfg.halflife_s)
            new_status, reason = self._derive_status(st, t)
            if new_status != before_status:
                if self._status_hook:
                    try:
                        self._status_hook(pid, new_status, reason)
                    except Exception:  # pragma: no cover
                        log.exception("status_hook failed for provider_id=%s", pid)
                st.last_status = new_status
            if HB_SCORE is not None:  # pragma: no cover
                HB_SCORE.labels(provider_id=pid).set(st.score)

    # ---- Internals -----------------------------------------------------------

    def _derive_status(self, st: ProviderHeartbeatState, now: float) -> Tuple[str, str]:
        """
        Compute status string and brief reason.
        """
        # Staleness wins first: if we haven't seen a success for too long, mark down.
        if st.last_seen_ts <= 0 or (now - st.last_seen_ts) > self.cfg.stale_timeout_s:
            if (
                st.consecutive_failures >= self.cfg.max_consecutive_fail_for_down
                or st.score <= self.cfg.down_threshold
            ):
                return "UNRESPONSIVE", "no recent success; stale beyond timeout"
            # stale but not fully down → degraded if score is lowish
            if st.score < self.cfg.degrade_threshold:
                return "DEGRADED", "stale and low score"
            # if score still good, call it degraded to be conservative
            return "DEGRADED", "stale but acceptable score"

        # Not stale: use thresholds
        if (
            st.score <= self.cfg.down_threshold
            or st.consecutive_failures >= self.cfg.max_consecutive_fail_for_down
        ):
            return "UNRESPONSIVE", "score below down threshold or too many failures"
        if st.score <= self.cfg.degrade_threshold:
            return "DEGRADED", "score below degrade threshold"
        return "HEALTHY", "score within healthy range"


# ---- Optional: simple HTTP probe helper -------------------------------------


async def http_probe(
    endpoint: str, timeout_s: float = 3.0
) -> Tuple[bool, Optional[float]]:
    """
    Best-effort async HTTP liveness probe for a provider endpoint.

    Returns (ok, latency_ms). This helper intentionally avoids hard deps; it
    will raise RuntimeError if httpx is not available.
    """
    try:
        import httpx  # noqa: WPS433 (runtime import)
    except Exception as e:  # pragma: no cover
        raise RuntimeError("http_probe requires 'httpx' to be installed") from e

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(endpoint)
            ok = 200 <= r.status_code < 300
    except Exception:
        return False, None
    finally:
        latency_ms = (time.perf_counter() - start) * 1000.0
    return ok, latency_ms
