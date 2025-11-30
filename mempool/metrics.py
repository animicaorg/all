"""
mempool.metrics
================

Prometheus metrics for the mempool: sizes, admissions, rejections, evictions,
replacements, ingress, and a few latency/size histograms.

- Import is resilient: if `prometheus_client` isn't installed, all metrics
  degrade to no-ops so the node can still run (useful for minimal test envs).
- Labels are kept small & bounded to avoid cardinality explosions.

Typical usage:

    from .metrics import MEMPOOL_METRICS as MM

    MM.set_sizes(txs=len(pool), bytes=pool.bytes, ready=pool.ready_count(), held=pool.held_count())
    MM.set_watermark(min_fee_gwei)
    MM.record_admit(source="rpc", size_bytes=len(tx_bytes), effective_fee_gwei=tip_gwei + base_gwei, latency_s=0.004)
    MM.record_reject(reason="fee_too_low")
    MM.record_evict(reason="low_fee")
    MM.record_replace(outcome="accepted", delta_fee_ratio=1.12)
    MM.observe_validation(seconds=0.0007)
    MM.observe_wait_time(seconds=12.4)

Exposed metrics (names follow Prometheus conventions):

Gauges
------
- animica_mempool_size_txs
- animica_mempool_size_bytes
- animica_mempool_ready_txs
- animica_mempool_held_txs
- animica_mempool_watermark_min_fee_gwei

Counters
--------
- animica_mempool_admit_total{source}
- animica_mempool_reject_total{reason}
- animica_mempool_replace_total{outcome}
- animica_mempool_evict_total{reason}
- animica_mempool_ingress_bytes_total{source}

Histograms
----------
- animica_mempool_tx_size_bytes
- animica_mempool_effective_fee_gwei
- animica_mempool_admit_latency_seconds
- animica_mempool_validation_seconds
- animica_mempool_wait_time_seconds
"""

from __future__ import annotations

from typing import Optional

# ---------- Robust import with graceful no-op fallback ----------
try:
    from prometheus_client import (CollectorRegistry, Counter,  # type: ignore
                                   Gauge, Histogram)

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only in minimal envs
    _PROM_AVAILABLE = False

    class _Noop:
        def labels(self, *_, **__):
            return self

        def inc(self, *_a, **_kw):
            pass

        def set(self, *_a, **_kw):
            pass

        def observe(self, *_a, **_kw):
            pass

    class Counter(_Noop):
        pass  # type: ignore

    class Gauge(_Noop):
        pass  # type: ignore

    class Histogram(_Noop):
        pass  # type: ignore

    class CollectorRegistry:  # type: ignore
        def __init__(self, *_, **__):
            pass


# ---------- Buckets (careful to keep bounded and broadly useful) ----------
# Sizes: powers-of-two-ish up to ~1MB
_SIZE_BUCKETS = (
    100,
    200,
    400,
    800,
    1200,
    1600,
    2200,
    3000,
    4500,
    6000,
    8_000,
    12_000,
    16_000,
    24_000,
    32_000,
    48_000,
    64_000,
    96_000,
    128_000,
    256_000,
    512_000,
    1_000_000,
)

# Fees in gwei: spans dust up to very high congestion
_FEE_BUCKETS = (
    1,
    2,
    5,
    10,
    20,
    30,
    50,
    75,
    100,
    150,
    200,
    300,
    400,
    600,
    800,
    1_000,
    1_500,
    2_000,
    3_000,
    5_000,
    8_000,
    12_000,
)

# Latencies (seconds): microseconds to minutes
_LAT_BUCKETS_FAST = (
    0.00025,
    0.0005,
    0.001,
    0.002,
    0.004,
    0.008,
    0.016,
    0.032,
    0.064,
    0.128,
    0.256,
    0.512,
    1.0,
)
_LAT_BUCKETS_SLOW = (0.25, 0.5, 1, 2, 4, 8, 16, 32, 64, 120, 300, 600)


