"""
capabilities.metrics
--------------------

Prometheus metrics for the Animica capabilities subsystem.

Tracks:
- Enqueue attempts (accepted/rejected) per kind (AI / Quantum / Blob / ZK / Random / Treasury).
- read_result hits/misses.
- zk.verify counts & latency.
- Blob I/O bytes (pin/get).

The module is safe to import even if `prometheus_client` is not installed: it
falls back to no-op shims so production code can run without optional deps.
"""

from __future__ import annotations

import os
import time
import logging
from contextlib import contextmanager
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# --- Optional Prometheus dependency -------------------------------------------------

try:
    from prometheus_client import Counter, Histogram, Gauge, start_http_server  # type: ignore
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when prometheus_client is absent
    _PROM_AVAILABLE = False

    class _NoopMetric:  # minimal shim
        def __init__(self, *_, **__): ...
        def labels(self, *_, **__): return self
        def inc(self, *_: float): ...
        def observe(self, *_: float): ...
        def set(self, *_: float): ...

    def Counter(*_, **__):  # type: ignore
        return _NoopMetric()

    def Histogram(*_, **__):  # type: ignore
        return _NoopMetric()

    def Gauge(*_, **__):  # type: ignore
        return _NoopMetric()

    def start_http_server(*_, **__):  # type: ignore
        log.info("prometheus_client not available; metrics HTTP server disabled")

# --- Namespacing --------------------------------------------------------------------

_NS = "animica"
_SUB = "capabilities"

def _m(name: str) -> str:
    return f"{_NS}_{_SUB}_{name}"

# --- Metric declarations ------------------------------------------------------------

# Counters
ENQUEUE_TOTAL = Counter(
    _m("enqueue_total"),
    "Jobs enqueued by kind.",
    labelnames=("kind",),
)

ENQUEUE_REJECTED_TOTAL = Counter(
    _m("enqueue_rejected_total"),
    "Jobs rejected at enqueue (determinism/limits/auth/other).",
    labelnames=("reason", "kind"),
)

READ_RESULT_TOTAL = Counter(
    _m("read_result_total"),
    "read_result outcomes per kind.",
    labelnames=("outcome", "kind"),  # outcome = hit|miss
)

ZK_VERIFY_TOTAL = Counter(
    _m("zk_verify_total"),
    "zk.verify attempts by scheme and verdict.",
    labelnames=("scheme", "verdict"),  # verdict = ok|fail
)

BLOB_BYTES_TOTAL = Counter(
    _m("blob_bytes_total"),
    "Total blob bytes processed in capability host (direction=in|out).",
    labelnames=("direction",),
)

# Histograms (latencies)
# Use conservative, low-cardinality buckets (seconds).
_LAT_BUCKETS_FAST = (
    0.001, 0.003, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0
)

ENQUEUE_LATENCY_SECONDS = Histogram(
    _m("enqueue_latency_seconds"),
    "Latency to accept and persist a job.",
    buckets=_LAT_BUCKETS_FAST,
    labelnames=("kind",),
)

READ_RESULT_LATENCY_SECONDS = Histogram(
    _m("read_result_latency_seconds"),
    "Latency to look up a deterministic result record.",
    buckets=_LAT_BUCKETS_FAST,
    labelnames=("kind",),
)

ZK_VERIFY_SECONDS = Histogram(
    _m("zk_verify_seconds"),
    "Latency to verify a ZK proof.",
    buckets=_LAT_BUCKETS_FAST + (10.0, 20.0, 30.0),
    labelnames=("scheme",),
)

# Gauges (lightweight, optional)
QUEUE_DEPTH = Gauge(
    _m("queue_depth"),
    "Approximate items in the capabilities queue.",
    labelnames=("kind",),
)

INFLIGHT_JOBS = Gauge(
    _m("inflight_jobs"),
    "Currently leased/running jobs.",
    labelnames=("kind",),
)

# --- Helper API ---------------------------------------------------------------------

