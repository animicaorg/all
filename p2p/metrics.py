"""
p2p.metrics
===========

Prometheus metrics for the P2P subsystem.  Independent from any web framework:
- uses the default `prometheus_client` registry (configurable),
- exposes helpers to mutate metrics from transports/handlers,
- provides a tiny ASGI app factory for mounting at `/metrics` if desired.

Label hints
-----------
- direction: "in" | "out"
- transport: "tcp" | "quic" | "ws"
- topic: canonical gossip topic string (e.g. "blocks", "txs", "shares")
- msg: short message name/id (e.g. "HELLO", "INV", "GETDATA")
- error_type: "handshake"|"decode"|"ratelimit"|"protocol"|"timeout"|"internal"

Usage
-----
    from p2p.metrics import METRICS, inc_bytes, inc_msg, observe_rtt

    inc_bytes("out", 1024, transport="tcp")
    inc_msg("in", topic="blocks", msg="BLOCK_ANNOUNCE")
    observe_rtt("12D3K...", 0.042)

Mounting a /metrics endpoint (ASGI):
    from p2p.metrics import make_asgi_app
    app = make_asgi_app()  # mount with your ASGI server

If you already have a FastAPI app (e.g., in rpc/server.py), prefer to reuse its
/metrics and only register P2P counters into the global registry.
"""

from __future__ import annotations

import os
import time
from typing import Dict, Iterable, Optional

from prometheus_client import (CONTENT_TYPE_LATEST, REGISTRY,
                               CollectorRegistry, Counter, Gauge, Histogram,
                               generate_latest)

# --------------------------------------------------------------------------- #
# Registry selection & default collectors
# --------------------------------------------------------------------------- #


def _pick_registry() -> CollectorRegistry:
    """
    Choose a registry. Supports Prometheus multiprocess mode if PROMETHEUS_MULTIPROC_DIR is set.
    """
    mp_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if mp_dir:
        # In multiprocess mode prometheus_client switches globals automatically.
        # Using REGISTRY is the correct thing to do.
        return REGISTRY
    return REGISTRY


REG: CollectorRegistry = _pick_registry()


# --------------------------------------------------------------------------- #
# Metric definitions
# --------------------------------------------------------------------------- #

# Bytes/framing
p2p_bytes_total = Counter(
    "p2p_bytes_total",
    "Total P2P bytes by direction and transport.",
    labelnames=("direction", "transport"),
    registry=REG,
)
p2p_frame_size_bytes = Histogram(
    "p2p_frame_size_bytes",
    "Observed frame sizes (encrypted payload).",
    labelnames=("direction", "transport"),
    buckets=(
        64,
        128,
        256,
        512,
        1024,
        2048,
        4096,
        8192,
        16384,
        32768,
        65536,
        131072,
        262144,
        524288,
        1048576,
    ),
    registry=REG,
)

# Messages
p2p_msgs_total = Counter(
    "p2p_msgs_total",
    "Total wire messages by direction/topic/msg.",
    labelnames=("direction", "topic", "msg"),
    registry=REG,
)

# Gossip lifecycle
p2p_gossip_events_total = Counter(
    "p2p_gossip_events_total",
    "Gossip events by action and topic.",
    labelnames=("action", "topic"),
    registry=REG,
)

# Peers & mesh state
p2p_peers_connected = Gauge(
    "p2p_peers_connected",
    "Current number of connected peers (all transports).",
    registry=REG,
)
p2p_peers_by_transport = Gauge(
    "p2p_peers_by_transport",
    "Connected peers per transport.",
    labelnames=("transport",),
    registry=REG,
)
p2p_mesh_size = Gauge(
    "p2p_mesh_size",
    "Gossip mesh size per topic.",
    labelnames=("topic",),
    registry=REG,
)

