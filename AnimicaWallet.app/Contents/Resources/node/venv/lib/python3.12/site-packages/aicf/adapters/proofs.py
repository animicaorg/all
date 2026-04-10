from __future__ import annotations

"""
aicf.adapters.proofs
====================

Parse *on-chain proof envelopes* (AI / Quantum) into normalized **work metrics**
for SLA evaluation and pricing.

This adapter is intentionally dependency-light. It accepts a flexible, vendor-
agnostic "envelope" (already verified by the consensus layer) and produces a
typed `ProofMetrics` object with:

- `units`: normalized billable units (inputs for pricing)
- `traps_ratio`: passed / total traps (SLA soundness)
- `qos`: throughput or quality indicator (SLA; AI uses tokens/sec if available)
- `latency_ms`: end-to-end latency in milliseconds (SLA timeliness)
- `details`: extra parsed fields preserved for debugging/audits

Design notes
------------
* We first look for explicit `units` in the envelope; if absent we derive:
  - AI: ceil((input_tokens + output_tokens) / AI_TOKENS_PER_UNIT)
  - Quantum: ceil((depth * width * shots) / Q_GATE_SHOTS_PER_UNIT)
* We remain conservative and clamp all counters to non-negative integers.
* The adapter does not re-verify attestations; it assumes consensus has done so
  and merely extracts metrics for AICF economics/SLA. A separate attestation
  normalization path (e.g., capabilities.jobs.attest_bridge) should feed the
  envelope shapes used here.

Typical AI envelope fields (tolerated aliases):
{
  "kind": "ai",
  "usage": {"input_tokens": 1234, "output_tokens": 2048},
  "latency_ms": 512,            # or {"total_ms": ...}
  "duration_ms": 480,           # model exec time (optional)
  "qos": {"tokens_per_sec": 420.0},   # optional QoS
  "traps": {"passed": 19, "total": 20}
  # optional explicit "units": 4
}

Typical Quantum envelope fields:
{
  "kind": "quantum",
  "circuit": {"depth": 50, "width": 64, "shots": 2000},
  "latency_ms": 900,
  "traps": {"passed": 97, "total": 100}
  # optional explicit "units": 7
}

The adapter is robust to missing sub-objects; absent metrics will be None.
"""

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from aicf.errors import AICFError

# Scaling constants (devnet-friendly defaults; can be tuned via policy later)
AI_TOKENS_PER_UNIT = 1_000  # 1k tokens ~ 1 ai_unit
Q_GATE_SHOTS_PER_UNIT = 1_000  # 1k (depth*width*shots) ~ 1 quantum_unit

Kind = Literal["ai", "quantum"]


# ---- datatypes ----------------------------------------------------------------


@dataclass(frozen=True)
class ProofMetrics:
    """Common, normalized metrics consumed by pricing & SLA engines."""

    kind: Kind
    units: int
    traps_ratio: Optional[float]
    qos: Optional[float]  # AI: tokens/sec; Quantum: provider-reported QoS (if any)
    latency_ms: Optional[int]
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Keep floats as floats; consumers downstream may serialize to JSON/CBOR.
        return d


# ---- helpers ------------------------------------------------------------------


def _as_int(x: Any, default: int = 0) -> int:
    try:
        v = int(x)
        return v if v >= 0 else 0
    except Exception:
        return default


def _as_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return default


def _clamp_nonneg(v: Optional[int]) -> Optional[int]:
    if v is None:
        return None
    return v if v >= 0 else 0


def _extract(obj: Mapping[str, Any], *path: str) -> Any:
    """Best-effort nested getter."""
    cur: Any = obj
    for p in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(p)
    return cur


def _traps_ratio(env: Mapping[str, Any]) -> Optional[float]:
    traps = _extract(env, "traps")
    if not isinstance(traps, Mapping):
        # tolerate alternative nesting
        t_pass = _extract(env, "traps_passed")
        t_total = _extract(env, "traps_total")
        passed = _as_int(t_pass, 0)
        total = _as_int(t_total, 0)
    else:
        passed = _as_int(traps.get("passed"), 0)
        total = _as_int(traps.get("total"), 0)
    if total <= 0:
        return None
    # bound to [0,1]
    r = max(0.0, min(1.0, passed / float(total)))
    return r