def record_enqueue(kind: str, *, accepted: bool, reason: str | None = None, latency_s: float | None = None) -> None:
    """
    Record an enqueue attempt.

    Parameters
    ----------
    kind : str
        'ai' | 'quantum' | 'blob' | 'zk' | 'random' | 'treasury' (free-form but keep low cardinality).
    accepted : bool
        Whether the job was accepted.
    reason : str | None
        If rejected, a short stable code ('not_deterministic', 'limit', 'auth', 'schema', 'other').
    latency_s : float | None
        Optional latency in seconds to observe.
    """
    if accepted:
        ENQUEUE_TOTAL.labels(kind=kind).inc()
    else:
        ENQUEUE_REJECTED_TOTAL.labels(reason=(reason or "other"), kind=kind).inc()
    if latency_s is not None:
        ENQUEUE_LATENCY_SECONDS.labels(kind=kind).observe(max(0.0, float(latency_s)))


def record_read_result(kind: str, *, hit: bool, latency_s: float | None = None) -> None:
    """
    Record a read_result() attempt and outcome.
    """
    READ_RESULT_TOTAL.labels(outcome=("hit" if hit else "miss"), kind=kind).inc()
    if latency_s is not None:
        READ_RESULT_LATENCY_SECONDS.labels(kind=kind).observe(max(0.0, float(latency_s)))


def record_zk_verify(scheme: str, *, ok: bool, latency_s: float | None = None) -> None:
    """
    Record a zk.verify attempt for a given scheme (keep scheme names low-cardinality).
    """
    ZK_VERIFY_TOTAL.labels(scheme=scheme, verdict=("ok" if ok else "fail")).inc()
    if latency_s is not None:
        ZK_VERIFY_SECONDS.labels(scheme=scheme).observe(max(0.0, float(latency_s)))


def add_blob_bytes(n: int, *, direction: str) -> None:
    """
    Accumulate blob byte counts.

    Parameters
    ----------
    n : int
        Number of bytes processed (will be clamped to >=0).
    direction : str
        'in' for pin/enqueue inputs, 'out' for reads/gets.
    """
    BLOB_BYTES_TOTAL.labels(direction=("in" if direction != "out" else "out")).inc(max(0, int(n)))


@contextmanager
def time_observe(histogram: Histogram, **label_kwargs):
    """
    Context manager to time a block and observe its duration in a histogram.

    Example:
        with time_observe(ENQUEUE_LATENCY_SECONDS, kind="ai"):
            do_work()
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        try:
            histogram.labels(**label_kwargs).observe(dt)
        except Exception:  # pragma: no cover - defensive
            pass


_server_started = False

def ensure_metrics_server(port: int | None = None, addr: str = "0.0.0.0") -> bool:
    """
    Optionally start a Prometheus /metrics HTTP server (idempotent).

    The port can be configured by:
    - argument `port`
    - env var ANIMICA_METRICS_PORT
    - default: 0 (disabled) if prometheus_client is missing; otherwise 9109.

    Returns True if a server is (or was already) running, False otherwise.
    """
    global _server_started

    if _server_started:
        return True

    # Resolve port preference
    env_port = os.getenv("ANIMICA_METRICS_PORT")
    chosen_port: Optional[int]
    if port is not None:
        chosen_port = int(port)
    elif env_port is not None and env_port.strip():
        try:
            chosen_port = int(env_port)
        except ValueError:
            log.warning("Invalid ANIMICA_METRICS_PORT=%r; metrics server disabled", env_port)
            chosen_port = None
    else:
        chosen_port = 9109 if _PROM_AVAILABLE else None

    if not _PROM_AVAILABLE or not chosen_port or chosen_port <= 0:
        if not _PROM_AVAILABLE:
            log.info("prometheus_client not installed; skipping metrics server")
        return False

    try:
        start_http_server(chosen_port, addr=addr)  # type: ignore[call-arg]
        _server_started = True
        log.info("Metrics server started on http://%s:%d/metrics", addr, chosen_port)
        return True
    except Exception as e:  # pragma: no cover - environment dependent
        log.warning("Failed to start metrics server: %s", e)
        return False


__all__ = [
    # counters
    "ENQUEUE_TOTAL",
    "ENQUEUE_REJECTED_TOTAL",
    "READ_RESULT_TOTAL",
    "ZK_VERIFY_TOTAL",
    "BLOB_BYTES_TOTAL",
    # histograms
    "ENQUEUE_LATENCY_SECONDS",
    "READ_RESULT_LATENCY_SECONDS",
    "ZK_VERIFY_SECONDS",
    # gauges
    "QUEUE_DEPTH",
    "INFLIGHT_JOBS",
    # helpers
    "record_enqueue",
    "record_read_result",
    "record_zk_verify",
    "add_blob_bytes",
    "time_observe",
    "ensure_metrics_server",
]
