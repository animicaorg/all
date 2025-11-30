from __future__ import annotations

from aicf.queue.jobkind import JobKind

"""
aicf.integration.proofs_bridge
--------------------------------

Map verified on-chain proofs (AI/Quantum) to AICF job-claim records.

This module is intentionally light on dependencies: it accepts either the
strongly-typed proof objects from `proofs.types` OR plain dict-like objects
that expose the expected fields. The AICF side consumes only a small, stable
subset: job kind, task_id, nullifier, and the block height the proof was
included in.

Typical usage (after a block has been verified by the node):

    from aicf.integration.proofs_bridge import (
        claim_from_ai_proof, claim_from_quantum_proof
    )
    claim = claim_from_ai_proof(ai_proof_obj, height=blk.height)
    payouts.enqueue_claim(claim)

If the upstream proof objects evolve, this bridge should remain robust by
duck-typing the attributes we read.

"""


from dataclasses import asdict
from typing import Any, Mapping, Optional

from aicf.aitypes.job import JobKind
from aicf.aitypes.proof_claim import ProofClaim
from aicf.errors import AICFError


def _to_hex(x: Any) -> str:
    """Return a 0x-prefixed lowercase hex string from bytes/int/str."""
    if x is None:
        raise ValueError("expected non-None value")
    if isinstance(x, bytes):
        return "0x" + x.hex()
    if isinstance(x, int):
        # Normalize non-negative integers to hex
        if x < 0:
            raise ValueError("negative integers not supported for hex encoding")
        return hex(x)
    if isinstance(x, str):
        # Assume it's already hex-ish or an id; normalize prefix
        return x if x.startswith("0x") else ("0x" + x)
    raise TypeError(f"cannot hex-encode value of type {type(x)!r}")


def _get(obj: Any, *candidates: str) -> Optional[Any]:
    """
    Try a list of attribute / key names on obj; return first hit or None.
    Supports attribute access and mapping-key access.
    """
    for name in candidates:
        # attr
        if hasattr(obj, name):
            return getattr(obj, name)
        # mapping
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
    return None


def _require(value: Optional[Any], what: str) -> Any:
    if value is None:
        raise AICFError(f"missing required field: {what}")
    return value


def _maybe_to_dict(obj: Any) -> Mapping[str, Any]:
    """
    Best-effort conversion for logging/debug: dataclass → dict, mapping → as-is,
    arbitrary object → shallow attribute dict.
    """
    try:
        return asdict(obj)  # dataclasses
    except Exception:
        if isinstance(obj, Mapping):
            return obj
        try:
            return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}
        except Exception:
            return {"repr": repr(obj)}


def _extract_task_id(proof: Any) -> str:
    """
    Read a deterministic task identifier from an AI/Quantum proof.

    Preferred fields (in order):
      - proof.task_id
      - proof.meta.task_id
      - proof.body.task_id
      - proof["task_id"] / proof["meta"]["task_id"]

    The value may be bytes/str/int; it is normalized to 0x-hex.
    """
    task_id = _get(proof, "task_id")
    if task_id is None:
        meta = _get(proof, "meta") or {}
        task_id = _get(meta, "task_id")
    if task_id is None:
        body = _get(proof, "body") or {}
        task_id = _get(body, "task_id")

    if task_id is None and isinstance(proof, Mapping):
        # nested mapping form
        task_id = (
            proof.get("task_id")
            or proof.get("body", {}).get("task_id")
            or proof.get("meta", {}).get("task_id")
        )

    if task_id is None:
        raise AICFError(
            "AI/Quantum proof does not expose a task_id; "
            "capabilities/jobs MUST embed the deterministic task_id into the proof body."
        )
    return _to_hex(task_id)


def _extract_nullifier(proof_or_envelope: Any) -> str:
    """
    Read the domain-separated nullifier that prevents proof reuse.
    Preferred fields:
      - envelope.nullifier
      - proof.nullifier
      - mapping["nullifier"]
    """
    n = _get(proof_or_envelope, "nullifier")
    if n is None and isinstance(proof_or_envelope, Mapping):
        n = proof_or_envelope.get("nullifier")
    return _to_hex(_require(n, "nullifier"))


