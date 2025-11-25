from __future__ import annotations
"""
aicf.queue.dispatcher
=====================

Main scheduling loop that continuously tries to match eligible providers
to queued jobs using the AssignmentEngine. It also sweeps expired leases
and requeues/tombstones jobs as needed.

Design
------
- Single-threaded loop with cooperative sleep; safe to run one per process.
- Uses AssignmentEngine for all atomic state transitions.
- Idle/backoff sleep when nothing happens; faster ticks under load.
- Soft Prometheus metrics imports (module works without metrics installed).

Typical usage
-------------
    engine = AssignmentEngine(storage, registry, quotas, config=AssignmentConfig(...))
    disp = Dispatcher(engine, DispatcherConfig())
    stop = threading.Event()
    disp.run_forever(stop)  # blocks until stop.set()

"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import random
import threading
import time
from typing import Optional

from .assignment import AssignmentEngine

log = logging.getLogger(__name__)

# ────────────────────────────── Optional metrics ──────────────────────────────
try:
    # These names are optional; metrics module may provide them.
    from aicf.metrics import (  # type: ignore
        HISTOGRAM_DISPATCH_TICK_SECONDS as _H_TICK,
        GAUGE_QUEUE_DEPTH as _G_QDEPTH,
        GAUGE_ACTIVE_LEASES as _G_ACTIVE,
        COUNTER_DISPATCH_TICKS as _C_TICKS,
        COUNTER_DISPATCH_IDLE as _C_IDLE,
    )
except Exception:  # pragma: no cover - soft import fallback

    class _Noop:
        def labels(self, *_, **__):  # type: ignore
            return self

        def observe(self, *_: float):  # type: ignore
            return None

        def set(self, *_: float):  # type: ignore
            return None

        def inc(self, *_: float):  # type: ignore
            return None

    _H_TICK = _Noop()
    _G_QDEPTH = _Noop()
    _G_ACTIVE = _Noop()
    _C_TICKS = _Noop()
    _C_IDLE = _Noop()


# ──────────────────────────────── Config model ────────────────────────────────


@dataclass
class DispatcherConfig:
    """
    Controls pacing and behavior of the dispatcher loop.

    tick_interval_seconds:
        Target period between ticks under steady load.
    idle_sleep_seconds:
        Sleep duration when no assignments or sweeps occurred.
    jitter_fraction:
        +/- random jitter applied to sleep to avoid thundering herds.
    sweep_every_ticks:
        Run a lease-expiry sweep once every N ticks (>=1).
    """

    tick_interval_seconds: float = 0.5
    idle_sleep_seconds: float = 1.5
    jitter_fraction: float = 0.15
    sweep_every_ticks: int = 2


# ───────────────────────────────── Dispatcher ─────────────────────────────────


class Dispatcher:
    """
    Main loop driver for the scheduler. It is intentionally thin and delegates
    all state changes to AssignmentEngine.
    """

    def __init__(self, engine: AssignmentEngine, config: Optional[DispatcherConfig] = None) -> None:
        self.engine = engine
        self.config = config or DispatcherConfig()
        self._tick_count = 0
        self._lock = threading.Lock()

    # Public API ---------------------------------------------------------------

    def run_forever(self, stop_event: Optional[threading.Event] = None) -> None:
        """
        Block and run the dispatcher loop until `stop_event` is set (if provided).
        """
        stop = stop_event or threading.Event()
        log.info("dispatcher: starting main loop (tick=%.3fs idle=%.3fs)",
                 self.config.tick_interval_seconds, self.config.idle_sleep_seconds)

        while not stop.is_set():
            started = _now()
            assigned, requeued, tombstoned, swept = self._tick_internal(started)

            # pacing
            duration = (_now() - started).total_seconds()
            _H_TICK.observe(duration)  # type: ignore
            _C_TICKS.inc(1)  # type: ignore

            if assigned == 0 and requeued == 0 and tombstoned == 0 and swept == 0:
                _C_IDLE.inc(1)  # type: ignore
                self._sleep_with_jitter(self.config.idle_sleep_seconds, stop)
            else:
                # Maintain near target tick interval if work was done quickly.
                remaining = max(0.0, self.config.tick_interval_seconds - duration)
                if remaining > 0:
                    self._sleep_with_jitter(remaining, stop)

        log.info("dispatcher: stopped")

    def run_once(self, now: Optional[datetime] = None) -> tuple[int, int, int, int]:
        """
        Execute a single tick. Returns a tuple:
        (assigned, requeued, tombstoned, swept).
        """
        return self._tick_internal(now or _now())

    # Internal -----------------------------------------------------------------

    def _tick_internal(self, now: datetime) -> tuple[int, int, int, int]:
        with self._lock:
            self._tick_count += 1
            # Optionally sweep expired leases
            swept_requeued = swept_tomb = 0
            if self._tick_count % max(1, self.config.sweep_every_ticks) == 0:
                swept_requeued, swept_tomb = self.engine.sweep_expired(now=now)
                if swept_requeued or swept_tomb:
                    log.debug("dispatcher: sweep_expired requeued=%d tombstoned=%d",
                              swept_requeued, swept_tomb)

            assigned = self.engine.assign_pass(now=now)

            # Best-effort gauges if storage exposes counts
            self._maybe_update_gauges()

            if assigned or swept_requeued or swept_tomb:
                log.debug("dispatcher: tick assigned=%d requeued=%d tombstoned=%d",
                          assigned, swept_requeued, swept_tomb)
            return assigned, swept_requeued, swept_tomb, int(bool(swept_requeued or swept_tomb))

    def _maybe_update_gauges(self) -> None:
        """
        If the underlying storage exposes queue/lease counts, export them as gauges.
        This is optional to avoid coupling protocols to metrics.
        """
        storage = self.engine.storage  # type: ignore[attr-defined]

        qdepth = None
        active = None

        # queue_depth()
        if hasattr(storage, "queue_depth"):
            try:
                qdepth = int(storage.queue_depth())  # type: ignore[attr-defined]
            except Exception:
                qdepth = None

        # active_leases_count()
        if hasattr(storage, "active_leases_count"):
            try:
                active = int(storage.active_leases_count())  # type: ignore[attr-defined]
            except Exception:
                active = None

        if qdepth is not None:
            _G_QDEPTH.set(float(qdepth))  # type: ignore
        if active is not None:
            _G_ACTIVE.set(float(active))  # type: ignore

    def _sleep_with_jitter(self, seconds: float, stop: threading.Event) -> None:
        if seconds <= 0:
            return
        frac = self.config.jitter_fraction
        jitter = seconds * frac * (2 * random.random() - 1.0)  # +/- frac
        delay = max(0.0, seconds + jitter)
        stop.wait(delay)


# ────────────────────────────── Helper utilities ──────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["Dispatcher", "DispatcherConfig"]
