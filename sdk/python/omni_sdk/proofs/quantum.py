"""
omni_sdk.proofs.quantum
=======================

Client-side helper to assemble **QuantumProof reference objects** from provider
attestations and trap-circuit outcomes, with optional bindings to a header and
task id. This is a *non-consensus* convenience for SDKs, wallets, and tools.
Consensus verification/scoring live in the node's `proofs/quantum.py` and
`proofs/policy_adapter.py`.

What you get
------------
- Stable envelope shape: {"type_id","body","nullifier","metrics"}
- Deterministic digests of circuit spec and outcomes (domain-separated SHA3-256)
- Normalization of trap outcomes into a ratio, QoS into a [0,1] score
- Heuristic `quantum_units` estimator (or honor explicit value)

Example
-------
    from omni_sdk.proofs.quantum import assemble_quantum_proof

    proof = assemble_quantum_proof(
        circuits={"depth": 28, "width": 16, "shots": 2048},   # or a full spec/list
        outcomes={"trap_passes": 1900, "trap_fails": 148},
        attestation={"provider_cert": {...}},
        traps={"passes": 97, "fails": 3, "seed": "0x1234"},
        benchmarks={"depth": 28, "width": 16, "shots": 2048},
        qos={"latency_ms": 2200, "availability": 0.999},
        task_id="qjob_abc123",
        provider_id="qpu-lab-01",
        header={"hash": "0x..."}
    )

Returned dict fields are documented in the function below.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Union

# --- Utilities & fallbacks ---------------------------------------------------

try:
    from omni_sdk.utils.bytes import from_hex as _from_hex  # type: ignore
    from omni_sdk.utils.bytes import to_hex as _to_hex
except Exception:  # pragma: no cover

    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)

    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()


try:
    from omni_sdk.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib as _hashlib

    def sha3_256(data: bytes) -> bytes:
        return _hashlib.sha3_256(data).digest()


# Deterministic JSON-like bytes as a last-resort encoder (kept tiny)
try:
    from omni_sdk.utils.cbor import dumps as cbor_dumps  # type: ignore
except Exception:  # pragma: no cover
    import json as _json

    def cbor_dumps(obj: Any) -> bytes:
        return _json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


Json = Dict[str, Any]


class QuantumProofError(Exception):
    """Raised when inputs are malformed or assembly fails."""


# Domain tags
DOMAIN_CIRCUIT_DIGEST = b"animica:quantum.circuit.digest.v1"
DOMAIN_OUTCOME_DIGEST = b"animica:quantum.outcome.digest.v1"
DOMAIN_NULLIFIER = b"animica:proof.nullifier.quantum.v1"


def _norm_bytes(
    x: Union[str, bytes, bytearray, memoryview, None], *, field: str
) -> Optional[bytes]:
    if x is None:
        return None
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        # Accept 0x-hex; otherwise treat as UTF-8 string
        if x.startswith("0x") and len(x) >= 4:
            try:
                return _from_hex(x)
            except Exception as e:
                raise QuantumProofError(f"{field}: invalid hex") from e
        return x.encode("utf-8")
    raise QuantumProofError(f"{field} must be bytes or string")


def _extract_header_hash(header: Optional[Mapping[str, Any]]) -> Optional[bytes]:
    if not header:
        return None
    v = header.get("hash") or header.get("headerHash")
    if isinstance(v, str) and v.startswith("0x"):
        return _from_hex(v)
    rb = header.get("raw")
    if isinstance(rb, str) and rb.startswith("0x"):
        return sha3_256(_from_hex(rb))
    return None


def _digest(obj: Any, *, domain: bytes) -> bytes:
    """
    Deterministically digest an object:
      - bytes/bytearray: as-is
      - str: if 0x-hex -> decode; else UTF-8
      - dict/list/tuple: canonical CBOR/JSON-ish encoding
    """
    if isinstance(obj, (bytes, bytearray, memoryview)):
        payload = bytes(obj)
    elif isinstance(obj, str):
        if obj.startswith("0x") and len(obj) >= 4:
            payload = _from_hex(obj)
        else:
            payload = obj.encode("utf-8")
    else:
        payload = cbor_dumps(obj)
    return sha3_256(domain + payload)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _norm_traps(traps: Optional[Mapping[str, Any]]) -> Json:
    """
    Normalize trap-circuit outcomes.
    Accept shapes:
      - {"passes": N, "fails": M, "seed": "0x..."}
      - {"ratio": r}
      - {"trap_passes": N, "trap_fails": M} (alternate keys)
    """
    if not traps:
        return {"passes": 0, "fails": 0, "ratio": 0.0}
    passes_ = int(traps.get("passes", traps.get("trap_passes", 0)) or 0)
    fails_ = int(traps.get("fails", traps.get("trap_fails", 0)) or 0)
    total = passes_ + fails_
    if "ratio" in traps:
        ratio = float(traps["ratio"])
        ratio = (passes_ / total) if total > 0 else _clamp01(ratio)
    else:
        ratio = (passes_ / total) if total > 0 else 0.0
    out: Json = {"passes": passes_, "fails": fails_, "ratio": _clamp01(ratio)}
    for k in ("seed", "confidence", "method"):
        if k in traps:
            out[k] = traps[k]
    return out


def _latency_score_ms(latency_ms: Optional[float]) -> float:
    """Map latency in ms to [0,1]; lower latency scores higher."""
    if latency_ms is None or latency_ms <= 0:
        return 1.0
    s = 1.0 / (1.0 + (latency_ms / 1000.0))  # slower decay than AI jobs
    return max(0.1, min(1.0, s))


def _norm_qos(qos: Optional[Mapping[str, Any]]) -> Json:
    """
    Normalize QoS to a scalar score and echo common fields.
    Keys: latency_ms, success_rate/success, availability/uptime.
    """
    if not qos:
        return {"score": 1.0}
    lat = qos.get("latency_ms")
    lat_s = _latency_score_ms(float(lat)) if lat is not None else 1.0
    succ = _clamp01(float(qos.get("success_rate", qos.get("success", 1.0))))
    avail = _clamp01(float(qos.get("availability", qos.get("uptime", 1.0))))
    score = _clamp01(0.50 * succ + 0.35 * avail + 0.15 * lat_s)
    out: Json = {"score": score}
    for k in ("latency_ms", "success_rate", "availability", "uptime"):
        if k in qos:
            out[k] = qos[k]
    return out


def _norm_benchmarks(bench: Optional[Mapping[str, Any]], circuits: Any) -> Json:
    """
    Normalize benchmark hints.
    Recognized keys: depth, width, shots. If missing, try to infer from circuits.
    """
    depth = width = shots = None
    if bench:
        depth = bench.get("depth")
        width = bench.get("width")
        shots = bench.get("shots")

    # Try inference from a minimal circuit dict/list
    try:
        if depth is None and isinstance(circuits, Mapping):
            depth = circuits.get("depth", depth)
        if width is None and isinstance(circuits, Mapping):
            width = circuits.get("width", width)
        if shots is None and isinstance(circuits, Mapping):
            shots = circuits.get("shots", shots)
    except Exception:
        pass

    out: Json = {}
    if depth is not None:
        out["depth"] = int(depth)
    if width is not None:
        out["width"] = int(width)
    if shots is not None:
        out["shots"] = int(shots)
    return out


def _compute_quantum_units(
    *,
    quantum_units: Optional[float],
    benchmarks: Mapping[str, Any],
    outcome_len_bytes: int,
) -> float:
    """
    Heuristic estimator for `quantum_units` if not provided.

    Priority:
      1) explicit quantum_units
      2) product depth*width*shots if available
      3) fallback: 1 unit per KiB of outcome payload
    """
    if quantum_units is not None:
        return float(quantum_units)
    d = int(benchmarks.get("depth", 0) or 0)
    w = int(benchmarks.get("width", 0) or 0)
    s = int(benchmarks.get("shots", 0) or 0)
    prod = d * w * max(1, s)
    if prod > 0:
        return float(prod)
    return max(1.0, float(outcome_len_bytes) / 1024.0)


def _nullifier(
    *,
    task_id_b: Optional[bytes],
    header_hash: Optional[bytes],
    circuit_digest: Optional[bytes],
    outcome_digest: Optional[bytes],
) -> bytes:
    """
    Deterministic nullifier to avoid duplicate references in client tools.

        H(DOMAIN_NULLIFIER || task_id? || header_hash? || circuit_digest? || outcome_digest?)
    """
    parts = [DOMAIN_NULLIFIER]
    if task_id_b:
        parts.append(task_id_b)
    if header_hash:
        parts.append(header_hash)
    if circuit_digest:
        parts.append(circuit_digest)
    if outcome_digest:
        parts.append(outcome_digest)
    return sha3_256(b"".join(parts))


def assemble_quantum_proof(
    *,
    circuits: Optional[Any] = None,
    outcomes: Optional[Any] = None,
    attestation: Mapping[str, Any],
    traps: Optional[Mapping[str, Any]] = None,
    benchmarks: Optional[Mapping[str, Any]] = None,
    qos: Optional[Mapping[str, Any]] = None,
    task_id: Optional[Union[str, bytes]] = None,
    provider_id: Optional[str] = None,
    header: Optional[Mapping[str, Any]] = None,
    quantum_units: Optional[float] = None,
    type_id: str = "quantum.v1",
) -> Dict[str, Any]:
    """
    Assemble a QuantumProof reference envelope.

    Parameters
    ----------
    circuits :
        Circuit spec (dict/list/string/bytes). Used only for digesting/metadata.
    outcomes :
        Measurement outcomes/transcript (any JSON-serializable or bytes). Digested.
    attestation :
        Provider identity/evidence bundle (JSON-serializable).
    traps :
        Trap-circuit summary. Accept {"passes":N,"fails":M,"seed":...} or {"ratio":r}.
    benchmarks :
        Hints like {"depth":D,"width":W,"shots":S}. Auto-inferred from circuits when possible.
    qos :
        Optional QoS hints: {"latency_ms":.., "success_rate":.., "availability":..}.
    task_id :
        Deterministic id from capabilities/jobs/id or any stable id.
    provider_id :
        Human/registry provider id (echoed).
    header :
        Optional header mapping to bind (hash/raw). Echoed if present.
    quantum_units :
        Optional explicit compute units. If omitted, estimated from benchmarks/outcome size.
    type_id :
        Envelope type identifier (default "quantum.v1").

    Returns
    -------
    dict
        {
          "type_id": "quantum.v1",
          "body": {
            "taskId": "...", "providerId": "...", "headerHash": "0x..."?,
            "circuitDigest": "0x..."?, "outcomeDigest": "0x..."?,
            "attestation": {...},
            "traps": {...}, "benchmarks": {...}, "qos": {...}
          },
          "nullifier": "0x...",
          "metrics": {"quantum_units": float, "traps_ratio": float, "qos": float}
        }
    """
    circuit_digest: Optional[bytes] = (
        _digest(circuits, domain=DOMAIN_CIRCUIT_DIGEST)
        if circuits is not None
        else None
    )
    outcome_digest: Optional[bytes] = (
        _digest(outcomes, domain=DOMAIN_OUTCOME_DIGEST)
        if outcomes is not None
        else None
    )

    traps_n = _norm_traps(traps)
    qos_n = _norm_qos(qos)
    bench_n = _norm_benchmarks(benchmarks, circuits)

    # Estimate units
    outcome_len = 0
    if isinstance(outcomes, (bytes, bytearray, memoryview)):
        outcome_len = len(outcomes)
    elif outcomes is None:
        outcome_len = 0
    else:
        outcome_len = len(cbor_dumps(outcomes))
    units = _compute_quantum_units(
        quantum_units=quantum_units, benchmarks=bench_n, outcome_len_bytes=outcome_len
    )

    # Optional bindings
    header_hash = _extract_header_hash(header)
    task_id_b = _norm_bytes(task_id, field="task_id")

    nul = _nullifier(
        task_id_b=task_id_b,
        header_hash=header_hash,
        circuit_digest=circuit_digest,
        outcome_digest=outcome_digest,
    )

    body: Json = {
        "attestation": dict(attestation),
        "traps": traps_n,
        "benchmarks": bench_n,
        "qos": qos_n,
    }
    if task_id is not None:
        body["taskId"] = task_id if isinstance(task_id, str) else _to_hex(task_id_b or b"")  # type: ignore[arg-type]
    if provider_id is not None:
        body["providerId"] = provider_id
    if header_hash is not None:
        body["headerHash"] = _to_hex(header_hash)
    if circuit_digest is not None:
        body["circuitDigest"] = _to_hex(circuit_digest)
    if outcome_digest is not None:
        body["outcomeDigest"] = _to_hex(outcome_digest)

    envelope: Dict[str, Any] = {
        "type_id": type_id,
        "body": body,
        "nullifier": _to_hex(nul),
        "metrics": {
            "quantum_units": float(units),
            "traps_ratio": float(traps_n.get("ratio", 0.0)),
            "qos": float(qos_n.get("score", 1.0)),
        },
    }
    return envelope


__all__ = ["QuantumProofError", "assemble_quantum_proof"]
