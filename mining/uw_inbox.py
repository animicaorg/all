"""
mining.uw_inbox
===============

Process-wide inbox that bridges the useful-work workers (ai_worker,
quantum_worker, storage_worker, vdf_worker) to the share scanner.

When a worker completes a job it builds a `compute.receipt.v1`
UsefulWorkProof envelope from its ResultRecord, encodes it to hex, and
pushes it here. The HashShare scanner drains pending proofs and attaches
them to outgoing shares as `attachedProofs`. The node-side UWP verifier
(see `core/usefulwork`) decodes, validates and credits bonus AICF credits
to the miner.

The receipt format intentionally matches the existing built-in verifier
(`core/usefulwork/verifiers.py::verify_compute_receipt`) so credits flow
without any consensus change. The signature is left empty (the verifier
currently stubs PQ signature checks) — once PQ verification is enabled
this module must be updated to sign over the canonical message.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
import threading
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("mining.uw_inbox")

# Hard cap to keep the inbox from growing unbounded if the scanner is slow.
_MAX_QUEUE = int(os.getenv("ANIMICA_UW_INBOX_MAX", "256") or 256)

_LOCK = threading.Lock()
_QUEUE: List[str] = []


def _sha3(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _normalize_model_id(record: Any) -> str:
    """Best-effort model id extraction across the worker kinds."""
    metrics = getattr(record, "metrics", None) or {}
    if isinstance(metrics, dict):
        for k in ("model", "model_id", "modelId"):
            v = metrics.get(k)
            if isinstance(v, str) and v:
                return v
    return f"animica.{getattr(record, 'kind', 'work').lower()}"


def _normalize_steps_tokens(record: Any) -> tuple[int, int]:
    metrics = getattr(record, "metrics", None) or {}
    if not isinstance(metrics, dict):
        return 1, 1
    # Different worker kinds report different counters; coerce to positive ints
    # so the verifier accepts them (it rejects steps/tokens <= 0).
    steps = (
        metrics.get("steps")
        or metrics.get("iterations")
        or metrics.get("t_iter")
        or metrics.get("ai_units")
        or 1
    )
    tokens = (
        metrics.get("tokens")
        or metrics.get("max_tokens")
        or metrics.get("bytes_read")
        or metrics.get("ai_units")
        or 1
    )
    try:
        steps_i = max(1, min(int(steps), 1_000_000_000 - 1))
    except Exception:
        steps_i = 1
    try:
        tokens_i = max(1, min(int(tokens), 1_000_000_000_000 - 1))
    except Exception:
        tokens_i = 1
    return steps_i, tokens_i


def _build_compute_receipt_proof(record: Any) -> Optional[str]:
    """Convert a worker ResultRecord into a hex-encoded UsefulWorkProof."""
    try:
        from core.encoding.cbor import dumps as cbor_dumps
        from core.usefulwork import UsefulWorkProof, encode_proof_to_hex
    except Exception as exc:  # pragma: no cover - core.usefulwork is bundled
        log.debug("uw_inbox: usefulwork module unavailable: %s", exc)
        return None

    output_digest = getattr(record, "output_digest", None)
    if not isinstance(output_digest, (bytes, bytearray)) or len(output_digest) != 32:
        return None

    task_id = getattr(record, "task_id", "") or ""
    provider_id = getattr(record, "provider_id", None) or "miner"
    kind = getattr(record, "kind", "AI")
    completed_at = float(getattr(record, "completed_at", time.time()) or time.time())

    model_id = _normalize_model_id(record)
    steps, tokens = _normalize_steps_tokens(record)

    plan_commitment = _sha3(b"plan:" + kind.encode() + b":" + model_id.encode())
    instance_id = _sha3(b"instance:" + task_id.encode())
    attestation = getattr(record, "attestation", None) or {}
    inputs_hash_hex = (
        attestation.get("inputs_hash_hex") if isinstance(attestation, dict) else None
    )
    if isinstance(inputs_hash_hex, str) and len(inputs_hash_hex) == 64:
        try:
            input_commitment = bytes.fromhex(inputs_hash_hex)
        except Exception:
            input_commitment = _sha3(b"inputs:" + task_id.encode())
    else:
        input_commitment = _sha3(b"inputs:" + task_id.encode())
    output_commitment = bytes(output_digest)
    trace_summary_hash = _sha3(
        b"trace:" + task_id.encode() + b":" + str(completed_at).encode()
    )

    timestamp = int(completed_at)
    # canonical CBOR map matching `verify_compute_receipt` expectations
    receipt = {
        "contributor_id": provider_id,
        "steps": steps,
        "tokens": tokens,
        "model_id": model_id,
        "timestamp": timestamp,
        "trace_summary_hash": trace_summary_hash,
        "signature": b"",  # verifier currently stubs PQ verification
        "public_key": b"",
    }
    try:
        receipt_bytes = cbor_dumps(receipt)
    except Exception as exc:
        log.debug("uw_inbox: cbor encode failed: %s", exc)
        return None

    metadata: Dict[str, Any] = {"kind": kind}
    if isinstance(attestation, dict):
        prov = attestation.get("provider")
        if isinstance(prov, str):
            metadata["provider"] = prov

    try:
        proof = UsefulWorkProof(
            scheme_id="compute.receipt.v1",
            plan_commitment=plan_commitment,
            instance_id=instance_id,
            input_commitment=input_commitment,
            output_commitment=output_commitment,
            receipt_bytes=receipt_bytes,
            metadata=metadata,
        )
        return encode_proof_to_hex(proof)
    except Exception as exc:
        log.debug("uw_inbox: proof build failed: %s", exc)
        return None


def push_result(record: Any) -> bool:
    """
    Convert a worker ResultRecord to a UWP envelope and enqueue it for the
    next outgoing share. Returns True on success.
    """
    proof_hex = _build_compute_receipt_proof(record)
    if proof_hex is None:
        return False
    with _LOCK:
        if len(_QUEUE) >= _MAX_QUEUE:
            # Drop the oldest to keep the inbox bounded — freshest work wins.
            _QUEUE.pop(0)
        _QUEUE.append(proof_hex)
    return True


def drain(max_n: int = 4) -> List[str]:
    """Return up to `max_n` pending proof envelopes (hex strings)."""
    if max_n <= 0:
        return []
    with _LOCK:
        if not _QUEUE:
            return []
        out = _QUEUE[:max_n]
        del _QUEUE[:max_n]
        return out


def pending() -> int:
    with _LOCK:
        return len(_QUEUE)


def reset() -> None:
    """Test helper: clear the inbox."""
    with _LOCK:
        _QUEUE.clear()


__all__ = ["push_result", "drain", "pending", "reset"]
