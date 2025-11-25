"""
Animica | proofs.ai

AI v1 verification:
- Verifies a TEE attestation bundle (SGX/TDX, SEV-SNP, Arm CCA) via proofs.attestations.tee.*
- Verifies trap receipts committed under a Merkle root and a seed commit→reveal pair.
- Derives redundancy and QoS inputs from the proof body.
- Emits ProofMetrics(ai_units, traps_ratio, redundancy, qos).

Body shape (validated against proofs/schemas/ai_attestation.schema.json):

{
  "tee": {            # TEE evidence bundle (opaque to this module; verified by vendor-specific code)
    "kind": "sgx" | "sev_snp" | "cca",
    "evidence": bytes,
    ? "policy": { ... }  # optional vendor/measurement allowlist; adapters enforce if present
  },

  "job": {            # deterministic job linkage
    "taskId": bstr .size 32,
    "inputDigest": bstr .size 32,
    "outputDigest": bstr .size 32,
    "runtimeSec": uint,
    ? "aiUnits": uint    # preferred: pre-computed normalized units (tokens/flops-seconds → chain units)
  },

  "traps": {          # correctness beacons mixed into the prompt stream
    "seedCommit": bstr .size 32,        # H(seedReveal)
    "seedReveal": bstr .size 32,        # revealed seed for auditing
    "receipts": [                        # evaluated traps
      { "promptDigest": bstr .size 32,
        "answerDigest": bstr .size 32,
        "ok": bool }
    ],
    "root": bstr .size 32               # Merkle root over receipts (domain-separated)
  },

  "redundancy": {     # cross-provider agreement signal
    "replicas": uint,                   # number of providers asked
    "agree": uint,                      # count of agreeing outputs
    "total": uint                       # count of responses received
  },

  "qos": {            # service quality snapshot for the job window
    "latencyMsP95": uint,
    "successPermil": uint,              # 0..1000
    "uptimePermil": uint                # 0..1000
  }
}

Returned metrics (ProofMetrics):
- ai_units:          int (≥0) — normalized compute units (as provided or derived)
- traps_ratio:       float in [0,1]
- redundancy:        float in [0,1]
- qos:               float in [0,1]
(Other ProofMetrics fields are None for AI proof.)
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .errors import ProofError, AttestationError, SchemaError
from .types import ProofType, ProofEnvelope
from .metrics import ProofMetrics
from .cbor import validate_body
from .utils.hash import sha3_256
from .utils.math import clamp01

# TEE verifiers
from .attestations.tee.common import verify_tee_attestation  # runtime-dispatches to sgx/sev/cca
# If individual imports are needed by tooling, they can be used in common.verify_tee_attestation:
# from .attestations.tee import sgx, sev_snp, cca


# ─────────────────────────────── helpers ───────────────────────────────

_TRAP_ITEM_DOMAIN = b"Animica/AITrapItem/v1"
_TRAP_ROOT_DOMAIN = b"Animica/AITrapRoot/v1"


def _merkle_root(leaves: List[bytes]) -> bytes:
    """
    Canonical SHA3-256 Merkle root with domain separation:
      - leaf hash = sha3_256(TRAP_ITEM_DOMAIN || leaf)
      - internal = sha3_256(TRAP_ROOT_DOMAIN || left || right)
      - odd nodes are duplicated (Bitcoin-style) to form pairs
    Empty list → sha3_256(TRAP_ROOT_DOMAIN) (defined sentinel).
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


def _trap_item_bytes(prompt_digest: bytes, answer_digest: bytes, ok: bool) -> bytes:
    if len(prompt_digest) != 32 or len(answer_digest) != 32:
        raise SchemaError("trap receipt digests must be 32 bytes")
    return prompt_digest + answer_digest + (b"\x01" if ok else b"\x00")


