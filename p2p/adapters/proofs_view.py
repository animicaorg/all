"""
p2p.adapters.proofs_view
========================

A *fast* pre-parser for proof envelopes flowing over share-gossip.

Goals
-----
- Decode just enough from a CBOR-encoded proof *envelope* to:
  • identify the proof type (Hash/AI/Quantum/Storage/VDF),
  • extract the nullifier (for dedupe/DoS control),
  • (optionally) extract a few light hints like the bound header hash for HashShare.
- Enforce cheap sanity checks (max size, required fields).
- Never run heavy cryptography or full verification here.

Non-goals
---------
- No ψ (psi) mapping or policy caps enforcement (that belongs to consensus).
- No attestation parsing (TEE/QPU) or VDF verification.
- No DB writes; callers may feed a rolling nullifier set for dedupe.

Wire shape
----------
Per spec/proofs/schemas/proof_envelope.cddl, an envelope contains:
  { type_id: uint, body: <CBOR value>, nullifier: bytes }

We accept a few liberal forms for resilience in devnets:
  - map with string keys: {"type_id", "body", "nullifier"}
  - map with small-int keys: {0,1,2}
  - 3-tuple/array: [type_id, body, nullifier]

This module depends only on *decoding* CBOR. It will try the project's
canonical decoder first (core.encoding.cbor), falling back to cbor2 if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, MutableSet, Optional

# --- CBOR decode (cheap, no canonicalization on encode required here) ---

try:
    # Preferred: project wrapper with deterministic semantics elsewhere
    from core.encoding.cbor import loads as _cbor_decode  # type: ignore
except Exception:  # pragma: no cover
    try:
        import cbor2  # type: ignore

        def _cbor_decode(b: bytes) -> Any:
            return cbor2.loads(b)
    except Exception as e:  # pragma: no cover
        raise RuntimeError("No CBOR decoder available (need core.encoding.cbor or cbor2)") from e

# --- Type ids and topic mapping ---

_TYPE_NAME_BY_ID = {
    # Keep in sync with consensus/proofs registries (defensive copy for fast-path use).
    0: "hash",       # HashShare
    1: "ai",         # AIProof
    2: "quantum",    # QuantumProof
    3: "storage",    # StorageHeartbeat
    4: "vdf",        # VDFProof
}

try:
    # Prefer canonical enum when available
    from consensus.types import ProofType  # type: ignore

    # Build a dynamic name map if enum exists
    _TYPE_NAME_BY_ID = {
        int(getattr(ProofType, "HASH", 0)): "hash",
        int(getattr(ProofType, "AI", 1)): "ai",
        int(getattr(ProofType, "QUANTUM", 2)): "quantum",
        int(getattr(ProofType, "STORAGE", 3)): "storage",
        int(getattr(ProofType, "VDF", 4)): "vdf",
    }
except Exception:
    pass  # fall back to static map


@dataclass(frozen=True)
class PreParsedProof:
    """Minimal information extracted from a proof envelope."""
    type_id: int
    type_name: str
    nullifier: Optional[bytes]
    size_bytes: int
    # Hints for specific types (best-effort, may be None)
    header_hash: Optional[bytes] = None  # for HashShare (bind to header template)
    # raw decoded body (only for upstreams that want to do further *cheap* checks)
    body: Optional[Any] = None


# --------------------------
# Internal helpers
# --------------------------

def _as_mapping(x: Any) -> Optional[Mapping[Any, Any]]:
    return x if isinstance(x, Mapping) else None


def _extract_envelope(obj: Any) -> tuple[int, Any, Optional[bytes]]:
    """
    Normalize decoded CBOR into (type_id, body, nullifier).
    Accepts dict (string or int keys) or a 3-item array/tuple.
    """
    # Array/tuple case: [type_id, body, nullifier]
    if isinstance(obj, (list, tuple)) and len(obj) == 3:
        t_id, body, nul = obj[0], obj[1], obj[2]
        return int(t_id), body, (bytes(nul) if isinstance(nul, (bytes, bytearray)) else None)

    m = _as_mapping(obj)
    if m is not None:
        # Prefer string keys
        if "type_id" in m and "body" in m and "nullifier" in m:
            nul = m["nullifier"]
            return int(m["type_id"]), m["body"], (bytes(nul) if isinstance(nul, (bytes, bytearray)) else None)
        # Small-int-keyed map: 0=type,1=body,2=nullifier
        if 0 in m and 1 in m:
            nul = m.get(2, None)
            return int(m[0]), m[1], (bytes(nul) if isinstance(nul, (bytes, bytearray)) else None)

    raise ValueError("invalid envelope shape")


def _type_name(tid: int) -> str:
    return _TYPE_NAME_BY_ID.get(int(tid), "unknown")


def _best_effort_header_hash(body: Any) -> Optional[bytes]:
    """
    For HashShare bodies only, try to extract a bound header hash without fully validating.
    We accept a few common shapes:
      {"headerHash": b'...'} or {"header_hash": b'...'} or {0: b'...'}
    """
    m = _as_mapping(body)
    if not m:
        return None
    # String-keyed variants
    for k in ("headerHash", "header_hash"):
        v = m.get(k)
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
    # Integer-keyed (compact) variant commonly used in CDDL→CBOR
    v = m.get(0)
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    return None


# --------------------------
# Public API
# --------------------------

def preparse_proof(envelope_cbor: bytes, *, max_size: int = 256 * 1024) -> PreParsedProof:
    """
    Decode the envelope and return a PreParsedProof.

    Cheap checks performed:
      - overall message size <= max_size
      - envelope contains type_id & body
      - nullifier is bytes or None (accepted but flagged if None)

    No heavy verification is done.
    """
    if not isinstance(envelope_cbor, (bytes, bytearray)):
        raise TypeError("envelope must be bytes")
    size = len(envelope_cbor)
    if size <= 0 or size > max_size:
        raise ValueError(f"envelope size {size} outside allowed bounds (0, {max_size}]")

    obj = _cbor_decode(envelope_cbor)
    t_id, body, nullifier = _extract_envelope(obj)
    name = _type_name(t_id)

    header_hash = _best_effort_header_hash(body) if name == "hash" else None
    return PreParsedProof(
        type_id=int(t_id),
        type_name=name,
        nullifier=nullifier,
        size_bytes=size,
        header_hash=header_hash,
        body=body,
    )


def admit_for_gossip(
    envelope_cbor: bytes,
    *,
    seen_nullifiers: Optional[MutableSet[bytes]] = None,
    max_size: int = 256 * 1024,
) -> tuple[Optional[PreParsedProof], Optional[str]]:
    """
    Fast admission check for share-gossip.

    Returns
    -------
    (pp, reason)
        If accepted: (PreParsedProof, None)
        If rejected: (None, reason_string)

    Rejection reasons (stable strings for metrics/logs):
      - "oversize"
      - "decode-failed"
      - "bad-envelope"
      - "unknown-type"
      - "missing-nullifier"
      - "duplicate-nullifier"
    """
    try:
        if len(envelope_cbor) > max_size:
            return None, "oversize"
        pp = preparse_proof(envelope_cbor, max_size=max_size)
    except ValueError as e:
        msg = str(e)
        if "size" in msg:
            return None, "oversize"
        return None, "bad-envelope"
    except Exception:
        return None, "decode-failed"

    if pp.type_name == "unknown":
        return None, "unknown-type"

    if pp.nullifier is None or not isinstance(pp.nullifier, (bytes, bytearray)) or len(pp.nullifier) == 0:
        return None, "missing-nullifier"

    if seen_nullifiers is not None:
        nul = bytes(pp.nullifier)
        if nul in seen_nullifiers:
            return None, "duplicate-nullifier"
        # Admit & record
        seen_nullifiers.add(nul)

    return pp, None


__all__ = ["PreParsedProof", "preparse_proof", "admit_for_gossip"]
