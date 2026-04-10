"""
omni_sdk.proofs.ai
==================

Client-side helper to assemble **AIProof reference objects** from an AI job's
outputs, attestation bundles, trap receipts, and QoS/redundancy signals.

This is a **non-consensus** convenience for SDKs, wallets, and tools to package
a proof-shaped object that downstream components (e.g., AICF bridge, demo nodes,
or test harnesses) can accept. Consensus verification and scoring remain the job
of the node's `proofs/ai.py` and `proofs/policy_adapter.py`.

What you get
------------
- Stable envelope shape: {"type_id","body","nullifier","metrics"}
- Output digesting (SHA3-256) with a clear domain tag
- Optional linkage to a header hash and/or task id
- Normalization of `traps`, `qos`, and `redundancy` inputs into scalar metrics

Example
-------
    from omni_sdk.proofs.ai import assemble_ai_proof

    proof = assemble_ai_proof(
        output=b"model output bytes",
        attestation={"tee": {"quote": "0x..."}},
        traps={"passes": 97, "fails": 3, "seed": "0x1234"},
        qos={"latency_ms": 850, "success_rate": 0.99, "availability": 0.995},
        redundancy={"replicas": 2, "agree": 2},
        task_id="task_abc123",
        provider_id="provider-01",
        header={"hash": "0x..."}  # optional binding
    )

    # -> dict with .body.outputDigest, .metrics.traps_ratio, .metrics.qos, etc.

Inputs are intentionally flexible; see function docstring for accepted shapes.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping, Optional, Union

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


class AIProofError(Exception):
    """Raised when inputs are malformed or assembly fails."""


# Domain tags
DOMAIN_OUTPUT_DIGEST = b"animica:ai.output.digest.v1"
DOMAIN_NULLIFIER = b"animica:proof.nullifier.ai.v1"


def _norm_bytes(x: Union[str, bytes, bytearray, memoryview], *, field: str) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    if isinstance(x, str):
        # Accept 0x-hex; otherwise treat as UTF-8 string
        if x.startswith("0x") and len(x) >= 4:
            try:
                return _from_hex(x)
            except Exception as e:
                raise AIProofError(f"{field}: invalid hex") from e
        return x.encode("utf-8")
    raise AIProofError(f"{field} must be bytes or string")


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


def _digest_output(output: Union[str, bytes, bytearray, memoryview]) -> bytes:
    b = _norm_bytes(output, field="output")
    # Domain-separate the output digest for clarity and collision resistance
    return sha3_256(DOMAIN_OUTPUT_DIGEST + b)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)


def _norm_traps(traps: Optional[Mapping[str, Any]]) -> Json:
    """Accept shapes like {"passes":N,"fails":M} or {"ratio":r} and normalize."""
    if not traps:
        return {"passes": 0, "fails": 0, "ratio": 0.0}
    passes_ = int(traps.get("passes", 0) or 0)
    fails_ = int(traps.get("fails", 0) or 0)
    total = passes_ + fails_
    if "ratio" in traps:
        ratio = float(traps["ratio"])
        # If passes/fails given, prefer their computed ratio; else clamp provided one.
        ratio = (passes_ / total) if total > 0 else _clamp01(ratio)
    else:
        ratio = (passes_ / total) if total > 0 else 0.0
    out: Json = {
        "passes": passes_,
        "fails": fails_,
        "ratio": _clamp01(ratio),
    }
    # Preserve common optional fields if present
    for k in ("seed", "confidence", "method"):
        if k in traps:
            out[k] = traps[k]
    return out


def _latency_score_ms(latency_ms: Optional[float]) -> float:
    """
    Map latency in ms to [0,1], where lower is better.
    Piecewise: <=500ms -> ~1, 2s -> ~0.5, >=10s -> ~0.1 (soft floor).
    """
    if latency_ms is None or latency_ms <= 0:
        return 1.0
    # Smooth hyperbolic decay with soft floor at ~0.1
    s = 1.0 / (1.0 + (latency_ms / 500.0))
    return max(0.1, min(1.0, s))


def _norm_qos(qos: Optional[Mapping[str, Any]]) -> Json:
    """
    Normalize QoS inputs to a scalar `score` in [0,1] plus echoed fields.
    Accepts keys like: latency_ms, success_rate, availability, uptime.
    """
    if not qos:
        return {"score": 1.0}
    lat = qos.get("latency_ms")
    lat_s = _latency_score_ms(float(lat)) if lat is not None else 1.0
    succ = _clamp01(float(qos.get("success_rate", qos.get("success", 1.0))))
    avail = _clamp01(float(qos.get("availability", qos.get("uptime", 1.0))))
    # Blend with simple weights (can be tuned to match policy)
    score = _clamp01(0.55 * succ + 0.30 * avail + 0.15 * lat_s)
    out: Json = {"score": score}
    for k in ("latency_ms", "success_rate", "availability", "uptime"):
        if k in qos:
            out[k] = qos[k]
    return out


def _norm_redundancy(redundancy: Optional[Mapping[str, Any]]) -> Json:
    """
    Normalize redundancy agreement to a scalar `score` in [0,1].
    Accepts: {"replicas": N, "agree": K} or direct {"score": r}.
    """
    if not redundancy:
        return {"score": 1.0}
    if "score" in redundancy:
        r = _clamp01(float(redundancy["score"]))
        out = {"score": r}
    else:
        replicas = max(0, int(redundancy.get("replicas", 0) or 0))
        agree = max(0, int(redundancy.get("agree", 0) or 0))
        r = (agree / replicas) if replicas > 0 else 1.0
        out = {"replicas": replicas, "agree": agree, "score": _clamp01(r)}
        if "disagree" in redundancy:
            out["disagree"] = int(redundancy["disagree"])
    return out


def _compute_ai_units(
    *,
    ai_units: Optional[float],
    output_bytes_len: int,
    qos: Mapping[str, Any],
) -> float:
    """
    Very rough dev-only estimator for `ai_units` if caller didn't provide one.

    Priority:
      1) explicit ai_units if given
      2) qos["tokens"] if present (scaled)
      3) fallback to ~bytes/1024 (KB) as a weak proxy for effort
    """
    if ai_units is not None:
        return float(ai_units)
    if "tokens" in qos:
        try:
            return float(qos["tokens"])
        except Exception:
            pass
    # Fallback scale: 1 unit per KiB of output
    return max(1.0, float(output_bytes_len) / 1024.0)


def _nullifier(
    *, task_id_b: Optional[bytes], output_digest: bytes, header_hash: Optional[bytes]
) -> bytes:
    """
    Deterministic nullifier to de-duplicate references in client tools.

        nullifier = H(DOMAIN_NULLIFIER || task_id? || header_hash? || output_digest)

    Fields are optional; when omitted they are simply not included.
    """
    parts = [DOMAIN_NULLIFIER]
    if task_id_b:
        parts.append(task_id_b)
    if header_hash:
        parts.append(header_hash)
    parts.append(output_digest)
    return sha3_256(b"".join(parts))


def assemble_ai_proof(
    *,
    output: Union[str, bytes, bytearray, memoryview],
    attestation: Mapping[str, Any],
    traps: Optional[Mapping[str, Any]] = None,
    qos: Optional[Mapping[str, Any]] = None,
    redundancy: Optional[Mapping[str, Any]] = None,
    task_id: Optional[Union[str, bytes]] = None,
    provider_id: Optional[str] = None,
    header: Optional[Mapping[str, Any]] = None,
    ai_units: Optional[float] = None,
    type_id: str = "ai.v1",
) -> Dict[str, Any]:
    """
    Assemble an AIProof reference envelope.

    Parameters
    ----------
    output :
        Raw model output bytes or string. If a hex string (0x…), decoded first.
    attestation :
        Opaque evidence bundle (TEE quote, provider cert, etc.). Must be JSON-serializable.
    traps :
        Trap-circuit outcomes. Accept:
          - {"passes": N, "fails": M, "seed": "0x..."} (ratio computed)
          - {"ratio": r} (0..1)
    qos :
        Service quality hints. Accept keys:
          - latency_ms: float
          - success_rate / success: 0..1
          - availability / uptime: 0..1
          - tokens: numeric (optional; used to estimate ai_units)
    redundancy :
        Replication agreement. Accept:
          - {"replicas": N, "agree": K, "disagree": J?}
          - {"score": r}
    task_id :
        Deterministic id from capabilities/jobs/id, or any caller-supplied stable id.
    provider_id :
        Human-readable or registry id for the provider (optional, echoed).
    header :
        Optional header mapping to bind to (hash/raw). If provided, its hash is echoed in body.
    ai_units :
        Optional explicit compute units. If omitted, a rough estimate is derived.
    type_id :
        Envelope type identifier (default "ai.v1").

    Returns
    -------
    dict
        {
          "type_id": "ai.v1",
          "body": {
            "taskId": "...", "providerId": "...", "headerHash": "0x..."?,
            "outputDigest": "0x...", "attestation": {...},
            "traps": {...}, "qos": {...}, "redundancy": {...}
          },
          "nullifier": "0x...",
          "metrics": {"ai_units": float, "traps_ratio": float, "qos": float, "redundancy": float}
        }
    """
    # Digest output
    out_digest = _digest_output(output)
    out_len = len(_norm_bytes(output, field="output"))

    # Normalize signals
    traps_n = _norm_traps(traps)
    qos_n = _norm_qos(qos)
    red_n = _norm_redundancy(redundancy)

    # Estimate ai_units if needed
    units = _compute_ai_units(ai_units=ai_units, output_bytes_len=out_len, qos=qos_n)

    # Optional bindings
    header_hash = _extract_header_hash(header)
    task_id_b = _norm_bytes(task_id, field="task_id") if task_id is not None else None

    nul = _nullifier(
        task_id_b=task_id_b, output_digest=out_digest, header_hash=header_hash
    )

    body: Json = {
        "outputDigest": _to_hex(out_digest),
        "attestation": dict(attestation),  # shallow copy
        "traps": traps_n,
        "qos": qos_n,
        "redundancy": red_n,
    }
    if task_id is not None:
        # Keep original representation for readability; also include a stable hex hint
        body["taskId"] = task_id if isinstance(task_id, str) else _to_hex(task_id_b)  # type: ignore[arg-type]
    if provider_id is not None:
        body["providerId"] = provider_id
    if header_hash is not None:
        body["headerHash"] = _to_hex(header_hash)

    envelope: Dict[str, Any] = {
        "type_id": type_id,
        "body": body,
        "nullifier": _to_hex(nul),
        "metrics": {
            "ai_units": float(units),
            "traps_ratio": float(traps_n.get("ratio", 0.0)),
            "qos": float(qos_n.get("score", 1.0)),
            "redundancy": float(red_n.get("score", 1.0)),
        },
    }
    return envelope


__all__ = ["AIProofError", "assemble_ai_proof"]
