from __future__ import annotations

"""
Prometheus metrics for the AI Compute Fund (AICF).

We expose counters and histograms covering:
- enqueue: jobs submitted by kind (AI / Quantum)
- assigns: assignment and renewals issued to providers
- proofs: ingested + verified (accepted/rejected) by kind
- payouts: settlement counts and amount distribution
- slashes: slash events by reason and amount distribution
- latencies: enqueue→assign, assign→complete, proof verify, settlement

This module is dependency-light and can be mounted into any ASGI app
or FastAPI app via the helpers at the bottom.
"""


import time
from contextlib import contextmanager
from typing import Optional

from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry, Counter,
                               Gauge, Histogram, generate_latest)

# Use a dedicated registry so embedding apps can choose to merge or expose it directly.
REGISTRY = CollectorRegistry()

# ────────────────────────────────────────────────────────────────────────────────
# Label conventions
#   kind: "ai" | "quantum"
#   result: "accepted" | "rejected" | "invalid"
#   reason: "traps_fail" | "qos_fail" | "availability_fail" | "misbehavior" | "other"
# ────────────────────────────────────────────────────────────────────────────────

# Counters
JOBS_ENQUEUED = Counter(
    "animica_aicf_jobs_enqueued_total",
    "Total jobs enqueued by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)

ASSIGNMENTS_ISSUED = Counter(
    "animica_aicf_assignments_issued_total",
    "Total assignment leases issued by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)

ASSIGNMENT_RENEWALS = Counter(
    "animica_aicf_assignment_renewals_total",
    "Total assignment lease renewals by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)

LEASES_LOST = Counter(
    "animica_aicf_leases_lost_total",
    "Total assignment leases lost by reason.",
    labelnames=("reason",),
    registry=REGISTRY,
)

PROOFS_INGESTED = Counter(
    "animica_aicf_proofs_ingested_total",
    "Total proof submissions ingested by kind.",
    labelnames=("kind",),
    registry=REGISTRY,
)

PROOFS_VERIFIED = Counter(
    "animica_aicf_proofs_verified_total",
    "Total proofs verified by kind and result.",
    labelnames=("kind", "result"),
    registry=REGISTRY,
)

PAYOUTS = Counter(
    "animica_aicf_payouts_total",
    "Total payout records created (settled/queued) by kind.",
    labelnames=("kind", "status"),  # status: "settled" | "queued"
    registry=REGISTRY,
)

SLASHES = Counter(
    "animica_aicf_slashes_total",
    "Total slash events by reason.",
    labelnames=("reason",),
    registry=REGISTRY,
)

# Histograms
_LATENCY_BUCKETS = (
    0.01,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)

ENQUEUE_TO_ASSIGN_SECONDS = Histogram(
    "animica_aicf_enqueue_to_assign_seconds",
    "Latency from enqueue to assignment issuance, by kind.",
    labelnames=("kind",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

ASSIGN_TO_COMPLETE_SECONDS = Histogram(
    "animica_aicf_assign_to_complete_seconds",
    "Latency from assignment to completion/proof submission, by kind.",
    labelnames=("kind",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

PROOF_VERIFY_SECONDS = Histogram(
    "animica_aicf_proof_verify_seconds",
    "Time spent verifying a proof submission, by kind.",
    labelnames=("kind",),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

SETTLEMENT_SECONDS = Histogram(
    "animica_aicf_settlement_seconds",
    "Time to settle payouts after accepted proofs (batch/epoch accounting).",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

# Monetary amounts are tracked in *tokens* (float) to keep bucket scales reasonable for histograms.
# Convert from nano-tokens at call sites if your accounting uses nanos.
PAYOUT_AMOUNT_TOKENS = Histogram(
    "animica_aicf_payout_amount_tokens",
    "Distribution of payout amounts (in tokens).",
    buckets=(
        0.001,
        0.01,
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2.5,
        5,
        10,
        25,
        50,
        100,
        250,
        500,
        1000,
    ),
    registry=REGISTRY,
)

SLASH_AMOUNT_TOKENS = Histogram(
    "animica_aicf_slash_amount_tokens",
    "Distribution of slash amounts (in tokens).",
    buckets=(
        0.001,
        0.01,
        0.05,
        0.1,
        0.25,
        0.5,
        1,
        2.5,
        5,
        10,
        25,
        50,
        100,
        250,
        500,
        1000,
    ),
    registry=REGISTRY,
)

# Gauges (optional real-time snapshots)
ACTIVE_LEASES = Gauge(
    "animica_aicf_active_leases",
    "Current number of active leases across all providers.",
    registry=REGISTRY,
)

QUEUE_DEPTH = Gauge(
    "animica_aicf_queue_depth",
    "Current job queue depth (ready + waiting).",
    labelnames=("kind",),
    registry=REGISTRY,
)

# ────────────────────────────────────────────────────────────────────────────────
# Recording helpers
# ────────────────────────────────────────────────────────────────────────────────


def record_enqueue(kind: str) -> None:
    """Increment the enqueue counter for a job kind."""
    JOBS_ENQUEUED.labels(kind=kind).inc()
    QUEUE_DEPTH.labels(kind=kind).inc()


def record_assignment(kind: str, renewed: bool = False) -> None:
    """Record an assignment issuance or renewal."""
    if renewed:
        ASSIGNMENT_RENEWALS.labels(kind=kind).inc()
    else:
        ASSIGNMENTS_ISSUED.labels(kind=kind).inc()
        # One job moved from queue to in-progress
        QUEUE_DEPTH.labels(kind=kind).dec()
        ACTIVE_LEASES.inc()


def record_lease_lost(reason: str) -> None:
    """Record a lost lease (by reason)."""
    LEASES_LOST.labels(reason=reason).inc()
    # A lease is no longer active
    ACTIVE_LEASES.dec()


def record_proof_ingested(kind: str) -> None:
    """Increment ingested proof submissions."""
    PROOFS_INGESTED.labels(kind=kind).inc()


def record_proof_verified(kind: str, result: str) -> None:
    """
    Increment verified proofs by result: 'accepted' | 'rejected' | 'invalid'.
    """
    PROOFS_VERIFIED.labels(kind=kind, result=result).inc()
    if result in ("accepted", "rejected", "invalid"):
        # Proof completion ends the lease for this job.
        # Guard against negative gauges if called more than once per job.
        ACTIVE_LEASES.dec()


def record_payout(kind: str, amount_tokens: float, status: str = "settled") -> None:
    """Record a payout event and observe its amount."""
    PAYOUTS.labels(kind=kind, status=status).inc()
    if amount_tokens >= 0:
        PAYOUT_AMOUNT_TOKENS.observe(float(amount_tokens))


def record_slash(reason: str, amount_tokens: float = 0.0) -> None:
    """Record a slash event and observe its amount."""
    SLASHES.labels(reason=reason).inc()
    if amount_tokens > 0:
        SLASH_AMOUNT_TOKENS.observe(float(amount_tokens))


# Timers (context managers) for latency histograms


@contextmanager
def time_enqueue_to_assign(kind: str):
    """Context manager to observe enqueue→assign latency for a given kind."""
    start = time.perf_counter()
    try:
        yield
    finally:
        ENQUEUE_TO_ASSIGN_SECONDS.labels(kind=kind).observe(time.perf_counter() - start)


@contextmanager
def time_assign_to_complete(kind: str):
    """Context manager to observe assign→complete latency for a given kind."""
    start = time.perf_counter()
    try:
        yield
    finally:
        ASSIGN_TO_COMPLETE_SECONDS.labels(kind=kind).observe(
            time.perf_counter() - start
        )


@contextmanager
def time_proof_verify(kind: str):
    """Context manager to observe proof verification time for a given kind."""
    start = time.perf_counter()
    try:
        yield
    finally:
        PROOF_VERIFY_SECONDS.labels(kind=kind).observe(time.perf_counter() - start)


@contextmanager
def time_settlement():
    """Context manager to observe settlement latency."""
    start = time.perf_counter()
    try:
        yield
    finally:
        SETTLEMENT_SECONDS.observe(time.perf_counter() - start)


# ────────────────────────────────────────────────────────────────────────────────
# ASGI/FastAPI mounting helpers
# ────────────────────────────────────────────────────────────────────────────────


def make_prometheus_asgi_app(registry: Optional[CollectorRegistry] = None):
    """
    Return a minimal ASGI app that serves Prometheus metrics at '/'.
    No external web framework required.
    """
    reg = registry or REGISTRY

    async def app(scope, receive, send):  # type: ignore[override]
        if scope["type"] != "http":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"Not Found"})
            return
        path = scope.get("path") or "/"
        if path != "/":
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"Not Found"})
            return
        payload = generate_latest(reg)
        headers = [
            (b"content-type", CONTENT_TYPE_LATEST.encode("ascii")),
            (b"cache-control", b"no-cache, no-store, must-revalidate"),
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    return app


def mount_fastapi(
    app, path: str = "/metrics", registry: Optional[CollectorRegistry] = None
) -> None:
    """
    Mount a GET {path} endpoint on a FastAPI app to serve metrics.

    Usage:
        from fastapi import FastAPI
        from aicf.metrics import mount_fastapi
        app = FastAPI()
        mount_fastapi(app)
    """
    reg = registry or REGISTRY
    try:
        from fastapi import Response  # type: ignore

        @app.get(path)
        def _metrics() -> "Response":
            return Response(generate_latest(reg), media_type=CONTENT_TYPE_LATEST)

    except Exception:  # pragma: no cover - optional dependency path
        # Silently ignore if FastAPI is not available; caller can use make_prometheus_asgi_app instead.
        pass


__all__ = [
    "REGISTRY",
    "JOBS_ENQUEUED",
    "ASSIGNMENTS_ISSUED",
    "ASSIGNMENT_RENEWALS",
    "LEASES_LOST",
    "PROOFS_INGESTED",
    "PROOFS_VERIFIED",
    "PAYOUTS",
    "SLASHES",
    "ENQUEUE_TO_ASSIGN_SECONDS",
    "ASSIGN_TO_COMPLETE_SECONDS",
    "PROOF_VERIFY_SECONDS",
    "SETTLEMENT_SECONDS",
    "PAYOUT_AMOUNT_TOKENS",
    "SLASH_AMOUNT_TOKENS",
    "ACTIVE_LEASES",
    "QUEUE_DEPTH",
    "record_enqueue",
    "record_assignment",
    "record_lease_lost",
    "record_proof_ingested",
    "record_proof_verified",
    "record_payout",
    "record_slash",
    "time_enqueue_to_assign",
    "time_assign_to_complete",
    "time_proof_verify",
    "time_settlement",
    "make_prometheus_asgi_app",
    "mount_fastapi",
]
