"""
Animica | proofs.policy_adapter

Purpose
-------
Translate low-level, verifier-produced `ProofMetrics` into *normalized ψ-input
signals* expected by `consensus.scorer`. **No caps or weighting is applied here**;
this module only normalizes ranges and names so the scorer can apply the policy
from `spec/poies_policy.yaml`.

Design
------
- Each proof kind maps to a small, well-defined set of scalar signals.
- Signals are dimensionless (or clearly unit-annotated in the key name).
- Where a metric may be missing (None), we substitute safe neutral defaults.
- Normalization:
  * ratios clamped to [0,1]
  * counts/units floored at 0 (or 1 for redundancy)
  * booleans mapped to {0.0, 1.0}
- Returned structure is a dict with stable keys consumed by the scorer.

Signals by proof type
---------------------
HASH:
  - d_ratio: float   # difficulty-share ratio (>=0), already computed by verifier

AI:
  - units: float         # abstract AI units (>=0)
  - traps_ratio: float   # [0,1] fraction of trap prompts passed
  - qos: float           # [0,1] quality-of-service / SLO score
  - redundancy: float    # >=1 effective redundancy factor

QUANTUM:
  - units: float         # abstract quantum-compute units (>=0)
  - traps_ratio: float   # [0,1] fraction of trap circuits validated
  - qos: float           # [0,1] quality-of-service / reliability

STORAGE:
  - heartbeat: float         # {0.0,1.0} PoSt heartbeat present
  - retrieval_bonus: float   # {0.0,1.0} optional retrieval success ticket
  - qos: float               # [0,1] availability/latency composite

VDF:
  - seconds: float       # ≥0 seconds-equivalent delay (from verifier or heuristic)

Notes
-----
* This module has **no** dependency on consensus/ to avoid cycles.
* Keep names stable; scorer policy references them by string key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping

from .metrics import ProofMetrics
from .types import ProofEnvelope, ProofType

# ───────────────────────────── helpers ─────────────────────────────


def _clamp01(x: float | None) -> float:
    if x is None:
        return 0.0
    if x != x:  # NaN
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _floor0(x: float | int | None) -> float:
    if x is None:
        return 0.0
    try:
        v = float(x)
    except Exception:
        return 0.0
    return v if v >= 0.0 else 0.0


def _at_least_one(x: float | int | None) -> float:
    v = _floor0(x)
    return v if v >= 1.0 else 1.0


def _bool01(flag: bool | None) -> float:
    return 1.0 if bool(flag) else 0.0


# ────────────────────────── public mapping ─────────────────────────


def metrics_to_signals(proof_type: ProofType, m: ProofMetrics) -> Dict[str, float]:
    """
    Map a single proof's metrics → normalized ψ-input signal dict.

    This function performs **no** policy weighting nor caps; it only normalizes.
    """
    # HASH share
    if proof_type == ProofType.HASH:
        return {
            "d_ratio": _floor0(m.d_ratio),
        }

    # AI compute
    if proof_type == ProofType.AI:
        return {
            "units": _floor0(m.ai_units),
            "traps_ratio": _clamp01(m.traps_ratio),
            "qos": _clamp01(m.qos),
            "redundancy": _at_least_one(m.redundancy),
        }

    # QUANTUM compute
    if proof_type == ProofType.QUANTUM:
        # Metric name for units may be shared with AI; accept either.
        units = getattr(m, "quantum_units", None)
        if units is None:
            units = m.ai_units  # fall back to shared units field if used
        return {
            "units": _floor0(units),
            "traps_ratio": _clamp01(m.traps_ratio),
            "qos": _clamp01(m.qos),
        }

    # STORAGE heartbeat
    if proof_type == ProofType.STORAGE:
        return {
            "heartbeat": _bool01(getattr(m, "storage_ok", None)),
            "retrieval_bonus": _bool01(getattr(m, "retrieval_bonus", None)),
            "qos": _clamp01(m.qos),
        }

    # VDF delay
    if proof_type == ProofType.VDF:
        return {
            "seconds": _floor0(m.vdf_seconds),
        }

    # Unknown type → empty (scorer should ignore or raise)
    return {}


@dataclass(frozen=True)
class PsiInput:
    """
    A typed wrapper for a single proof's ψ-input signals.

    Fields:
      - type_id: ProofType
      - signals: Mapping[str, float] normalized by `metrics_to_signals`
    """

    type_id: ProofType
    signals: Mapping[str, float]


def envelope_to_psi_input(env: ProofEnvelope, metrics: ProofMetrics) -> PsiInput:
    """
    Convenience: map (envelope, verified metrics) → PsiInput.
    """
    sigs = metrics_to_signals(env.type_id, metrics)
    return PsiInput(type_id=env.type_id, signals=sigs)


__all__ = [
    "PsiInput",
    "metrics_to_signals",
    "envelope_to_psi_input",
]
