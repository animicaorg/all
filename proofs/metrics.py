"""
Per-proof measurable metrics used by PoIES scoring.

This module defines a small, type-tagged metrics container and helpers to compute
derived measures (e.g., traps ratio) from verified proof bodies. These metrics are
_consensus inputs_ for PoIES (after caps/policy mapping happens elsewhere).

Design notes
- We keep this file free of policy/capping logic. Mapping → ψ inputs is done in
  proofs/policy_adapter.py and scoring/capping is in consensus/.
- Verifier modules (hashshare.py, ai.py, quantum.py, storage.py, vdf.py) should call
  the builder functions here to produce a ProofMetrics for each accepted envelope.
- For things that require environment context (e.g., a “difficulty ratio” computed
  against the current Θ/target, or VDF quality against a reference), the helpers
  accept explicit parameters so the caller provides the context used during verify.

Key fields
- d_ratio:       share difficulty ratio vs current micro-target (≥0). 1.0 ≈ on-target.
- traps_ratio:   traps_passed / traps_total (∈[0,1]); None if no traps in that proof.
- ai_units:      abstract AI work units (dimensionless, ≥0).
- quantum_units: abstract Quantum work units (dimensionless, ≥0).
- redundancy:    integer ≥1 when replicated computation was required/observed.
- qos_ms:        end-to-end latency in milliseconds (provider → proof receipt).
- size_bytes:    storage size covered by a heartbeat/proof (≥0).
- vdf_seconds:   prover-reported wall seconds (advisory; verified via iterations).
- vdf_iterations:number of VDF iterations (consensus-critical).
- vdf_quality:   optional normalized quality, e.g., seconds / (iterations/ref_rate).

All floats are bounded to finite values; ratios are clamped to [0, +∞) or [0,1] as appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Dict, Any

from .types import (
    ProofType,
    ProofEnvelope,
    HashShareBody,
    AIProofBody,
    QuantumProofBody,
    StorageHeartbeatBody,
    VDFProofBody,
    Bytes32,
)

# -------- small numeric helpers ------------------------------------------------

def _finite_nonneg(x: float) -> float:
    if x != x or x == float("inf") or x == float("-inf"):
        raise ValueError("non-finite metric")
    return 0.0 if x < 0.0 else x

def _ratio01(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    r = num / den
    if r != r:  # NaN
        return None
    if r < 0.0:
        return 0.0
    if r > 1.0:
        return 1.0
    return r

# -------- metrics container ----------------------------------------------------

@dataclass(frozen=True)
class ProofMetrics:
    """
    Type-tagged metrics emitted by verifiers and consumed by PoIES mapping.

    Fields are optional where not applicable to a proof type. The (kind,nullifier)
    pair identifies the source envelope for debugging/tracing.
    """
    kind: ProofType
    nullifier: Bytes32

    # Hash share
    d_ratio: Optional[float] = None  # ≥0.0

    # AI
    ai_units: Optional[int] = None           # ≥0
    traps_ratio: Optional[float] = None      # ∈[0,1]
    redundancy: Optional[int] = None         # ≥1
    qos_ms: Optional[int] = None             # ≥0

    # Quantum
    quantum_units: Optional[int] = None      # ≥0

    # Storage
    size_bytes: Optional[int] = None         # ≥0

    # VDF
    vdf_seconds: Optional[int] = None        # ≥0
    vdf_iterations: Optional[int] = None     # ≥0
    vdf_quality: Optional[float] = None      # ≥0 (contextual)

    def to_dict(self) -> Dict[str, Any]:
        """Plain dict suitable for JSON/CBOR; omits None fields."""
        out = {
            "kind": int(self.kind),
            "nullifier": bytes(self.nullifier).hex(),
        }
        for k, v in (
            ("d_ratio", self.d_ratio),
            ("ai_units", self.ai_units),
            ("traps_ratio", self.traps_ratio),
            ("redundancy", self.redundancy),
            ("qos_ms", self.qos_ms),
            ("quantum_units", self.quantum_units),
            ("size_bytes", self.size_bytes),
            ("vdf_seconds", self.vdf_seconds),
            ("vdf_iterations", self.vdf_iterations),
            ("vdf_quality", self.vdf_quality),
        ):
            if v is not None:
                out[k] = v
        return out

    def ensure_bounds(self) -> "ProofMetrics":
        """
        Returns a copy with numeric sanity applied (non-negativity, finite, ratios clamped).
        Callers may use this defensively after constructing a metrics object.
        """
        m = self
        if m.d_ratio is not None:
            m = replace(m, d_ratio=_finite_nonneg(m.d_ratio))
        if m.traps_ratio is not None:
            # clamp to [0,1]
            tr = m.traps_ratio
            if tr != tr or tr == float("inf") or tr == float("-inf"):
                tr = 0.0
            tr = 0.0 if tr < 0.0 else (1.0 if tr > 1.0 else tr)
            m = replace(m, traps_ratio=tr)
        if m.ai_units is not None and m.ai_units < 0:
            m = replace(m, ai_units=0)
        if m.quantum_units is not None and m.quantum_units < 0:
            m = replace(m, quantum_units=0)
        if m.redundancy is not None and m.redundancy < 1:
            m = replace(m, redundancy=1)
        if m.qos_ms is not None and m.qos_ms < 0:
            m = replace(m, qos_ms=0)
        if m.size_bytes is not None and m.size_bytes < 0:
            m = replace(m, size_bytes=0)
        if m.vdf_seconds is not None and m.vdf_seconds < 0:
            m = replace(m, vdf_seconds=0)
        if m.vdf_iterations is not None and m.vdf_iterations < 0:
            m = replace(m, vdf_iterations=0)
        if m.vdf_quality is not None:
            m = replace(m, vdf_quality=_finite_nonneg(m.vdf_quality))
        return m


# -------- builders for each proof kind ----------------------------------------

def metrics_hashshare(env: ProofEnvelope, *, d_ratio: float) -> ProofMetrics:
    """
    Build metrics for a HashShare envelope.

    Parameters
    - d_ratio: achieved_share_difficulty / current_target_difficulty
               This MUST be computed by the verifier with the exact same target used
               for acceptance checks (fractional micro-target). Typical values ~1.0.
    """
    if env.type_id != ProofType.HASH_SHARE or not isinstance(env.body, HashShareBody):
        raise TypeError("metrics_hashshare requires a HashShare envelope")
    return ProofMetrics(
        kind=env.type_id,
        nullifier=env.nullifier,
        d_ratio=_finite_nonneg(d_ratio),
    )


def metrics_ai(env: ProofEnvelope) -> ProofMetrics:
    """
    Build metrics for an AI proof envelope. traps_ratio is derived. ai_units/qos_ms/redundancy
    are taken directly from the verified body.
    """
    if env.type_id != ProofType.AI or not isinstance(env.body, AIProofBody):
        raise TypeError("metrics_ai requires an AI envelope")
    b = env.body
    tr = _ratio01(b.traps_passed, b.traps_total)
    return ProofMetrics(
        kind=env.type_id,
        nullifier=env.nullifier,
        ai_units=max(0, int(b.ai_units)),
        traps_ratio=tr,
        redundancy=max(1, int(b.redundancy)),
        qos_ms=max(0, int(b.qos_ms)),
    )


def metrics_quantum(env: ProofEnvelope) -> ProofMetrics:
    """
    Build metrics for a Quantum proof envelope. traps_ratio is derived. quantum_units/qos_ms
    are taken directly from the verified body.
    """
    if env.type_id != ProofType.QUANTUM or not isinstance(env.body, QuantumProofBody):
        raise TypeError("metrics_quantum requires a Quantum envelope")
    b = env.body
    tr = _ratio01(b.traps_passed, b.traps_total)
    return ProofMetrics(
        kind=env.type_id,
        nullifier=env.nullifier,
        quantum_units=max(0, int(b.quantum_units)),
        traps_ratio=tr,
        redundancy=None,  # quantum redundancy is accounted implicitly by traps/units unless policy says otherwise
        qos_ms=max(0, int(b.qos_ms)),
    )


def metrics_storage(env: ProofEnvelope) -> ProofMetrics:
    """
    Build metrics for a Storage heartbeat envelope.
    """
    if env.type_id != ProofType.STORAGE or not isinstance(env.body, StorageHeartbeatBody):
        raise TypeError("metrics_storage requires a Storage envelope")
    b = env.body
    return ProofMetrics(
        kind=env.type_id,
        nullifier=env.nullifier,
        size_bytes=max(0, int(b.size_bytes)),
        qos_ms=max(0, int(b.qos_ms)),
    )


def metrics_vdf(
    env: ProofEnvelope,
    *,
    ref_iters_per_sec: Optional[float] = None,
) -> ProofMetrics:
    """
    Build metrics for a VDF proof envelope.

    Parameters
    - ref_iters_per_sec: if provided (>0), compute vdf_quality := vdf_seconds / (iterations / ref_iters_per_sec).
      Intuition: values ≈1 mean the prover ran near the reference speed; >1 slower; <1 faster.

    The VDF verifier should have already checked (input_digest, y, pi, iterations).
    """
    if env.type_id != ProofType.VDF or not isinstance(env.body, VDFProofBody):
        raise TypeError("metrics_vdf requires a VDF envelope")
    b = env.body
    qual: Optional[float] = None
    if ref_iters_per_sec and ref_iters_per_sec > 0 and b.iterations > 0:
        expected_secs = b.iterations / float(ref_iters_per_sec)
        if expected_secs > 0:
            qual = b.seconds / expected_secs
            qual = _finite_nonneg(qual)
    return ProofMetrics(
        kind=env.type_id,
        nullifier=env.nullifier,
        vdf_seconds=max(0, int(b.seconds)),
        vdf_iterations=max(0, int(b.iterations)),
        vdf_quality=qual,
    )


# -------- generic dispatcher ---------------------------------------------------

def build_metrics(env: ProofEnvelope, **context: Any) -> ProofMetrics:
    """
    Convenience dispatcher that builds metrics for any envelope.

    Context kwargs (optional):
      - d_ratio: float                    (required for HASH_SHARE)
      - ref_iters_per_sec: float         (optional for VDF)
    """
    if env.type_id == ProofType.HASH_SHARE:
        if "d_ratio" not in context:
            raise ValueError("build_metrics(HashShare): missing d_ratio context")
        return metrics_hashshare(env, d_ratio=float(context["d_ratio"])).ensure_bounds()
    if env.type_id == ProofType.AI:
        return metrics_ai(env).ensure_bounds()
    if env.type_id == ProofType.QUANTUM:
        return metrics_quantum(env).ensure_bounds()
    if env.type_id == ProofType.STORAGE:
        return metrics_storage(env).ensure_bounds()
    if env.type_id == ProofType.VDF:
        return metrics_vdf(env, ref_iters_per_sec=context.get("ref_iters_per_sec")).ensure_bounds()
    raise ValueError(f"Unknown proof type: {env.type_id}")


__all__ = [
    "ProofMetrics",
    "metrics_hashshare",
    "metrics_ai",
    "metrics_quantum",
    "metrics_storage",
    "metrics_vdf",
    "build_metrics",
]
