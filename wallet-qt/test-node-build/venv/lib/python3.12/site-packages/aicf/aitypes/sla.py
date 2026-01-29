from __future__ import annotations

"""
SLA dimensions and simple evaluation helpers.

This module defines:
- SlaDims: measured dimensions for a provider/job (traps ratio, QoS, latency, availability).
- SlaThresholds: policy thresholds.
- SlaWeights: optional weights for computing a soft score in [0,1].
- SlaEvaluation: pass/fail with per-dimension violations and a composite score.

All values are pure/typed and free of IO, suitable for use inside the AICF SLA
evaluator and tests.
"""


from dataclasses import asdict, dataclass
from typing import Dict, Mapping, Optional

# ────────────────────────────────────────────────────────────────────────────────
# Types
# ────────────────────────────────────────────────────────────────────────────────


@dataclass
class SlaDims:
    """
    Measured SLA dimensions for a provider or a specific job/sample window.

    traps_ratio:   Fraction of trap-circuit checks passed (0.0..1.0).
    qos:           Composite QoS score (0.0..1.0). Combines e.g. output quality metrics.
    latency_ms:    End-to-end latency in milliseconds for the job/window.
    availability:  Uptime / successful response fraction (0.0..1.0).

    All ratios MUST be finite and within [0, 1]. latency_ms MUST be >= 0.
    """

    traps_ratio: float
    qos: float
    latency_ms: int
    availability: float

    def validate(self) -> None:
        for name, val in (
            ("traps_ratio", self.traps_ratio),
            ("qos", self.qos),
            ("availability", self.availability),
        ):
            if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
                raise ValueError(f"{name} must be a number in [0, 1]")
        if not isinstance(self.latency_ms, int) or self.latency_ms < 0:
            raise ValueError("latency_ms must be an integer >= 0")

    def to_dict(self) -> Dict[str, float]:
        self.validate()
        return {
            "traps_ratio": float(self.traps_ratio),
            "qos": float(self.qos),
            "latency_ms": int(self.latency_ms),
            "availability": float(self.availability),
        }

    @staticmethod
    def from_dict(d: Mapping[str, object]) -> "SlaDims":
        dims = SlaDims(
            traps_ratio=float(d.get("traps_ratio", 0.0)),
            qos=float(d.get("qos", 0.0)),
            latency_ms=int(d.get("latency_ms", 0)),
            availability=float(d.get("availability", 0.0)),
        )
        dims.validate()
        return dims


@dataclass
class SlaThresholds:
    """
    Policy thresholds. Passing requires:

      traps_ratio   >= min_traps_ratio
      qos           >= min_qos
      latency_ms    <= max_latency_ms
      availability  >= min_availability

    Defaults are intentionally conservative and should be overridden by network policy.
    """

    min_traps_ratio: float = 0.60
    min_qos: float = 0.80
    max_latency_ms: int = 30_000
    min_availability: float = 0.98

    def validate(self) -> None:
        for name, val in (
            ("min_traps_ratio", self.min_traps_ratio),
            ("min_qos", self.min_qos),
            ("min_availability", self.min_availability),
        ):
            if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
                raise ValueError(f"{name} must be a number in [0, 1]")
        if not isinstance(self.max_latency_ms, int) or self.max_latency_ms <= 0:
            raise ValueError("max_latency_ms must be an integer > 0")

    def to_dict(self) -> Dict[str, float]:
        self.validate()
        return asdict(self)


@dataclass
class SlaWeights:
    """
    Weights for composite score in [0,1]. All non-negative; normalized internally.

    A higher weight makes a dimension contribute more to the final soft score.
    """

    traps: float = 1.0
    qos: float = 1.0
    latency: float = 1.0
    availability: float = 1.0

    def validate(self) -> None:
        for name, val in (
            ("traps", self.traps),
            ("qos", self.qos),
            ("latency", self.latency),
            ("availability", self.availability),
        ):
            if not isinstance(val, (int, float)) or float(val) < 0.0:
                raise ValueError(f"{name} weight must be a non-negative number")

    def norm(self) -> "SlaWeights":
        self.validate()
        total = float(self.traps + self.qos + self.latency + self.availability)
        if total == 0.0:
            # default to equal weights if all zero
            return SlaWeights(1.0, 1.0, 1.0, 1.0)
        return SlaWeights(
            traps=float(self.traps) / total,
            qos=float(self.qos) / total,
            latency=float(self.latency) / total,
            availability=float(self.availability) / total,
        )