def _latency_ms(env: Mapping[str, Any]) -> Optional[int]:
    # common shapes: "latency_ms" or {"latency_ms":...} or {"latency":{"total_ms":...}}
    lat = _extract(env, "latency_ms")
    if lat is None and isinstance(_extract(env, "latency"), Mapping):
        lat = _extract(env, "latency", "total_ms")
    if lat is None:
        lat = _extract(env, "duration_ms")  # better-than-nothing fallback
    v = _as_int(lat, 0)
    return _clamp_nonneg(v) if v else None


def _ai_units(env: Mapping[str, Any]) -> int:
    # explicit override
    u = _extract(env, "units")
    if u is not None:
        return max(0, int(u))
    usage = _extract(env, "usage")
    if isinstance(usage, Mapping):
        it = _as_int(usage.get("input_tokens"), 0)
        ot = _as_int(usage.get("output_tokens"), 0)
    else:
        it = _as_int(_extract(env, "input_tokens"), 0)
        ot = _as_int(_extract(env, "output_tokens"), 0)
    total = it + ot
    return max(0, math.ceil(total / AI_TOKENS_PER_UNIT))  # ceil to billable unit


def _ai_qos(env: Mapping[str, Any]) -> Optional[float]:
    qos = _extract(env, "qos")
    if isinstance(qos, Mapping):
        tps = _as_float(qos.get("tokens_per_sec"))
        if tps is not None:
            return max(0.0, tps)
    # derive from output_tokens / duration_s if available
    usage = (
        _extract(env, "usage") if isinstance(_extract(env, "usage"), Mapping) else None
    )
    ot = (
        _as_int(usage.get("output_tokens"), 0)
        if isinstance(usage, Mapping)
        else _as_int(_extract(env, "output_tokens"), 0)
    )
    dur_ms = _as_int(_extract(env, "duration_ms"), 0)
    if ot > 0 and dur_ms > 0:
        return max(0.0, ot / (dur_ms / 1000.0))
    return None


def _quantum_units(env: Mapping[str, Any]) -> int:
    u = _extract(env, "units")
    if u is not None:
        return max(0, int(u))
    circ = _extract(env, "circuit")
    if isinstance(circ, Mapping):
        depth = _as_int(circ.get("depth"), 0)
        width = _as_int(circ.get("width"), 0)
        shots = _as_int(circ.get("shots"), 0)
    else:
        depth = _as_int(_extract(env, "depth"), 0)
        width = _as_int(_extract(env, "width"), 0)
        shots = _as_int(_extract(env, "shots"), 0)
    gate_shots = depth * width * shots
    return max(0, math.ceil(gate_shots / Q_GATE_SHOTS_PER_UNIT))


# ---- public API ----------------------------------------------------------------


def extract_ai_metrics(envelope: Mapping[str, Any]) -> ProofMetrics:
    """
    Extract metrics from an AI proof envelope.

    Returns:
        ProofMetrics(kind="ai", ...)
    Raises:
        AICFError if the envelope is obviously malformed.
    """
    if not isinstance(envelope, Mapping):
        raise AICFError("AI proof envelope must be a mapping")

    units = _ai_units(envelope)
    traps = _traps_ratio(envelope)
    qos = _ai_qos(envelope)
    latency = _latency_ms(envelope)

    # Collect details for auditing
    usage = _extract(envelope, "usage")
    details: Dict[str, Any] = {
        "input_tokens": (
            _as_int(usage.get("input_tokens"), 0)
            if isinstance(usage, Mapping)
            else _as_int(_extract(envelope, "input_tokens"), 0)
        ),
        "output_tokens": (
            _as_int(usage.get("output_tokens"), 0)
            if isinstance(usage, Mapping)
            else _as_int(_extract(envelope, "output_tokens"), 0)
        ),
        "duration_ms": _as_int(_extract(envelope, "duration_ms"), 0),
        "latency_ms_raw": _as_int(_extract(envelope, "latency_ms"), 0),
        "qos_tokens_per_sec": qos,
        "traps_ratio": traps,
    }

    return ProofMetrics(
        kind="ai",
        units=units,
        traps_ratio=traps,
        qos=qos,
        latency_ms=latency,
        details=details,
    )


