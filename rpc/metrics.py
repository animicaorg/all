from __future__ import annotations

"""
Prometheus metrics for the RPC service.

- Exposes a /metrics endpoint (text/plain; version=0.0.4).
- Provides HTTP request counters & latency histograms.
- Provides JSON-RPC method-level metrics via explicit hooks.
- Provides WebSocket gauges/counters via explicit hooks.
- Optional support for multiprocess mode if PROMETHEUS_MULTIPROC_DIR is set.

Usage
-----
from rpc.metrics import mount_metrics, http_metrics_middleware, rpc_metrics

app = FastAPI()
mount_metrics(app)                        # adds GET /metrics
app.add_middleware(http_metrics_middleware)

# In your JSON-RPC dispatcher:
with rpc_metrics.observe_jsonrpc("chain.getHead") as obs:
    try:
        result = handler(...)
        obs.ok()
        return result
    except JSONRPCError as e:
        obs.error(str(e.code))           # record code label
        raise
    except Exception:
        obs.error("internal")
        raise

# In your WS hub:
rpc_metrics.ws_connected()
rpc_metrics.ws_disconnected()
rpc_metrics.ws_msg_sent(topic="newHeads")
rpc_metrics.ws_msg_received(topic="pendingTxs")
"""

import os
import time
import typing as t

from fastapi import APIRouter, FastAPI, Request
from prometheus_client import (CONTENT_TYPE_LATEST, REGISTRY,
                               CollectorRegistry, Counter, Gauge, Histogram,
                               generate_latest)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, Response


def _registry() -> CollectorRegistry:
    mp_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        # Multiprocess mode (Gunicorn/UVicorn workers). You must clear the
        # dir on boot (handled by your process manager).
        from prometheus_client import multiprocess  # type: ignore

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return registry
    return REGISTRY  # default in-process registry


REG = _registry()


# ---- Metric definitions ----------------------------------------------------

# HTTP
HTTP_REQUESTS = Counter(
    "animica_http_requests_total",
    "Total HTTP requests by method and path and status.",
    ["method", "path", "status"],
    registry=REG,
)

HTTP_LATENCY = Histogram(
    "animica_http_request_duration_seconds",
    "HTTP request duration in seconds by method and path.",
    ["method", "path"],
    # Buckets suited for JSON-RPC+small REST handlers.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
    registry=REG,
)

# JSON-RPC
JSONRPC_CALLS = Counter(
    "animica_jsonrpc_requests_total",
    "Total JSON-RPC method calls by method, transport and status.",
    ["method", "transport", "status", "code"],
    registry=REG,
)
JSONRPC_LATENCY = Histogram(
    "animica_jsonrpc_request_duration_seconds",
    "JSON-RPC method latency in seconds by method and transport.",
    ["method", "transport"],
    buckets=(0.001, 0.003, 0.0075, 0.015, 0.03, 0.06, 0.12, 0.25, 0.5, 1, 2, 5),
    registry=REG,
)

# WebSocket hub
WS_CONNECTIONS = Gauge(
    "animica_ws_connections",
    "Current number of active WebSocket clients.",
    registry=REG,
)
WS_MESSAGES = Counter(
    "animica_ws_messages_total",
    "WebSocket messages sent/received by topic and direction.",
    ["topic", "direction"],  # direction ∈ {"sent","recv"}
    registry=REG,
)

# Pending pool (optional, set from rpc/pending_pool.py)
PENDING_TXS = Gauge(
    "animica_pending_txs",
    "Number of transactions in the pending pool.",
    registry=REG,
)
PENDING_BYTES = Gauge(
    "animica_pending_bytes",
    "Approximate bytes used by the pending pool.",
    registry=REG,
)

# Head / chain (optional helpers)
CHAIN_HEIGHT = Gauge(
    "animica_chain_height",
    "Current canonical chain height known to this node.",
    registry=REG,
)
RPC_SUBSCRIBERS = Gauge(
    "animica_ws_subscribers",
    "WS subscribers by stream/topic.",
    ["topic"],
    registry=REG,
)


# ---- HTTP Middleware -------------------------------------------------------


def _short_path(path: str) -> str:
    """
    Collapse high-cardinality path segments. We only expose a few stable paths to
    Prometheus labels to avoid blowups: /rpc, /ws, /metrics, /healthz, /readyz, /openrpc.json.
    All others are reported as '/other'.
    """
    if path in ("/rpc", "/ws", "/metrics", "/healthz", "/readyz", "/openrpc.json"):
        return path
    return "/other"


