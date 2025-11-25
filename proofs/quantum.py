"""
Animica | proofs.quantum

QuantumProof v1 verification:
- Verifies provider identity/attestation (X.509/EdDSA/PQ-hybrid per policy).
- Verifies trap-circuit section: commit→reveal seed, Merkle root of trap outcomes, success ratio.
- Computes normalized quantum_units from (depth × width × shots) via reference benchmarks
  or accepts an explicitly provided quantumUnits field (policy may cap/scale).
- Emits ProofMetrics(quantum_units, traps_ratio, qos).

Body shape (validated against proofs/schemas/quantum_attestation.schema.json):

{
  "provider": {                 # provider identity/attestation bundle
    "certChain": [...],         # X.509-like JSON or COSE; PQ-hybrid accepted via policy
    "endorsedAlgs": ["..."],    # declared supported algorithms/kernels
    ? "policy": { ... }         # optional allowlist/region flags to enforce
  },

  "job": {                      # deterministic linkage to contract/job
    "taskId": bstr .size 32,
    "circuitDigest": bstr .size 32,   # digest of the user circuit
    "resultDigest": bstr .size 32,    # digest of the returned results (shots histogram etc.)
    "depth": uint,
    "width": uint,
    "shots": uint,
    ? "quantumUnits": uint             # optionally pre-normalized units
  },

  "traps": {                    # trap circuits mixed with the batch
    "seedCommit": bstr .size 32,       # H(seedReveal)
    "seedReveal": bstr .size 32,
    "receipts": [
      { "trapDigest": bstr .size 32,
        "count": uint,                 # number of trap shots evaluated for this circuit
        "ok": bool }                   # all checks consistent for this trap circuit
    ],
    "root": bstr .size 32
  },

  "qos": {                      # provider QoS snapshot for this job window
    "latencyMsP95": uint,
    "successPermil": uint,             # 0..1000
    "uptimePermil": uint               # 0..1000
  }
}

Returned metrics (ProofMetrics):
- quantum_units:     int (≥0) — normalized compute units
- traps_ratio:       float in [0,1]
- qos:               float in [0,1]
(Other ProofMetrics fields are None for Quantum proof.)
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .errors import ProofError, AttestationError, SchemaError
from .types import ProofType, ProofEnvelope
from .metrics import ProofMetrics
from .cbor import validate_body
from .utils.hash import sha3_256
from .utils.math import clamp01

# Attestation & benchmarking helpers (implemented in module-local subpackages)
from .quantum_attest.provider_cert import verify_provider_cert  # -> (ok: bool, info: dict)
from .quantum_attest.benchmarks import units_for  # -> int units from (depth,width,shots)

# Optional: if traps helpers exist, we use them; else we fall back to a local implementation.
try:
    from .quantum_attest.traps import confidence_lower_bound  # Wilson/Clopper-Pearson LB
except Exception:  # pragma: no cover
    def confidence_lower_bound(ok: int, total: int, alpha: float = 0.05) -> float:
        """Wilson score lower bound for a Bernoulli proportion (fallback)."""
        import math
        if total <= 0:
            return 0.0
        z = 1.959963984540054 if alpha == 0.05 else abs(float(alpha))  # crude; callers may pass z directly
        phat = ok / total
        denom = 1 + z**2 / total
        center = phat + z*z/(2*total)
        rad = z * ((phat*(1 - phat) + z*z/(4*total)) / total) ** 0.5
        return max(0.0, (center - rad) / denom)


# ─────────────────────────────── domains & helpers ───────────────────────────────

_TRAP_ITEM_DOMAIN = b"Animica/QTrapItem/v1"
_TRAP_ROOT_DOMAIN = b"Animica/QTrapRoot/v1"


def _merkle_root(leaves: List[bytes]) -> bytes:
    """
    Canonical SHA3-256 Merkle root with domain separation:
      - leaf = sha3_256(_TRAP_ITEM_DOMAIN || raw_leaf)
      - node = sha3_256(_TRAP_ROOT_DOMAIN || left || right)
    Odd node duplication to pair, Bitcoin-style. Empty → sha3_256(_TRAP_ROOT_DOMAIN).
    """
    if not leaves:
        return sha3_256(_TRAP_ROOT_DOMAIN)
    level = [sha3_256(_TRAP_ITEM_DOMAIN + leaf) for leaf in leaves]
    while len(level) > 1:
        nxt: List[bytes] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i if i + 1 >= len(level) else i + 1]
            nxt.append(sha3_256(_TRAP_ROOT_DOMAIN + left + right))
        level = nxt
    return level[0]


def _trap_item_bytes(trap_digest: bytes, count: int, ok: bool) -> bytes:
    if len(trap_digest) != 32:
        raise SchemaError("trapDigest must be 32 bytes")
    if count < 0:
        raise SchemaError("trap count must be non-negative")
    # trap leaf = trapDigest || uvarint(count) || okByte
    # For simplicity, encode count as 8-byte big-endian (deterministic).
    return trap_digest + count.to_bytes(8, "big") + (b"\x01" if ok else b"\x00")


def _verify_traps_section(traps: Dict[str, Any], alpha: float = 0.05) -> Tuple[float, Dict[str, Any]]:
    """
    Verify seedCommit == H(seedReveal), recompute Merkle root of receipts,
    and compute traps_ratio = ok_count / total_trap_shots.
    Uses Wilson lower bound as a conservative displayed score component (details only).
    """
    seed_commit = bytes(traps["seedCommit"])
    seed_reveal = bytes(traps["seedReveal"])
    if len(seed_commit) != 32 or len(seed_reveal) != 32:
        raise SchemaError("seedCommit/seedReveal must be 32 bytes")
    if sha3_256(seed_reveal) != seed_commit:
        raise ProofError("trap seed commit mismatch")

    receipts = traps.get("receipts", [])
    if not isinstance(receipts, list):
        raise SchemaError("traps.receipts must be a list")

    leaves: List[bytes] = []
    trap_ok_shots = 0
    trap_total_shots = 0

    for idx, r in enumerate(receipts):
        try:
            d = bytes(r["trapDigest"])
            c = int(r["count"])
            ok = bool(r["ok"])
        except Exception as e:  # noqa: BLE001
            raise SchemaError(f"invalid traps.receipts[{idx}]: {e}") from e
        if c < 0:
            raise SchemaError("trap count must be non-negative")
        leaves.append(_trap_item_bytes(d, c, ok))
        trap_total_shots += c
        if ok:
            trap_ok_shots += c

    root = _merkle_root(leaves)
    declared_root = bytes(traps["root"])
    if len(declared_root) != 32:
        raise SchemaError("traps.root must be 32 bytes")
    if root != declared_root:
        raise ProofError("trap receipts Merkle root mismatch")

    total = max(1, trap_total_shots)
    ratio = trap_ok_shots / float(total)
    lb = confidence_lower_bound(trap_ok_shots, total, alpha=alpha)

    details = {
        "trap_ok_shots": trap_ok_shots,
        "trap_total_shots": trap_total_shots,
        "trap_ratio_lb95": lb,
        "trap_root": root.hex(),
    }
    return ratio, details


def _qos_score(qos: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Combine latency (P95), success rate, and uptime into a single [0,1] score.
    Mapping for latency: 0ms → 1.0, 1500ms → ~0.5, 6000ms → ~0.0.
    """
    p95 = max(0, int(qos["latencyMsP95"]))
    success = int(qos["successPermil"])
    uptime = int(qos["uptimePermil"])
    if not (0 <= success <= 1000 and 0 <= uptime <= 1000):
        raise SchemaError("successPermil/uptimePermil must be 0..1000")

    import math

    lat_norm = 1.0 - (math.log1p(p95 / 1500.0) / math.log1p(4.0))  # 0ms→1, 1.5s→~0.5, 6s→0
    lat_norm = clamp01(lat_norm)

    succ_norm = success / 1000.0
    up_norm = uptime / 1000.0
    score = clamp01(0.45 * lat_norm + 0.30 * succ_norm + 0.25 * up_norm)
    return score, {
        "latencyMsP95": p95,
        "success": succ_norm,
        "uptime": up_norm,
        "lat_component": lat_norm,
    }


