from __future__ import annotations

"""
Mining metrics for Prometheus.

- MINER_SCANNER_HASHRATE_ABS (Gauge): abstract shares/second reported by the scanner.
- MINER_FOUND_SHARES         (Counter): shares discovered locally.
- MINER_SUBMIT_OK            (Counter): accepted submissions.
- MINER_SUBMIT_REJECT        (Counter): rejected submissions.
- MINER_SUBMIT_LATENCY_SEC   (Histogram): end-to-end submit latency.
- MINER_ACTIVE_TEMPLATE_AGE_SEC (Gauge): age of the currently active template (seconds).

This module is *dependency-safe*: if `prometheus_client` (or Starlette) is not
available, it falls back to no-op stubs so importing code never crashes.
"""

import os
from typing import Optional

# --------------------------- Robust import with graceful fallback ---------------------------

try:
    from prometheus_client import (CONTENT_TYPE_LATEST, REGISTRY, Counter,
                                   Gauge, Histogram, generate_latest,
                                   start_http_server)

    _PROM_OK = True
except Exception:  # pragma: no cover
    _PROM_OK = False

    class _Noop:
        def __init__(self, *a, **k): ...
        def inc(self, *a, **k): ...
        def observe(self, *a, **k): ...
        def set(self, *a, **k): ...

    Counter = Gauge = Histogram = _Noop  # type: ignore
    REGISTRY = None  # type: ignore

    def generate_latest(*_a, **_k) -> bytes:  # type: ignore
        return b""

    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"  # type: ignore

    def start_http_server(*_a, **_k):  # type: ignore
        return None


# --------------------------- Metric objects ---------------------------

# Gauges
MINER_SCANNER_HASHRATE_ABS = Gauge(
    "animica_miner_hashrate_abs",
    "Abstract shares per second reported by the scanner.",
)
MINER_ACTIVE_TEMPLATE_AGE_SEC = Gauge(
    "animica_miner_active_template_age_seconds",
    "Age in seconds of the current mining template.",
)

# Counters
MINER_FOUND_SHARES = Counter(
    "animica_miner_found_shares_total",
    "Total shares found by the local scanner.",
)
MINER_SUBMIT_OK = Counter(
    "animica_miner_submit_ok_total",
    "Total successfully accepted share submissions.",
)
MINER_SUBMIT_REJECT = Counter(
    "animica_miner_submit_reject_total",
    "Total rejected share submissions.",
)

# Histograms (latency buckets tuned for LAN/local node; widen if needed)
MINER_SUBMIT_LATENCY_SEC = Histogram(
    "animica_miner_submit_latency_seconds",
    "Latency of share/solution submission to the node.",
    buckets=(0.02, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
)

__all__ = [
    "MINER_SCANNER_HASHRATE_ABS",
    "MINER_ACTIVE_TEMPLATE_AGE_SEC",
    "MINER_FOUND_SHARES",
    "MINER_SUBMIT_OK",
    "MINER_SUBMIT_REJECT",
    "MINER_SUBMIT_LATENCY_SEC",
    "maybe_start_http_endpoint",
    "build_asgi_metrics_app",
    "report_hashrate",
]


# --------------------------- Convenience helpers ---------------------------


def report_hashrate(shares_per_sec: float) -> None:
    """
    Update the hashrate gauge. Accepts any non-negative float; values are not persisted.
    """
    try:
        if shares_per_sec < 0:
            shares_per_sec = 0.0
        MINER_SCANNER_HASHRATE_ABS.set(shares_per_sec)
    except Exception:
        # stay silent in fallback mode
        pass


def maybe_start_http_endpoint(
    port: Optional[int] = None,
    addr: str = "0.0.0.0",
) -> Optional[object]:
    """
    Optionally start a standalone Prometheus scrape endpoint using
    prometheus_client's built-in HTTP server.

    Reads env if args are None:
      ANIMICA_MINER_METRICS_PORT (e.g., 9103)
      ANIMICA_MINER_METRICS_ADDR (default 0.0.0.0)

    Returns a handle (or None) depending on the client implementation.
    """
    if not _PROM_OK:
        return None

    if port is None:
        p = os.getenv("ANIMICA_MINER_METRICS_PORT")
        if not p:
            return None  # disabled
        try:
            port = int(p)
        except ValueError:
            return None

    addr = os.getenv("ANIMICA_MINER_METRICS_ADDR", addr)
    # start_http_server is non-blocking and returns None; keep signature anyway
    start_http_server(port, addr=addr)
    return None


def build_asgi_metrics_app():
    """
    Build a tiny ASGI app exposing /metrics.

    Usage with uvicorn:
        import uvicorn
        from mining.metrics import build_asgi_metrics_app
        uvicorn.run(build_asgi_metrics_app(), host="0.0.0.0", port=9103)

    Returns:
        Starlette app (if starlette and prometheus_client are available),
        otherwise None.
    """
    if not _PROM_OK:
        return None
    try:
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Route

        async def _metrics(_request):
            data = generate_latest(REGISTRY)
            return Response(data, media_type=CONTENT_TYPE_LATEST)

        return Starlette(routes=[Route("/metrics", _metrics)])
    except Exception:  # starlette not installed, etc.
        return None
