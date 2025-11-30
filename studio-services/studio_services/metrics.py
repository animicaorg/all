from __future__ import annotations

"""
Prometheus metrics setup and /metrics exporter for Studio Services.

Features
--------
- Single- *and* multi-process (gunicorn/uvicorn workers) support.
- Low-overhead ASGI middleware that records:
    - http_requests_total{method,path,status}
    - http_request_duration_seconds histogram
    - http_inprogress_requests gauge
- Clean FastAPI router mounted at /metrics (configurable).
- Optional service metadata metric (service_info).

Usage
-----
    from fastapi import FastAPI
    from studio_services.metrics import setup_metrics

    app = FastAPI()
    setup_metrics(app, service_name="studio-services", service_version="0.1.0", path="/metrics")

Env
---
- PROMETHEUS_MULTIPROC_DIR: if set, use multiprocess registry/collectors.
- METRICS_PATH: override default /metrics path (optional).
"""

import os
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, FastAPI
from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry, Counter,
                               Gauge, Histogram, generate_latest)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

try:
    # Optional: Info metric present in recent prometheus_client
    from prometheus_client import Info  # type: ignore
except Exception:  # pragma: no cover
    Info = None  # type: ignore

# Multiprocess support (only imported/used if env set)
try:  # pragma: no cover - exercised in multi-proc deployments
    from prometheus_client import (PlatformCollector, ProcessCollector,
                                   multiprocess)
except Exception:  # pragma: no cover
    multiprocess = None  # type: ignore
    ProcessCollector = None  # type: ignore
    PlatformCollector = None  # type: ignore


# ------------------------------ Registry -------------------------------------


class Metrics:
    """
    Holder for registry and metric objects. Exposed via app.state.metrics.
    """

    def __init__(
        self, service_name: str, service_version: Optional[str] = None
    ) -> None:
        self.multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
        self.registry = CollectorRegistry()

        if self.multiproc_dir and multiprocess:
            # When using multiprocess, only MultiProcessCollector should be registered.
            multiprocess.MultiProcessCollector(self.registry)
        else:
            # Single process default collectors
            if ProcessCollector:
                ProcessCollector(registry=self.registry)
            if PlatformCollector:
                PlatformCollector(registry=self.registry)

        # HTTP metrics
        # Note: in multiprocess mode, Gauges must specify multiprocess_mode.
        gauge_kwargs: Dict[str, Any] = {"registry": self.registry}
        if self.multiproc_dir:
            gauge_kwargs["multiprocess_mode"] = "livesum"

        self.http_inprogress = Gauge(
            "http_inprogress_requests",
            "In-progress HTTP requests",
            ["method", "path"],
            **gauge_kwargs,
        )

        self.http_requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["method", "path", "status"],
            registry=self.registry,
        )

        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "HTTP request duration in seconds",
            ["method", "path", "status"],
            # Reasonable buckets for API latencies (seconds)
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
            registry=self.registry,
        )

        # Service metadata (constant)
        if Info is not None:
            self.service_info = Info("service_info", "Service metadata", registry=self.registry)  # type: ignore
            payload = {"name": service_name}
            if service_version:
                payload["version"] = service_version
            # `info` sets a gauge-like const metric to 1 with these labels
            try:
                self.service_info.info(payload)  # type: ignore
            except Exception:
                # If Info isn't available or behaves differently across versions, ignore.
                pass

    def render_latest(self) -> bytes:
        return generate_latest(self.registry)


# ------------------------------ Middleware -----------------------------------


def _extract_path_template(scope: Scope) -> str:
    """
    Extract a low-cardinality path template from Starlette/FastAPI route, falling
    back to raw path if unavailable.
    """
    route = scope.get("route")
    for attr in ("path_format", "path"):  # FastAPI exposes path_format
        if route is not None and hasattr(route, attr):
            val = getattr(route, attr, None)
            if isinstance(val, str) and val:
                return val
    # Fallback to the literal path
    raw = scope.get("path") or (scope.get("raw_path") or b"").decode(
        "latin-1", "ignore"
    )
    return raw if isinstance(raw, str) else raw.decode("latin-1", "ignore")


class PrometheusMiddleware:
    """
    Minimal, allocation-light ASGI middleware to record HTTP metrics.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path_tmpl = _extract_path_template(scope)
        start = time.perf_counter()
        status_code = 500  # default in case of early error

        # Track in-progress
        self.metrics.http_inprogress.labels(method, path_tmpl).inc()

        async def send_wrapped(message: Dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            duration = time.perf_counter() - start
            labels = (method, path_tmpl, str(status_code))
            try:
                self.metrics.http_requests_total.labels(*labels).inc()
                self.metrics.http_request_duration_seconds.labels(*labels).observe(
                    duration
                )
            finally:
                self.metrics.http_inprogress.labels(method, path_tmpl).dec()


# ------------------------------ Router ---------------------------------------


def create_metrics_router(metrics: Metrics, path: str = "/metrics") -> APIRouter:
    """
    Build a router that serves Prometheus metrics at `path`.
    """
    router = APIRouter()

    @router.get(path, include_in_schema=False)
    async def metrics_endpoint() -> Response:
        try:
            payload = metrics.render_latest()
            return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
        except Exception as e:
            # Never raise here; exporters must be resilient.
            return PlainTextResponse(f"metrics error: {e}", status_code=500)

    return router


# ------------------------------ Setup helper ---------------------------------


def setup_metrics(
    app: FastAPI,
    *,
    service_name: str = "studio-services",
    service_version: Optional[str] = None,
    path: Optional[str] = None,
) -> Metrics:
    """
    Fully wire up Prometheus metrics into a FastAPI app:
    - create registry & metric objects
    - add ASGI middleware for HTTP metrics
    - mount /metrics (or custom path)

    Returns the `Metrics` instance and stores it in `app.state.metrics`.
    """
    metrics = Metrics(service_name=service_name, service_version=service_version)
    app.add_middleware(PrometheusMiddleware, metrics=metrics)

    export_path = path or os.getenv("METRICS_PATH") or "/metrics"
    router = create_metrics_router(metrics, export_path)
    app.include_router(router)

    # Expose for other modules to increment domain-specific metrics if needed
    app.state.metrics = metrics
    return metrics


__all__ = [
    "Metrics",
    "PrometheusMiddleware",
    "create_metrics_router",
    "setup_metrics",
]