@dataclass
class SlaEvaluation:
    """
    Result of evaluating SlaDims against SlaThresholds.

    passed:      True if all hard thresholds pass.
    violations:  Map of dimension -> human message for any failed threshold.
    score:       Soft score in [0,1] (weighted), useful for ranking / trend windows.
    """

    passed: bool
    violations: Dict[str, str]
    score: float

    def to_dict(self) -> Dict[str, object]:
        return {
            "passed": bool(self.passed),
            "violations": dict(self.violations),
            "score": float(self.score),
        }


# ────────────────────────────────────────────────────────────────────────────────
# Evaluation helpers
# ────────────────────────────────────────────────────────────────────────────────


def evaluate_sla(
    dims: SlaDims,
    thresholds: SlaThresholds,
    weights: Optional = SlaWeights(1.0, 1.0, 1.0, 1.0),
) -> SlaEvaluation:
    """
    Evaluate hard pass/fail and compute a soft score in [0,1].

    Scoring (per-dimension, clamped to [0,1]):
      - traps:      0 at 0; 1 at >= min_traps_ratio; linear ramp between.
      - qos:        0 at 0; 1 at >= min_qos; linear ramp between.
      - latency:    1 at <= max_latency_ms; decays linearly to 0 at 1.5x max.
      - availability: 0 at 0; 1 at >= min_availability; linear ramp between.

    Note: The soft score is not used for hard acceptance; it is for ranking and
    trend analysis. The evaluator can apply additional statistics (e.g., EWMA,
    confidence bounds) outside this pure function.
    """
    dims.validate()
    thresholds.validate()
    w = weights.norm() if isinstance(weights, SlaWeights) else SlaWeights().norm()

    violations: Dict[str, str] = {}

    # Hard checks
    if dims.traps_ratio < thresholds.min_traps_ratio:
        violations["traps_ratio"] = (
            f"{dims.traps_ratio:.3f} < min {thresholds.min_traps_ratio:.3f}"
        )
    if dims.qos < thresholds.min_qos:
        violations["qos"] = f"{dims.qos:.3f} < min {thresholds.min_qos:.3f}"
    if dims.latency_ms > thresholds.max_latency_ms:
        violations["latency_ms"] = (
            f"{dims.latency_ms}ms > max {thresholds.max_latency_ms}ms"
        )
    if dims.availability < thresholds.min_availability:
        violations["availability"] = (
            f"{dims.availability:.3f} < min {thresholds.min_availability:.3f}"
        )

    passed = len(violations) == 0

    # Soft scores
    traps_score = _ramp_up(dims.traps_ratio, thresholds.min_traps_ratio)
    qos_score = _ramp_up(dims.qos, thresholds.min_qos)
    latency_cut = float(thresholds.max_latency_ms)
    latency_score = _ramp_down(dims.latency_ms, latency_cut, latency_cut * 1.5)
    avail_score = _ramp_up(dims.availability, thresholds.min_availability)

    score = (
        w.traps * traps_score
        + w.qos * qos_score
        + w.latency * latency_score
        + w.availability * avail_score
    )

    return SlaEvaluation(passed=passed, violations=violations, score=float(score))


# ────────────────────────────────────────────────────────────────────────────────
# Normalization helpers
# ────────────────────────────────────────────────────────────────────────────────


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _ramp_up(value: float, min_ok: float) -> float:
    """
    0 at 0; 1 at >= min_ok; linear between 0..min_ok.
    """
    if value <= 0.0:
        return 0.0
    if value >= min_ok:
        return 1.0
    if min_ok <= 0.0:
        return 1.0
    return _clamp01(value / min_ok)


def _ramp_down(value: float, ok_at_or_below: float, zero_at_or_above: float) -> float:
    """
    1 at <= ok_at_or_below; 0 at >= zero_at_or_above; linear between.
    """
    if value <= ok_at_or_below:
        return 1.0
    if value >= zero_at_or_above:
        return 0.0
    span = max(1e-9, zero_at_or_above - ok_at_or_below)
    return _clamp01(1.0 - ((value - ok_at_or_below) / span))


__all__ = [
    "SlaDims",
    "SlaThresholds",
    "SlaWeights",
    "SlaEvaluation",
    "evaluate_sla",
]
