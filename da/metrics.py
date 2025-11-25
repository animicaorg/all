"""
Prometheus metrics for Animica Data Availability (DA).

This module centralizes counters, gauges, and histograms for:
- POST/GET request counts, in-flight, durations
- Bytes in/out (rates are computed in Prometheus via rate()/irate())
- Blob sizes on POST
- Proof verification timings & outcomes
- DAS sampler activity, latencies, and outcomes
- Simple cache hit/miss counters (for DA proof/cache layers)

Design goals:
- Zero-cost fallback when `prometheus_client` isn't installed (no-op metrics)
- Small, dependency-free surface for DA components to import and use
- Optional ASGI `/metrics` app factory for FastAPI/Starlette mounting

Typical usage (in a FastAPI route):

    from da.metrics import get_metrics

    METRICS = get_metrics()

    @router.post("/da/blob")
    async def post_blob(request: Request):
        with METRICS.time_request(method="POST", endpoint="/da/blob") as obs:
            body = await request.body()
            # ... handle upload ...
            obs.set_result(status_code=200, bytes_in=len(body))
            return {"ok": True}

For long-running work you can nest additional timers:

    with METRICS.time_proof_verify(endpoint="/da/proof") as t:
        verify_proof(...)
        t.ok()  # or t.fail("invalid_proof")

To expose `/metrics`:

    from fastapi import FastAPI
    from da.metrics import make_asgi_metrics_app, get_metrics

    app = FastAPI()
    app.mount("/metrics", make_asgi_metrics_app() or FastAPI())  # no-op if missing

"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional

# ----------------------------- optional import -------------------------------

try:
    # prometheus_client is an optional dependency. We provide graceful no-op fallback.
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        Summary,
        REGISTRY as _DEFAULT_REGISTRY,
    )
    try:
        # Newer prometheus_client offers ASGI/Wsgi helpers
        from prometheus_client import make_asgi_app as _make_asgi_app  # type: ignore
    except Exception:  # pragma: no cover - optional
        _make_asgi_app = None  # type: ignore
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - prometheus not installed
    CollectorRegistry = object  # type: ignore
    Counter = Gauge = Histogram = Summary = object  # type: ignore
    _DEFAULT_REGISTRY = None  # type: ignore
    _make_asgi_app = None  # type: ignore
    _PROM_AVAILABLE = False


# ------------------------------- noop metrics --------------------------------


class _NoopCounter:
    def labels(self, *args: str, **kwargs: str) -> "_NoopCounter":
        return self

    def inc(self, amount: float = 1.0) -> None:
        return

    def observe(self, value: float) -> None:
        return


class _NoopGauge(_NoopCounter):
    def set(self, value: float) -> None:
        return

    def inc(self, amount: float = 1.0) -> None:
        return

    def dec(self, amount: float = 1.0) -> None:
        return


class _NoopHistogram(_NoopCounter):
    def time(self):  # pragma: no cover - trivial noop
        @contextmanager
        def _cm():
            yield

        return _cm()


# ------------------------------- core metrics --------------------------------


@dataclass(frozen=True)
class _Labels:
    """Canonical label keys used across metrics."""
    method: str = "method"
    endpoint: str = "endpoint"
    status: str = "status"
    outcome: str = "outcome"
    kind: str = "kind"
    cache: str = "cache"


def _registry_or_default(registry: Optional["CollectorRegistry"]) -> Optional["CollectorRegistry"]:
    if not _PROM_AVAILABLE:
        return None
    return registry or _DEFAULT_REGISTRY


class DAMetrics:
    """
    Concrete metrics backed by prometheus_client.
    """

    def __init__(self, registry: Optional["CollectorRegistry"] = None) -> None:
        self._labels = _Labels()
        reg = _registry_or_default(registry)

        if _PROM_AVAILABLE:
            # Request-level metrics
            self.requests_total = Counter(
                "da_requests_total",
                "Total DA HTTP requests",
                [self._labels.method, self._labels.endpoint, self._labels.status],
                registry=reg,
            )
            self.inflight = Gauge(
                "da_requests_inflight",
                "In-flight DA HTTP requests",
                [self._labels.endpoint],
                registry=reg,
            )
            self.request_duration = Histogram(
                "da_request_duration_seconds",
                "DA HTTP request duration in seconds",
                [self._labels.method, self._labels.endpoint],
                registry=reg,
                buckets=(
                    0.005,
                    0.01,
                    0.025,
                    0.05,
                    0.1,
                    0.25,
                    0.5,
                    1.0,
                    2.5,
                    5.0,
                    10.0,
                ),
            )
            self.bytes_total = Counter(
                "da_bytes_total",
                "Total bytes in/out grouped by endpoint and direction",
                [self._labels.endpoint, "direction"],
                registry=reg,
            )
            self.post_blobs_total = Counter(
                "da_post_blobs_total",
                "Total POSTed blobs accepted",
                [self._labels.status],
                registry=reg,
            )
            self.get_blobs_total = Counter(
                "da_get_blobs_total",
                "Total GET /da/blob responses",
                [self._labels.status],
                registry=reg,
            )
            self.get_proofs_total = Counter(
                "da_get_proofs_total",
                "Total GET /da/proof responses",
                [self._labels.status],
                registry=reg,
            )
            self.post_blob_size = Histogram(
                "da_post_blob_size_bytes",
                "Distribution of posted blob sizes (bytes)",
                registry=reg,
                buckets=(
                    1_024,
                    2_048,
                    4_096,
                    8_192,
                    16_384,
                    32_768,
                    65_536,
                    131_072,
                    262_144,
                    524_288,
                    1_048_576,
                    2_097_152,
                    4_194_304,
                    8_388_608,
                    16_777_216,
                ),
            )

            # Proof verification
            self.proof_verify_total = Counter(
                "da_proof_verify_total",
                "Total proof verification attempts",
                [self._labels.outcome],
                registry=reg,
            )
            self.proof_verify_duration = Histogram(
                "da_proof_verify_duration_seconds",
                "Proof verification duration (seconds)",
                registry=reg,
                buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
            )

            # DAS sampling
            self.sampler_active_jobs = Gauge(
                "da_sampler_active_jobs",
                "Number of active DAS sampling jobs",
                registry=reg,
            )
            self.sampler_samples_total = Counter(
                "da_sampler_samples_total",
                "Total DAS samples performed",
                [self._labels.outcome],
                registry=reg,
            )
            self.sampler_sample_latency = Histogram(
                "da_sampler_sample_latency_seconds",
                "Latency per DAS sample (seconds)",
                registry=reg,
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
            )

            # Cache (proofs/shards)
            self.cache_events_total = Counter(
                "da_cache_events_total",
                "DA cache events (hits/misses/evictions) grouped by kind",
                [self._labels.cache, self._labels.kind],
                registry=reg,
            )
        else:  # No prometheus -> set no-op instruments
            self.requests_total = _NoopCounter()
            self.inflight = _NoopGauge()
            self.request_duration = _NoopHistogram()
            self.bytes_total = _NoopCounter()
            self.post_blobs_total = _NoopCounter()
            self.get_blobs_total = _NoopCounter()
            self.get_proofs_total = _NoopCounter()
            self.post_blob_size = _NoopHistogram()
            self.proof_verify_total = _NoopCounter()
            self.proof_verify_duration = _NoopHistogram()
            self.sampler_active_jobs = _NoopGauge()
            self.sampler_samples_total = _NoopCounter()
            self.sampler_sample_latency = _NoopHistogram()
            self.cache_events_total = _NoopCounter()

    # ---------------------------- request helpers ----------------------------

    @contextmanager
    def time_request(self, *, method: str, endpoint: str):
        """
        Context manager to record inflight, duration, bytes, and status.

        Usage:
            with METRICS.time_request(method="POST", endpoint="/da/blob") as obs:
                # ... work ...
                obs.set_result(status_code=200, bytes_in=n_in, bytes_out=n_out)
        """
        start = time.perf_counter()
        try:
            self.inflight.labels(endpoint).inc()  # type: ignore[attr-defined]
        except Exception:
            pass

        class _Observer:
            _status_code: Optional[int] = None
            _bytes_in: int = 0
            _bytes_out: int = 0

            def set_result(self, *, status_code: int, bytes_in: int = 0, bytes_out: int = 0) -> None:
                self._status_code = status_code
                self._bytes_in = max(0, int(bytes_in or 0))
                self._bytes_out = max(0, int(bytes_out or 0))

        obs = _Observer()
        try:
            yield obs
        finally:
            dur = max(0.0, time.perf_counter() - start)
            status = str(obs._status_code or 500)
            # Counters
            try:
                self.requests_total.labels(method, endpoint, status).inc()  # type: ignore[attr-defined]
                self.request_duration.labels(method, endpoint).observe(dur)  # type: ignore[attr-defined]
                if obs._bytes_in:
                    self.bytes_total.labels(endpoint, "in").inc(obs._bytes_in)  # type: ignore[attr-defined]
                if obs._bytes_out:
                    self.bytes_total.labels(endpoint, "out").inc(obs._bytes_out)  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                self.inflight.labels(endpoint).dec()  # type: ignore[attr-defined]
            except Exception:
                pass

    def note_post_blob(self, *, status_code: int, size_bytes: int) -> None:
        try:
            self.post_blobs_total.labels(str(status_code)).inc()  # type: ignore[attr-defined]
            if size_bytes > 0:
                self.post_blob_size.observe(float(size_bytes))  # type: ignore[attr-defined]
        except Exception:
            pass

    def note_get_blob(self, *, status_code: int, bytes_out: int) -> None:
        try:
            self.get_blobs_total.labels(str(status_code)).inc()  # type: ignore[attr-defined]
            if bytes_out > 0:
                self.bytes_total.labels("/da/blob", "out").inc(bytes_out)  # type: ignore[attr-defined]
        except Exception:
            pass

    def note_get_proof(self, *, status_code: int) -> None:
        try:
            self.get_proofs_total.labels(str(status_code)).inc()  # type: ignore[attr-defined]
        except Exception:
            pass

    # --------------------------- proof verify timer --------------------------

    @contextmanager
    def time_proof_verify(self, *, endpoint: str = "/da/proof"):
        """
        Context manager for proof verification steps.

        Usage:
            with METRICS.time_proof_verify() as t:
                if verify(...):
                    t.ok()
                else:
                    t.fail("invalid")
        """
        start = time.perf_counter()
        outcome_ref = {"v": "ok"}

        class _Mark:
            def ok(self) -> None:
                outcome_ref["v"] = "ok"

            def fail(self, outcome: str = "error") -> None:
                outcome_ref["v"] = outcome

        marker = _Mark()
        try:
            yield marker
            # if user didn't call ok()/fail(), assume ok
        finally:
            dur = max(0.0, time.perf_counter() - start)
            try:
                self.proof_verify_total.labels(outcome_ref["v"]).inc()  # type: ignore[attr-defined]
                self.proof_verify_duration.observe(dur)  # type: ignore[attr-defined]
            except Exception:
                pass

    # ----------------------------- sampler hooks -----------------------------

    @contextmanager
    def sampler_job(self):
        """
        Track active DAS sampling jobs. Use once per blob/window sampling session.
        """
        try:
            self.sampler_active_jobs.inc()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                self.sampler_active_jobs.dec()  # type: ignore[attr-defined]
            except Exception:
                pass

    def note_sample(self, *, outcome: str, latency_seconds: float) -> None:
        """
        Record a single DAS sample. Outcome suggestions:
            "ok", "timeout", "not_found", "invalid_proof", "error"
        """
        try:
            self.sampler_samples_total.labels(outcome).inc()  # type: ignore[attr-defined]
            self.sampler_sample_latency.observe(max(0.0, float(latency_seconds)))  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------- cache notes -----------------------------

    def cache_hit(self, *, kind: str) -> None:
        try:
            self.cache_events_total.labels("hit", kind).inc()  # type: ignore[attr-defined]
        except Exception:
            pass

    def cache_miss(self, *, kind: str) -> None:
        try:
            self.cache_events_total.labels("miss", kind).inc()  # type: ignore[attr-defined]
        except Exception:
            pass

    def cache_evict(self, *, kind: str) -> None:
        try:
            self.cache_events_total.labels("evict", kind).inc()  # type: ignore[attr-defined]
        except Exception:
            pass


class _NoopDAMetrics(DAMetrics):  # pragma: no cover - trivial
    """
    A no-op implementation used when prometheus_client is unavailable.
    """
    def __init__(self, registry: Optional["CollectorRegistry"] = None) -> None:
        super().__init__(registry=None)


# ------------------------------- public API ----------------------------------

_METRICS_SINGLETON: Optional[DAMetrics] = None


def get_metrics(registry: Optional["CollectorRegistry"] = None) -> DAMetrics:
    """
    Return a process-wide DAMetrics singleton. The first call can inject a custom
    registry; subsequent calls ignore the registry parameter.
    """
    global _METRICS_SINGLETON
    if _METRICS_SINGLETON is not None:
        return _METRICS_SINGLETON
    if _PROM_AVAILABLE:
        _METRICS_SINGLETON = DAMetrics(registry=registry)
    else:
        _METRICS_SINGLETON = _NoopDAMetrics()
    return _METRICS_SINGLETON


def make_asgi_metrics_app(registry: Optional["CollectorRegistry"] = None):
    """
    Return an ASGI app exposing /metrics if supported by prometheus_client,
    otherwise return None. You can then mount it as:

        app.mount("/metrics", make_asgi_metrics_app() or empty_app)

    where `empty_app` is a no-op ASGI app (or omit mounting entirely).
    """
    if not _PROM_AVAILABLE or _make_asgi_app is None:  # type: ignore[truthy-function]
        return None
    reg = _registry_or_default(registry)
    return _make_asgi_app(registry=reg)  # type: ignore[misc]


__all__ = [
    "DAMetrics",
    "get_metrics",
    "make_asgi_metrics_app",
]