def _derive_quantum_units(job: Dict[str, Any]) -> int:
    """
    Prefer explicit quantumUnits; otherwise derive from (depth,width,shots)
    using the reference units_for() mapping.
    """
    if "quantumUnits" in job:
        val = int(job["quantumUnits"])
        if val < 0:
            raise SchemaError("quantumUnits must be non-negative")
        return val
    depth = int(job["depth"])
    width = int(job["width"])
    shots = int(job["shots"])
    if depth < 0 or width < 0 or shots <= 0:
        raise SchemaError("depth/width must be ≥0 and shots ≥1")
    return max(0, int(units_for(depth=depth, width=width, shots=shots)))


# ─────────────────────────────── main API ───────────────────────────────

def verify_quantum_body(body: Dict[str, Any]) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Verify a Quantum proof body and return (metrics, details).
    - Validates structure against schema.
    - Verifies provider certificate/attestation and optional policy flags.
    - Verifies trap-circuit seed/reveal and receipts Merkle root; computes success ratio.
    - Computes normalized quantum_units and QoS score.

    details: rich dictionary for observability (safe to log).
    """
    # 1) Structural validation vs JSON/CDDL schema
    validate_body(ProofType.QUANTUM, body)

    # 2) Provider certificate / attestation
    provider_bundle = body["provider"]
    ok, provider_info = verify_provider_cert(provider_bundle)
    if not ok:
        raise AttestationError("quantum provider certificate/attestation failed policy checks")

    # 3) Traps
    traps_ratio, trap_details = _verify_traps_section(body["traps"])

    # 4) QoS
    qos, qos_details = _qos_score(body["qos"])

    # 5) Units
    job = body["job"]
    quantum_units = _derive_quantum_units(job)

    metrics = ProofMetrics(
        quantum_units=quantum_units,
        traps_ratio=traps_ratio,
        qos=qos,
    )

    details = {
        "taskId": bytes(job["taskId"]).hex(),
        "circuitDigest": bytes(job["circuitDigest"]).hex(),
        "resultDigest": bytes(job["resultDigest"]).hex(),
        "depth": int(job["depth"]),
        "width": int(job["width"]),
        "shots": int(job["shots"]),
        "provider": provider_info,    # vendor/model/endorsements/keys/validity windows
        "traps": trap_details,
        "qos": qos_details,
        "quantum_units": quantum_units,
    }
    return metrics, details


def verify_envelope(env: ProofEnvelope) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Envelope-aware variant; env.type_id must be QUANTUM.
    """
    if env.type_id != ProofType.QUANTUM:
        raise SchemaError(f"wrong proof type for Quantum verifier: {int(env.type_id)}")
    return verify_quantum_body(env.body)


__all__ = [
    "verify_quantum_body",
    "verify_envelope",
]