# Timings & quality
p2p_handshake_seconds = Histogram(
    "p2p_handshake_seconds",
    "Handshake duration distribution.",
    labelnames=("transport",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
    registry=REG,
)
p2p_rtt_seconds = Histogram(
    "p2p_rtt_seconds",
    "Peer ping RTT distribution.",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
    registry=REG,
)
p2p_msg_decode_seconds = Histogram(
    "p2p_msg_decode_seconds",
    "Message decode/validation latency.",
    labelnames=("msg",),
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
    registry=REG,
)

# Queues / backpressure
p2p_queue_depth = Gauge(
    "p2p_queue_depth",
    "Inbound/outbound queue depth.",
    labelnames=("direction",),
    registry=REG,
)

# Errors
p2p_errors_total = Counter(
    "p2p_errors_total",
    "Errors by type.",
    labelnames=("error_type",),
    registry=REG,
)


# --------------------------------------------------------------------------- #
# Public helpers (thin, allocation-light)
# --------------------------------------------------------------------------- #


def inc_bytes(direction: str, n: int, *, transport: str = "tcp") -> None:
    """Increment byte counters and observe frame size histogram."""
    if n <= 0:
        return
    p2p_bytes_total.labels(direction=direction, transport=transport).inc(n)
    p2p_frame_size_bytes.labels(direction=direction, transport=transport).observe(n)


def inc_msg(direction: str, *, topic: str, msg: str) -> None:
    """Increment message counters by direction/topic/msg."""
    p2p_msgs_total.labels(direction=direction, topic=topic, msg=msg).inc()


def inc_gossip(action: str, *, topic: str) -> None:
    """
    Record a gossip event:
      action ∈ {"publish","receive","graft","prune","reject","duplicate"}
    """
    p2p_gossip_events_total.labels(action=action, topic=topic).inc()


def set_peers(n_total: int, by_transport: Optional[Dict[str, int]] = None) -> None:
    """Set total peers gauge and optional per-transport gauges."""
    p2p_peers_connected.set(max(0, int(n_total)))
    if by_transport:
        for tr, n in by_transport.items():
            p2p_peers_by_transport.labels(transport=tr).set(max(0, int(n)))


def set_mesh_size(topic: str, size: int) -> None:
    """Set current mesh size for a topic."""
    p2p_mesh_size.labels(topic=topic).set(max(0, int(size)))


def observe_handshake(seconds: float, *, transport: str = "tcp") -> None:
    """Observe a completed handshake."""
    if seconds >= 0:
        p2p_handshake_seconds.labels(transport=transport).observe(seconds)


def observe_rtt(peer_id: str, seconds: float) -> None:
    """Observe a ping RTT (peer_id is unused but convenient for call sites)."""
    if seconds >= 0:
        p2p_rtt_seconds.observe(seconds)


class _Timer:
    """Small context manager to time decode paths: with time_decode('INV'): ..."""

    __slots__ = ("msg", "start")

    def __init__(self, msg: str):
        self.msg = msg
        self.start = 0.0

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self.start
        p2p_msg_decode_seconds.labels(msg=self.msg).observe(dt)
        # errors are counted separately; we just record elapsed


def time_decode(msg: str) -> _Timer:
    """Return a context manager to measure decode/validate latency."""
    return _Timer(msg)


def set_queue_depth(direction: str, depth: int) -> None:
    """Update queue depth gauge (e.g., 'in' or 'out')."""
    p2p_queue_depth.labels(direction=direction).set(max(0, int(depth)))


def inc_error(error_type: str) -> None:
    """Increment error counter of a given type."""
    p2p_errors_total.labels(error_type=error_type).inc()


# --------------------------------------------------------------------------- #
# ASGI app for /metrics
# --------------------------------------------------------------------------- #


def make_asgi_app(registry: CollectorRegistry = REG):
    """
    Return a minimal ASGI app that serves Prometheus metrics.

    Example (Starlette/FastAPI style mounting):
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

    No extra dependencies required.
    """

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"metrics: http only"})
            return

        if scope.get("method", "GET") not in ("GET", "HEAD"):
            await send({"type": "http.response.start", "status": 405, "headers": []})
            await send({"type": "http.response.body", "body": b"method not allowed"})
            return

        output = generate_latest(registry)
        headers = [
            (b"content-type", CONTENT_TYPE_LATEST.encode("ascii")),
            (b"cache-control", b"no-cache"),
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        if scope.get("method") == "HEAD":
            await send({"type": "http.response.body", "body": b""})
        else:
            await send({"type": "http.response.body", "body": output})

    return app


# --------------------------------------------------------------------------- #
# Export a simple facade (optional)
# --------------------------------------------------------------------------- #


class _Facade:
    """Namespace-like access for importers that prefer a single object."""

    registry = REG
    bytes_total = p2p_bytes_total
    frame_size_bytes = p2p_frame_size_bytes
    msgs_total = p2p_msgs_total
    gossip_events_total = p2p_gossip_events_total
    peers_connected = p2p_peers_connected
    peers_by_transport = p2p_peers_by_transport
    mesh_size = p2p_mesh_size
    handshake_seconds = p2p_handshake_seconds
    rtt_seconds = p2p_rtt_seconds
    msg_decode_seconds = p2p_msg_decode_seconds
    queue_depth = p2p_queue_depth
    errors_total = p2p_errors_total


METRICS = _Facade()

_METRICS_SINGLETON: Optional[_Facade] = None


def get_metrics(registry: Optional[CollectorRegistry] = None) -> _Facade:
    """
    Return a process-wide metrics facade. Ignores custom registries for
    compatibility; all metrics share the module-level registry.
    """
    global _METRICS_SINGLETON
    if _METRICS_SINGLETON is None:
        _METRICS_SINGLETON = METRICS
    return _METRICS_SINGLETON


__all__ = [
    "REG",
    "METRICS",
    "get_metrics",
    "inc_bytes",
    "inc_msg",
    "inc_gossip",
    "set_peers",
    "set_mesh_size",
    "observe_handshake",
    "observe_rtt",
    "time_decode",
    "set_queue_depth",
    "inc_error",
    "make_asgi_app",
]
