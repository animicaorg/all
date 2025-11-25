"""
Prometheus metrics for the randomness beacon.

This module defines counters and histograms for the core pipeline:
  • commits  — incoming commitments per outcome
  • reveals  — reveal attempts per outcome
  • vdf_verify_seconds — time spent verifying VDF outputs
  • mix_entropy_bits   — observed entropy (in bits) of the final mix per round

Design notes
------------
- Label cardinality is intentionally low. We only expose an `outcome` label with a
  small, finite vocabulary for commit/reveal results.
- Avoid per-round labels for Gauges/Counters to keep metrics storage bounded.
- Prefer histograms (over gauges) for entropy so operators can build SLOs like
  "≥128 bits 99% of rounds" via histogram_quantile on the bits distribution.

Usage
-----
    from randomness.metrics import METRICS

    METRICS.record_commit("accepted")
    METRICS.record_reveal("bad_reveal")
    with METRICS.vdf_timer():
        verify_vdf(...)
    METRICS.observe_mix_entropy_bits(192.0)

If you need a custom Prometheus registry or different namespace/subsystem, construct
your own `Metrics` instance.
"""

from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterable, Optional

from prometheus_client import Counter, Histogram, REGISTRY


# --------- Vocabularies (kept small for bounded cardinality) ---------

_COMMIT_OUTCOMES = (
    "accepted",     # commit accepted for the round
    "too_late",     # arrived after commit window closed
    "duplicate",    # already have a commit from the same participant
    "invalid",      # malformed / failed basic validation
)

_REVEAL_OUTCOMES = (
    "accepted",     # reveal matched commitment and within window
    "too_early",    # reveal before window opens
    "too_late",     # reveal after window closes (if enforced)
    "bad_reveal",   # hash mismatch vs commitment / domain issues
    "invalid",      # malformed / failed validation
)

# --------- Default histogram buckets ---------

# VDF verification latency buckets (seconds): fine-grained sub-100ms up to 10s
_VDF_VERIFY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5,
    1.0, 2.5, 5.0, 10.0,
)

# Entropy bits distribution buckets: from 0 up to 256 bits
_ENTROPY_BITS_BUCKETS = (
    0.0, 1.0, 2.0, 4.0,
    8.0, 16.0, 32.0, 48.0,
    64.0, 96.0, 128.0, 160.0,
    192.0, 224.0, 256.0,
)


class Metrics:
    """
    Container for all randomness Prometheus instruments.

    Args:
        namespace: Prometheus metric namespace (prefix).
        subsystem: Prometheus metric subsystem (inserted between namespace and name).
        registry:  Prometheus registry to register the metrics with.
    """

    def __init__(
        self,
        *,
        namespace: str = "animica",
        subsystem: str = "randomness",
        registry = REGISTRY,
        vdf_buckets: Iterable[float] = _VDF_VERIFY_BUCKETS,
        entropy_buckets: Iterable[float] = _ENTROPY_BITS_BUCKETS,
    ) -> None:
        # Counters
        self.commits_total = Counter(
            "commits_total",
            "Number of commitment submissions processed, labeled by outcome.",
            labelnames=("outcome",),
            namespace=namespace,
            subsystem=subsystem,
            registry=registry,
        )
        self.reveals_total = Counter(
            "reveals_total",
            "Number of reveal attempts processed, labeled by outcome.",
            labelnames=("outcome",),
            namespace=namespace,
            subsystem=subsystem,
            registry=registry,
        )

        # Histograms
        self.vdf_verify_seconds = Histogram(
            "vdf_verify_seconds",
            "Time spent verifying VDF outputs (seconds).",
            buckets=tuple(vdf_buckets),
            namespace=namespace,
            subsystem=subsystem,
            registry=registry,
        )
        self.mix_entropy_bits = Histogram(
            "mix_entropy_bits",
            "Observed entropy of the mixed beacon output, in bits.",
            buckets=tuple(entropy_buckets),
            namespace=namespace,
            subsystem=subsystem,
            registry=registry,
        )

    # ----- Recording helpers -------------------------------------------------

    def record_commit(self, outcome: str) -> None:
        """
        Increment the commit counter for a specific outcome.

        Valid outcomes: one of _COMMIT_OUTCOMES.
        """
        if outcome not in _COMMIT_OUTCOMES:
            outcome = "invalid"
        self.commits_total.labels(outcome=outcome).inc()

    def record_reveal(self, outcome: str) -> None:
        """
        Increment the reveal counter for a specific outcome.

        Valid outcomes: one of _REVEAL_OUTCOMES.
        """
        if outcome not in _REVEAL_OUTCOMES:
            outcome = "invalid"
        self.reveals_total.labels(outcome=outcome).inc()

    def observe_vdf_verify(self, seconds: float) -> None:
        """Record a VDF verification duration in seconds."""
        self.vdf_verify_seconds.observe(float(seconds))

    def observe_mix_entropy_bits(self, bits: float) -> None:
        """Record the measured/estimated entropy (in bits) of the round's mixed output."""
        self.mix_entropy_bits.observe(float(bits))

    # ----- Context managers --------------------------------------------------

    @contextmanager
    def vdf_timer(self):
        """
        Context manager to time a VDF verification block.

            with METRICS.vdf_timer():
                verify_vdf(...)
        """
        start = perf_counter()
        try:
            yield
        finally:
            self.observe_vdf_verify(perf_counter() - start)


# Singleton used by most components
METRICS = Metrics()

__all__ = [
    "Metrics",
    "METRICS",
    "_COMMIT_OUTCOMES",
    "_REVEAL_OUTCOMES",
]
