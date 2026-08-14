"""L2 metrics facade — Prometheus when available, a no-op shim otherwise.

Mirrors the project convention (see ``mempool/metrics.py``): a single exported
facade instance ``L2_METRICS`` with verb helpers, so call sites never branch on
whether prometheus_client is installed. Metric names follow the spec's section 33
(``l2_*``) and the repo's ``animica_l2_*`` Prometheus naming.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Dict, Optional

try:  # pragma: no cover - dep gate
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    _PROM = True
except Exception:  # pragma: no cover
    _PROM = False


# Counter/gauge/histogram names. Kept explicit so a typo in a call site fails
# loudly (KeyError) rather than silently creating a new series.
_COUNTERS = {
    "sig_verifications_total": "animica_l2_signature_verifications_total",
    "sig_cache_hit": "animica_l2_signature_cache_hits_total",
    "sig_native_selftest_failed": "animica_l2_signature_native_selftest_failed_total",
    "ingress_total": "animica_l2_ingress_transactions_total",
    "validated_total": "animica_l2_validated_transactions_total",
    "executed_total": "animica_l2_executed_transactions_total",
    "soft_confirmed_total": "animica_l2_soft_confirmed_transactions_total",
    "settled_total": "animica_l2_settled_transactions_total",
    "reverted_total": "animica_l2_reverted_transactions_total",
    "rejected_total": "animica_l2_rejected_transactions_total",
    "batches_total": "animica_l2_batches_total",
    "proofs_generated_total": "animica_l2_proofs_generated_total",
    "forced_transactions_total": "animica_l2_forced_transactions_total",
    "forced_withdrawals_total": "animica_l2_forced_withdrawals_total",
    "deposits_credited_total": "animica_l2_deposits_credited_total",
    "withdrawals_finalized_total": "animica_l2_withdrawals_finalized_total",
    "merkle_updates_total": "animica_l2_merkle_updates_total",
}

_GAUGES = {
    "mempool_size": "animica_l2_mempool_size",
    "ingress_queue_depth": "animica_l2_ingress_queue_depth",
    "execution_queue_depth": "animica_l2_execution_queue_depth",
    "proof_queue_depth": "animica_l2_proof_queue_depth",
    "batch_transactions": "animica_l2_batch_transactions",
    "batch_bytes": "animica_l2_batch_bytes",
    "batch_compressed_bytes": "animica_l2_batch_compressed_bytes",
    "anm_locked": "animica_l2_anm_locked_nanos",
    "anm_l2_supply": "animica_l2_supply_nanos",
    "head_batch": "animica_l2_head_batch_number",
    "finalized_batch": "animica_l2_finalized_batch_number",
    "ingress_tps": "animica_l2_ingress_tps",
    "executed_tps": "animica_l2_executed_tps",
    "soft_confirmed_tps": "animica_l2_soft_confirmed_tps",
    "settled_tps": "animica_l2_settled_tps",
}

_HISTS = {
    "sig_verify_seconds": "animica_l2_signature_verification_seconds",
    "sig_batch_seconds": "animica_l2_signature_batch_seconds",
    "state_commit_seconds": "animica_l2_state_commit_seconds",
    "execution_seconds": "animica_l2_execution_seconds",
    "proof_generation_seconds": "animica_l2_proof_generation_seconds",
    "soft_confirmation_latency": "animica_l2_soft_confirmation_latency_seconds",
    "batch_latency": "animica_l2_batch_latency_seconds",
    "l1_finality_latency": "animica_l2_l1_finality_latency_seconds",
}


class _NoopTimer:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Metrics:
    def __init__(self) -> None:
        self._enabled = _PROM
        self._counters: Dict[str, object] = {}
        self._gauges: Dict[str, object] = {}
        self._hists: Dict[str, object] = {}
        # Plain-Python mirrors so metrics are queryable (l2_getMetrics /
        # l2_getTPS) even when prometheus_client is absent.
        self._cvals: Dict[str, int] = {k: 0 for k in _COUNTERS}
        self._gvals: Dict[str, float] = {k: 0.0 for k in _GAUGES}
        if self._enabled:
            self._reg = CollectorRegistry()
            for key, name in _COUNTERS.items():
                self._counters[key] = Counter(name, name, registry=self._reg)
            for key, name in _GAUGES.items():
                self._gauges[key] = Gauge(name, name, registry=self._reg)
            for key, name in _HISTS.items():
                self._hists[key] = Histogram(name, name, registry=self._reg)

    # ── verbs ──
    def inc(self, key: str, amount: int = 1) -> None:
        self._cvals[key] = self._cvals.get(key, 0) + amount
        if self._enabled:
            self._counters[key].inc(amount)

    def note(self, key: str) -> None:
        self.inc(key, 1)

    def set(self, key: str, value: float) -> None:
        self._gvals[key] = float(value)
        if self._enabled:
            self._gauges[key].set(value)

    @contextmanager
    def time(self, key: str):
        if not self._enabled:
            yield _NoopTimer()
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._hists[key].observe(time.perf_counter() - t0)

    def counter_value(self, key: str) -> int:
        return self._cvals.get(key, 0)

    def gauge_value(self, key: str) -> float:
        return self._gvals.get(key, 0.0)

    def snapshot(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k in _COUNTERS:
            out[k] = float(self._cvals.get(k, 0))
        for k in _GAUGES:
            out[k] = float(self._gvals.get(k, 0.0))
        return out

    @property
    def registry(self) -> Optional[object]:
        return getattr(self, "_reg", None) if self._enabled else None


L2_METRICS = _Metrics()