def extract_quantum_metrics(envelope: Mapping[str, Any]) -> ProofMetrics:
    """
    Extract metrics from a Quantum proof envelope.

    Returns:
        ProofMetrics(kind="quantum", ...)
    Raises:
        AICFError if the envelope is obviously malformed.
    """
    if not isinstance(envelope, Mapping):
        raise AICFError("Quantum proof envelope must be a mapping")

    units = _quantum_units(envelope)
    traps = _traps_ratio(envelope)
    latency = _latency_ms(envelope)

    circ = _extract(envelope, "circuit")
    if isinstance(circ, Mapping):
        depth = _as_int(circ.get("depth"), 0)
        width = _as_int(circ.get("width"), 0)
        shots = _as_int(circ.get("shots"), 0)
    else:
        depth = _as_int(_extract(envelope, "depth"), 0)
        width = _as_int(_extract(envelope, "width"), 0)
        shots = _as_int(_extract(envelope, "shots"), 0)

    # Optional provider-reported QoS (e.g., fidelity). Keep as float if present.
    qos = _as_float(_extract(envelope, "qos"))  # if qos is scalar for quantum

    details: Dict[str, Any] = {
        "depth": depth,
        "width": width,
        "shots": shots,
        "gate_shots": depth * width * shots,
        "traps_ratio": traps,
    }

    return ProofMetrics(
        kind="quantum",
        units=units,
        traps_ratio=traps,
        qos=qos,
        latency_ms=latency,
        details=details,
    )


def extract_metrics(kind: Kind, envelope: Mapping[str, Any]) -> ProofMetrics:
    """
    Unified entrypoint used by pricing/SLA code-paths.
    """
    k = (kind or _extract(envelope, "kind") or "").lower()
    if k not in ("ai", "quantum"):
        raise AICFError(f"unknown proof kind {kind!r}")
    return (
        extract_ai_metrics(envelope) if k == "ai" else extract_quantum_metrics(envelope)
    )


# Optional convenience if callers pass the entire on-chain proof object
def from_onchain_proof(proof: Mapping[str, Any]) -> ProofMetrics:
    """
    Convenience wrapper: try to detect kind from the proof wrapper and extract
    the nested envelope. Tolerates shapes like:
      {"AIProof": {...}} or {"kind":"ai","envelope":{...}}
    """
    if not isinstance(proof, Mapping):
        raise AICFError("proof must be a mapping")
    if "AIProof" in proof:
        env = proof.get("AIProof")  # already an envelope
        if not isinstance(env, Mapping):
            raise AICFError("AIProof is not a mapping")
        return extract_ai_metrics(env)
    if "QuantumProof" in proof:
        env = proof.get("QuantumProof")
        if not isinstance(env, Mapping):
            raise AICFError("QuantumProof is not a mapping")
        return extract_quantum_metrics(env)
    # generic wrapper
    env = proof.get("envelope", {})
    kind = (proof.get("kind") or _extract(env, "kind") or "").lower()
    if kind not in ("ai", "quantum"):
        raise AICFError("cannot determine proof kind")
    return extract_metrics(kind, env if isinstance(env, Mapping) else proof)


__all__ = [
    "ProofMetrics",
    "AI_TOKENS_PER_UNIT",
    "Q_GATE_SHOTS_PER_UNIT",
    "extract_ai_metrics",
    "extract_quantum_metrics",
    "extract_metrics",
    "from_onchain_proof",
]