# ---------- Metrics wrapper ----------
class MempoolMetrics:
    def __init__(self, registry: Optional[CollectorRegistry] = None) -> None:
        # Using default registry by default; tests can inject their own.
        self.registry = registry

        kw = {"registry": registry} if _PROM_AVAILABLE and registry is not None else {}

        # Gauges
        self.size_txs = Gauge(
            "animica_mempool_size_txs",
            "Current number of transactions in the mempool.",
            **kw,
        )
        self.size_bytes = Gauge(
            "animica_mempool_size_bytes",
            "Approximate total byte size of transactions in the mempool.",
            **kw,
        )
        self.ready_txs = Gauge(
            "animica_mempool_ready_txs",
            "Transactions ready to be included (no nonce gaps, funds ok).",
            **kw,
        )
        self.held_txs = Gauge(
            "animica_mempool_held_txs",
            "Transactions held due to gaps/deps (not yet includable).",
            **kw,
        )
        self.watermark_min_fee_gwei = Gauge(
            "animica_mempool_watermark_min_fee_gwei",
            "Rolling minimum effective fee (gwei) required for admission.",
            **kw,
        )

        # Counters
        self.admit_total = Counter(
            "animica_mempool_admit_total",
            "Total admitted transactions by source.",
            labelnames=("source",),  # rpc|p2p|local
            **kw,
        )
        self.reject_total = Counter(
            "animica_mempool_reject_total",
            "Total rejected transactions by reason.",
            labelnames=("reason",),
            **kw,
        )
        self.replace_total = Counter(
            "animica_mempool_replace_total",
            "Total replacements by outcome.",
            labelnames=("outcome",),  # accepted|rejected|worse_fee
            **kw,
        )
        self.evict_total = Counter(
            "animica_mempool_evict_total",
            "Total evictions by reason.",
            labelnames=(
                "reason",
            ),  # low_fee|sender_cap|memory_pressure|ttl_expire|reorg
            **kw,
        )
        self.ingress_bytes_total = Counter(
            "animica_mempool_ingress_bytes_total",
            "Cumulative bytes ingressed into the mempool by source.",
            labelnames=("source",),  # rpc|p2p|local
            **kw,
        )

        # Histograms
        self.tx_size_bytes = Histogram(
            "animica_mempool_tx_size_bytes",
            "Observed transaction sizes (bytes).",
            buckets=_SIZE_BUCKETS,
            **kw,
        )
        self.effective_fee_gwei = Histogram(
            "animica_mempool_effective_fee_gwei",
            "Observed effective fee (base+tip) in gwei at admission.",
            buckets=_FEE_BUCKETS,
            **kw,
        )
        self.admit_latency_seconds = Histogram(
            "animica_mempool_admit_latency_seconds",
            "Latency from initial receive to admission.",
            buckets=_LAT_BUCKETS_FAST,
            **kw,
        )
        self.validation_seconds = Histogram(
            "animica_mempool_validation_seconds",
            "Time spent in validation (stateless + accounting).",
            buckets=_LAT_BUCKETS_FAST,
            **kw,
        )
        self.wait_time_seconds = Histogram(
            "animica_mempool_wait_time_seconds",
            "Time from admission to removal (inclusion/evict/replace).",
            buckets=_LAT_BUCKETS_SLOW,
            **kw,
        )

    # ------------- public helper API -------------
    def set_sizes(self, *, txs: int, bytes: int, ready: int, held: int) -> None:
        self.size_txs.set(float(txs))
        self.size_bytes.set(float(bytes))
        self.ready_txs.set(float(ready))
        self.held_txs.set(float(held))

    def set_watermark(self, min_fee_gwei: float) -> None:
        self.watermark_min_fee_gwei.set(float(min_fee_gwei))

    def record_admit(
        self,
        *,
        source: str,  # "rpc" | "p2p" | "local"
        size_bytes: int,
        effective_fee_gwei: float,
        latency_s: float,
    ) -> None:
        src = _sanitize_label(source, {"rpc", "p2p", "local"})
        self.admit_total.labels(source=src).inc()
        self.ingress_bytes_total.labels(source=src).inc(size_bytes)
        self.tx_size_bytes.observe(float(size_bytes))
        self.effective_fee_gwei.observe(float(effective_fee_gwei))
        self.admit_latency_seconds.observe(float(latency_s))

    def record_reject(self, *, reason: str) -> None:
        r = _sanitize_label(
            reason,
            {
                "fee_too_low",
                "nonce_gap",
                "oversize",
                "invalid_sig",
                "chain_id",
                "ttl",
                "dup",
                "policy",
                "dos",
                "other",
            },
            fallback="other",
        )
        self.reject_total.labels(reason=r).inc()

    def record_replace(
        self, *, outcome: str, delta_fee_ratio: Optional[float] = None
    ) -> None:
        o = _sanitize_label(
            outcome, {"accepted", "rejected", "worse_fee"}, fallback="rejected"
        )
        self.replace_total.labels(outcome=o).inc()
        if delta_fee_ratio is not None and delta_fee_ratio >= 0:
            # Reuse fee histogram for visibility into replacement pressure.
            self.effective_fee_gwei.observe(
                0.0
            )  # keeps series alive even if no fee provided
            # We don't add a dedicated metric to avoid cardinality; callers can log delta_fee_ratio.

    def record_evict(self, *, reason: str) -> None:
        r = _sanitize_label(
            reason,
            {
                "low_fee",
                "sender_cap",
                "memory_pressure",
                "ttl_expire",
                "reorg",
                "other",
            },
            fallback="other",
        )
        self.evict_total.labels(reason=r).inc()

    def inc_ingress_bytes(self, *, source: str, nbytes: int) -> None:
        src = _sanitize_label(source, {"rpc", "p2p", "local"})
        self.ingress_bytes_total.labels(source=src).inc(nbytes)

    def observe_validation(self, *, seconds: float) -> None:
        self.validation_seconds.observe(float(seconds))

    def observe_wait_time(self, *, seconds: float) -> None:
        self.wait_time_seconds.observe(float(seconds))


def _sanitize_label(
    value: str, allowed: set[str], fallback: Optional[str] = None
) -> str:
    v = (value or "").strip().lower()
    if v in allowed:
        return v
    if fallback is not None:
        return fallback
    # map a few common synonyms to keep series bounded
    synonyms = {
        "api": "rpc",
        "jsonrpc": "rpc",
        "peer": "p2p",
        "net": "p2p",
        "sig": "invalid_sig",
        "signature": "invalid_sig",
        "gwei": "fee_too_low",
        "minfee": "fee_too_low",
        "timeout": "ttl",
        "expired": "ttl",
        "spam": "dos",
    }
    return synonyms.get(v, next(iter(allowed)))


# Singleton used by the mempool components
MEMPOOL_METRICS = MempoolMetrics()

__all__ = ["MempoolMetrics", "MEMPOOL_METRICS"]