def _extract_provider_id(proof: Any) -> Optional[str]:
    """
    Optional provider identity extracted from the proof (if present).
    Common candidates:
      - proof.provider_id
      - proof.meta.provider_id
      - proof.attestation.provider_id
    """
    provider = _get(proof, "provider_id")
    if provider is None:
        meta = _get(proof, "meta") or {}
        provider = _get(meta, "provider_id")
    if provider is None:
        att = _get(proof, "attestation") or {}
        provider = _get(att, "provider_id")
    if provider is None and isinstance(proof, Mapping):
        provider = (
            proof.get("provider_id")
            or proof.get("meta", {}).get("provider_id")
            or proof.get("attestation", {}).get("provider_id")
        )
    if provider is None:
        return None
    # provider ids are typically hex or bech32-like; don't force hex if it doesn't look like bytes.
    try:
        return _to_hex(provider)
    except Exception:
        return str(provider)


def claim_from_ai_proof(
    ai_proof: Any, *, height: int, envelope: Any | None = None
) -> ProofClaim:
    """
    Build a ProofClaim from a verified AIProof object/dict.

    Args:
        ai_proof: Verified AI proof (object from `proofs.types` or dict-like).
        height:   Block height in which the proof was included.
        envelope: Optional envelope exposing `nullifier` if not on the body.

    Returns:
        ProofClaim ready to be fed into payout/settlement logic.

    Raises:
        AICFError if required fields are missing.
    """
    task_id = _extract_task_id(ai_proof)
    nullifier = _extract_nullifier(envelope or ai_proof)
    provider = _extract_provider_id(ai_proof)

    return ProofClaim(
        kind=JobKind.AI,
        task_id=task_id,
        nullifier=nullifier,
        height=int(_require(height, "height")),
        provider_id=provider,
    )


def claim_from_quantum_proof(
    quantum_proof: Any, *, height: int, envelope: Any | None = None
) -> ProofClaim:
    """
    Build a ProofClaim from a verified QuantumProof object/dict.

    Follows the same extraction strategy as AI proofs.
    """
    task_id = _extract_task_id(quantum_proof)
    nullifier = _extract_nullifier(envelope or quantum_proof)
    provider = _extract_provider_id(quantum_proof)

    return ProofClaim(
        kind=JobKind.QUANTUM,
        task_id=task_id,
        nullifier=nullifier,
        height=int(_require(height, "height")),
        provider_id=provider,
    )


def claim_from_envelope(envelope: Any, *, height: int) -> ProofClaim:
    """
    Generic helper if the caller only has a verified proof envelope.

    The function attempts to detect AI vs Quantum by:
      1) Checking `type_id` or `type` name on the envelope/body.
      2) Falling back to presence of fields commonly unique to each proof type.

    For high-assurance code paths, prefer the explicit typed helpers above.
    """
    # Try common type markers
    t = (
        (_get(envelope, "type_id") or _get(envelope, "type") or "").lower()
        if isinstance(_get(envelope, "type") or "", str)
        else _get(envelope, "type_id")
    )
    body = _get(envelope, "body") or envelope

    def _looks_ai(m: Any) -> bool:
        return any(
            _get(m, k) is not None
            for k in ("qos", "redundancy", "trap_receipts", "workload_digest")
        )

    def _looks_quantum(m: Any) -> bool:
        return any(
            _get(m, k) is not None for k in ("trap_circuits", "shots", "depth", "width")
        )

    try:
        if isinstance(t, str) and "ai" in t:
            return claim_from_ai_proof(body, height=height, envelope=envelope)
        if isinstance(t, str) and "quantum" in t:
            return claim_from_quantum_proof(body, height=height, envelope=envelope)
    except Exception:
        # fall through to heuristic path
        pass

    if _looks_ai(body) and not _looks_quantum(body):
        return claim_from_ai_proof(body, height=height, envelope=envelope)
    if _looks_quantum(body) and not _looks_ai(body):
        return claim_from_quantum_proof(body, height=height, envelope=envelope)

    # If ambiguous, raise with context for the caller to resolve.
    raise AICFError(
        "Unable to infer proof kind (AI vs Quantum) from envelope. "
        "Provide a typed proof to claim_from_ai_proof/claim_from_quantum_proof.\n"
        f"envelope={_maybe_to_dict(envelope)}"
    )


__all__ = [
    "claim_from_ai_proof",
    "claim_from_quantum_proof",
    "claim_from_envelope",
]
