"""
execution.metrics — Prometheus counters & histograms for the Animica execution layer.

Design goals
------------
* Optional dependency: if `prometheus_client` is not installed, this module provides
  no-op shims so the rest of the node runs without metrics.
* Centralized registry: consumers can call `get_registry()` and `generate_latest_text()`
  to expose metrics via HTTP (e.g., in the RPC app).
* Simple helpers: `observe_tx(...)` and `time_block_apply(...)` cover the common paths.

Exposed metrics (names are prefixed with `animica_exec_`):
  - tx_apply_total{result,kind}          : Counter — transactions processed by result
  - tx_gas_used{kind}                    : Histogram — gas used per transaction
  - tx_logs_emitted{kind}                : Histogram — logs/events emitted per transaction
  - block_apply_seconds                  : Histogram — time to apply a block

Labels:
  - result ∈ {success, revert, oog, error}
  - kind   ∈ {transfer, deploy, call, other}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional
import os
import time


# ------------------------------ optional import ------------------------------

try:
    # Prometheus Python client (preferred)
    from prometheus_client import (
        Counter,
        Histogram,
        CollectorRegistry,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when dependency missing
    _PROM_AVAILABLE = False

    class _NoopMetric:
        def __init__(self, *_, **__): ...
        def labels(self, *_, **__): return self
        def inc(self, *_a, **_k): ...
        def observe(self, *_a, **_k): ...

    # No-op shims with compatible constructors/APIs
    Counter = _NoopMetric       # type: ignore
    Histogram = _NoopMetric     # type: ignore

    class CollectorRegistry:     # type: ignore
        def __init__(self, *_, **__): ...

    def generate_latest(_=None):  # type: ignore
        return b""

    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"  # type: ignore


# ------------------------------ configuration -------------------------------

_PREFIX = "animica_exec_"

# Allow narrow tuning of histogram buckets via env (simple comma-separated lists).
# If unset, we pick sane defaults for a Python VM chain.
def _buckets_from_env(name: str, default: Iterable[float]) -> Iterable[float]:
    raw = os.getenv(name)
    if not raw:
        return default
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            # ignore bad tokens
            continue
    return out or default


_TX_GAS_BUCKETS = tuple(_buckets_from_env(
    "ANIMICA_METRICS_TX_GAS_BUCKETS",
    # Rough, log-scale-ish buckets (gas units)
    (500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000, 1_000_000),
))
_TX_LOGS_BUCKETS = tuple(_buckets_from_env(
    "ANIMICA_METRICS_TX_LOGS_BUCKETS",
    (0, 1, 2, 4, 8, 16, 32, 64, 128, 256),
))
_BLOCK_SECONDS_BUCKETS = tuple(_buckets_from_env(
    "ANIMICA_METRICS_BLOCK_SECONDS_BUCKETS",
    # 5ms .. 30s
    (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0,
     2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0),
))


# ------------------------------ registry & ctor ------------------------------

_registry: Optional[CollectorRegistry] = None

def set_registry(registry: CollectorRegistry) -> None:
    """
    Inject a custom CollectorRegistry (e.g., an app-global one shared across modules).
    Must be called before the first metric is constructed.
    """
    global _registry, TX_APPLY_TOTAL, TX_GAS_USED, TX_LOGS_EMITTED, BLOCK_APPLY_SECONDS
    if _registry is not None:
        return  # already initialized; callers should set registry early
    _registry = registry
    _build_metrics()

def get_registry() -> CollectorRegistry:
    """
    Return the metrics registry, creating one on first use.
    """
    global _registry
    if _registry is None:
        _registry = CollectorRegistry() if _PROM_AVAILABLE else CollectorRegistry()
        _build_metrics()
    return _registry


# Metric singletons (bound during _build_metrics)
TX_APPLY_TOTAL: Counter
TX_GAS_USED: Histogram
TX_LOGS_EMITTED: Histogram
BLOCK_APPLY_SECONDS: Histogram


def _build_metrics() -> None:
    """
    Instantiate metrics bound to the current registry. Idempotent.
    """
    reg = get_registry()

    # Use namespaced metric names for clarity when scraped alongside other subsystems.
    global TX_APPLY_TOTAL, TX_GAS_USED, TX_LOGS_EMITTED, BLOCK_APPLY_SECONDS

    TX_APPLY_TOTAL = Counter(
        _PREFIX + "tx_apply_total",
        "Transactions executed (by result and kind).",
        labelnames=("result", "kind"),
        registry=reg,
    )
    TX_GAS_USED = Histogram(
        _PREFIX + "tx_gas_used",
        "Gas used per transaction (VM-level).",
        labelnames=("kind",),
        buckets=_TX_GAS_BUCKETS,
        registry=reg,
    )
    TX_LOGS_EMITTED = Histogram(
        _PREFIX + "tx_logs_emitted",
        "Logs/events emitted per transaction.",
        labelnames=("kind",),
        buckets=_TX_LOGS_BUCKETS,
        registry=reg,
    )
    BLOCK_APPLY_SECONDS = Histogram(
        _PREFIX + "block_apply_seconds",
        "Wall time to apply a block end-to-end.",
        buckets=_BLOCK_SECONDS_BUCKETS,
        registry=reg,
    )


# ------------------------------ helpers -------------------------------------

def _norm_result(s: str) -> str:
    s = (s or "").strip().lower()
    if s in {"ok", "success", "s"}:
        return "success"
    if s in {"revert", "rv"}:
        return "revert"
    if s in {"oog", "out_of_gas", "out-of-gas"}:
        return "oog"
    return "error" if s else "error"


def _norm_kind(s: str) -> str:
    s = (s or "").strip().lower()
    if s in {"xfer", "transfer"}:
        return "transfer"
    if s in {"deploy", "create"}:
        return "deploy"
    if s in {"call", "invoke"}:
        return "call"
    return "other"


def observe_tx(*, result: str, kind: str, gas_used: int, logs_emitted: int) -> None:
    """
    Record metrics for a single transaction execution.

    Args:
        result: logical outcome: {'success','revert','oog','error'} (flexibly normalized)
        kind:   tx kind: {'transfer','deploy','call',...} (normalized)
        gas_used: integer gas used (>= 0)
        logs_emitted: number of logs/events emitted (>= 0)
    """
    r = _norm_result(result)
    k = _norm_kind(kind)
    # Ensure registry & metrics are built
    _build_metrics()
    try:
        TX_APPLY_TOTAL.labels(result=r, kind=k).inc()
        if gas_used is not None and gas_used >= 0:
            TX_GAS_USED.labels(kind=k).observe(float(gas_used))
        if logs_emitted is not None and logs_emitted >= 0:
            TX_LOGS_EMITTED.labels(kind=k).observe(float(logs_emitted))
    except Exception:
        # Metrics must never crash the execution path.
        pass


@dataclass
class _TimerCtx:
    h: Histogram
    labels: Dict[str, str]
    t0: float

    def stop(self) -> float:
        dt = max(0.0, time.perf_counter() - self.t0)
        try:
            self.h.labels(**self.labels).observe(dt)
        except Exception:
            pass
        return dt

    # Context manager protocol
    def __enter__(self) -> "_TimerCtx":
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def time_block_apply(labels: Optional[Dict[str, str]] = None) -> _TimerCtx:
    """
    Context manager to time block application.

    Example:
        with time_block_apply():
            apply_block(block)

    Args:
        labels: optional label dict (ignored by default; reserved for future keys)
    """
    _build_metrics()
    return _TimerCtx(
        h=BLOCK_APPLY_SECONDS,
        labels=labels or {},
        t0=time.perf_counter(),
    )


# ------------------------------ exposition ----------------------------------

def generate_latest_text() -> bytes:
    """
    Return Prometheus exposition format for the current registry.
    Suitable for an ASGI/WSGI /metrics handler.
    """
    reg = get_registry()
    return generate_latest(reg)


__all__ = [
    "get_registry",
    "set_registry",
    "generate_latest_text",
    "observe_tx",
    "time_block_apply",
    # metric singletons (useful for advanced labeling)
    "TX_APPLY_TOTAL",
    "TX_GAS_USED",
    "TX_LOGS_EMITTED",
    "BLOCK_APPLY_SECONDS",
    # feature flag for optional import
    "_PROM_AVAILABLE",
    "CONTENT_TYPE_LATEST",
]