class _HttpMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        method = request.method.upper()
        path = _short_path(request.url.path)

        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            # Count error as 500 to avoid losing the event.
            elapsed = time.perf_counter() - start
            HTTP_REQUESTS.labels(method=method, path=path, status="500").inc()
            HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)
            raise

        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(
            method=method, path=path, status=str(response.status_code)
        ).inc()
        HTTP_LATENCY.labels(method=method, path=path).observe(elapsed)
        return response


# exported alias for app.add_middleware(...)
http_metrics_middleware = _HttpMetricsMiddleware


# ---- JSON-RPC helper (explicit instrumentation) ---------------------------


class _RpcObservation:
    __slots__ = ("_method", "_transport", "_start", "_ended")

    def __init__(self, method: str, transport: str) -> None:
        self._method = method
        self._transport = transport
        self._start = time.perf_counter()
        self._ended = False

    def _finish(self, status: str, code: str = "0") -> None:
        if self._ended:
            return
        self._ended = True
        dt = time.perf_counter() - self._start
        JSONRPC_CALLS.labels(
            method=self._method, transport=self._transport, status=status, code=code
        ).inc()
        JSONRPC_LATENCY.labels(method=self._method, transport=self._transport).observe(
            dt
        )

    def ok(self) -> None:
        """Mark successful completion."""
        self._finish("ok", "0")

    def error(self, code: str = "internal") -> None:
        """Mark failed completion, with a string code label (e.g., '-32602')."""
        self._finish("error", code)


class _RpcMetrics:
    """
    Central helper for explicit JSON-RPC & WS instrumentation and optional gauges.
    """

    def observe_jsonrpc(self, method: str, transport: str = "http") -> _RpcObservation:
        """
        Create an observation context for a JSON-RPC call.

        Example:
            with rpc_metrics.observe_jsonrpc("chain.getHead") as obs:
                ...
                obs.ok() or obs.error("-32602")
        """
        return _RpcObservation(method=method, transport=transport)

    # WebSocket helpers
    def ws_connected(self) -> None:
        WS_CONNECTIONS.inc()

    def ws_disconnected(self) -> None:
        WS_CONNECTIONS.dec()

    def ws_msg_sent(self, *, topic: str) -> None:
        WS_MESSAGES.labels(topic=topic, direction="sent").inc()

    def ws_msg_received(self, *, topic: str) -> None:
        WS_MESSAGES.labels(topic=topic, direction="recv").inc()

    # Pending pool gauges
    def set_pending(self, count: int, approx_bytes: int | float = 0) -> None:
        PENDING_TXS.set(max(0, int(count)))
        if approx_bytes is not None:
            PENDING_BYTES.set(float(approx_bytes))

    # Chain height gauge
    def set_height(self, height: int) -> None:
        CHAIN_HEIGHT.set(max(0, int(height)))

    # Subscribers gauge (optionally maintain per topic)
    def set_subscribers(self, topic: str, n: int) -> None:
        RPC_SUBSCRIBERS.labels(topic=topic).set(max(0, int(n)))


rpc_metrics = _RpcMetrics()


# ---- /metrics endpoint -----------------------------------------------------


def _metrics_handler() -> PlainTextResponse:
    # generate_latest selects the global/default REGISTRY or our custom one.
    data = generate_latest(REG)  # bytes
    media_type = (
        CONTENT_TYPE_LATEST.decode()
        if isinstance(CONTENT_TYPE_LATEST, (bytes, bytearray))
        else CONTENT_TYPE_LATEST
    )
    return Response(content=data, media_type=media_type)


def mount_metrics(app: FastAPI) -> None:
    """
    Mount GET /metrics on the provided FastAPI app, ready for Prometheus to scrape.
    """
    router = APIRouter()
    router.add_api_route(
        "/metrics", _metrics_handler, methods=["GET"], include_in_schema=False
    )
    app.include_router(router)


__all__ = [
    "mount_metrics",
    "http_metrics_middleware",
    "rpc_metrics",
    # raw collectors (optional import by other modules)
    "HTTP_REQUESTS",
    "HTTP_LATENCY",
    "JSONRPC_CALLS",
    "JSONRPC_LATENCY",
    "WS_CONNECTIONS",
    "WS_MESSAGES",
    "PENDING_TXS",
    "PENDING_BYTES",
    "CHAIN_HEIGHT",
    "RPC_SUBSCRIBERS",
]