def _verify_traps_section(traps: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Verify seedCommit == H(seedReveal), recompute Merkle root of receipts,
    and compute traps_ratio = ok_count / total.
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
    ok_count = 0
    for idx, r in enumerate(receipts):
        try:
            p = bytes(r["promptDigest"])
            a = bytes(r["answerDigest"])
            ok = bool(r["ok"])
        except Exception as e:  # noqa: BLE001
            raise SchemaError(f"invalid trap receipt at index {idx}: {e}") from e
        leaves.append(_trap_item_bytes(p, a, ok))
        ok_count += 1 if ok else 0

    root = _merkle_root(leaves)
    declared_root = bytes(traps["root"])
    if len(declared_root) != 32:
        raise SchemaError("traps.root must be 32 bytes")
    if root != declared_root:
        raise ProofError("trap receipts Merkle root mismatch")

    total = max(1, len(receipts))
    traps_ratio = ok_count / float(total)
    details = {
        "traps_ok": ok_count,
        "traps_total": len(receipts),
        "trap_root": root.hex(),
    }
    return traps_ratio, details


def _redundancy_score(redundancy: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    replicas = int(redundancy["replicas"])
    agree = int(redundancy["agree"])
    total = int(redundancy["total"])
    if replicas <= 0 or total < 0 or agree < 0:
        raise SchemaError("redundancy parameters must be non-negative (replicas > 0)")
    if agree > total or total > replicas:
        raise SchemaError("redundancy must satisfy agree ≤ total ≤ replicas")
    # Score is agreement ratio among returned replicas; normalize to [0,1].
    score = 0.0 if total == 0 else agree / float(total)
    return clamp01(score), {
        "replicas": replicas,
        "agree": agree,
        "total": total,
    }


def _qos_score(qos: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Combine latency (P95), success rate, and uptime into a single [0,1] score.
    Mapping for latency: 0ms → 1.0, 1000ms → ~0.5, 4000ms → ~0.0 (smooth log curve).
    """
    p95 = max(0, int(qos["latencyMsP95"]))
    success = int(qos["successPermil"])
    uptime = int(qos["uptimePermil"])
    if not (0 <= success <= 1000 and 0 <= uptime <= 1000):
        raise SchemaError("successPermil/uptimePermil must be 0..1000")

    # Latency → [0,1] using a soft logarithmic squashing.
    import math

    lat_norm = 1.0 - (math.log1p(p95 / 1000.0) / math.log1p(4.0))  # 0ms→1, 1000ms→~0.5, 4s→0
    lat_norm = clamp01(lat_norm)

    succ_norm = success / 1000.0
    up_norm = uptime / 1000.0

    # Weighted blend; weights can be re-tuned via policy in future versions.
    score = clamp01(0.4 * lat_norm + 0.3 * succ_norm + 0.3 * up_norm)
    return score, {
        "latencyMsP95": p95,
        "success": succ_norm,
        "uptime": up_norm,
        "lat_component": lat_norm,
    }


def _derive_ai_units(job: Dict[str, Any]) -> int:
    """
    Prefer explicit aiUnits; otherwise derive a conservative unit count
    from runtime (sec) using a chain-wide minimum baseline (100 units/sec).
    (Exact conversion from FLOP-seconds/tokens is policy-tunable off-chain.)
    """
    if "aiUnits" in job:
        val = int(job["aiUnits"])
        if val < 0:
            raise SchemaError("aiUnits must be non-negative")
        return val
    runtime = int(job.get("runtimeSec", 0))
    if runtime < 0:
        raise SchemaError("runtimeSec must be non-negative")
    return runtime * 100


# ─────────────────────────────── main API ───────────────────────────────

def verify_ai_body(body: Dict[str, Any]) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Verify an AI proof body and return (metrics, details).
    - Validates structure against schema.
    - Verifies TEE attestation bundle.
    - Verifies traps seed/reveals and receipts root.
    - Computes redundancy & QoS scores and ai_units.

    details: rich dictionary for caller observability (safe to log).
    """
    # 1) Structural validation vs JSON/CDDL schema
    validate_body(ProofType.AI, body)

    # 2) TEE attestation
    tee_bundle = body["tee"]
    tee_ok, tee_info = verify_tee_attestation(tee_bundle)
    if not tee_ok:
        raise AttestationError("TEE attestation failed or violates policy")

    # 3) Traps
    traps_ratio, trap_details = _verify_traps_section(body["traps"])

    # 4) Redundancy
    redundancy, red_details = _redundancy_score(body["redundancy"])

    # 5) QoS
    qos, qos_details = _qos_score(body["qos"])

    # 6) AI units
    job = body["job"]
    ai_units = _derive_ai_units(job)

    metrics = ProofMetrics(
        ai_units=ai_units,
        traps_ratio=traps_ratio,
        redundancy=redundancy,
        qos=qos,
    )

    details = {
        "taskId": bytes(job["taskId"]).hex(),
        "inputDigest": bytes(job["inputDigest"]).hex(),
        "outputDigest": bytes(job["outputDigest"]).hex(),
        "runtimeSec": int(job.get("runtimeSec", 0)),
        "tee": tee_info,                 # dict with vendor, measurement, policy flags, timestamp, etc.
        "traps": trap_details,
        "redundancy": red_details,
        "qos": qos_details,
        "ai_units": ai_units,
    }
    return metrics, details


def verify_envelope(env: ProofEnvelope) -> Tuple[ProofMetrics, Dict[str, Any]]:
    """
    Envelope-aware variant; env.type_id must be AI.
    """
    if env.type_id != ProofType.AI:
        raise SchemaError(f"wrong proof type for AI verifier: {int(env.type_id)}")
    return verify_ai_body(env.body)


__all__ = [
    "verify_ai_body",
    "verify_envelope",
]
