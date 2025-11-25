"""
capabilities.host.compute
=========================

Deterministic host-side providers for off-chain compute syscalls:

- ai_enqueue(model: str, prompt: bytes) -> JobReceipt
- quantum_enqueue(circuit: bytes|dict, shots: int, *, extras: dict|None = None) -> JobReceipt

Behavior
--------
* Validates inputs against configured caps (size, shots, id length).
* Derives a deterministic task_id using chain context (chainId, height, txHash, caller)
  and a domain-separated digest of the payload.
* Prefer delegation to adapters.aicf.enqueue_* to persist and schedule jobs.
  Falls back to a local queue (capabilities.jobs.queue) when adapter is unavailable.
* Returns a minimal, deterministic receipt dict:
    {
        "task_id": bytes,      # 32-byte id
        "kind": "AI"|"Quantum",
        "height": int,         # block height at enqueue
        "provider": "adapter.aicf" | "local.queue",
    }

Notes
-----
Receipts are intentionally compact and deterministic. Full result records are
retrieved via capabilities.host.result_read (bound elsewhere) on/after the next
block once proofs have been resolved.

This module is designed to be imported by the central ProviderRegistry in
`capabilities.host.provider`. It marks handlers as deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union
import logging
import hashlib

from .provider import (
    SyscallContext,
    ProviderRegistry,
    get_registry,
    AI_ENQUEUE,
    QUANTUM_ENQUEUE,
)
from ..errors import CapError, LimitExceeded, NotDeterministic

log = logging.getLogger("capabilities.host.compute")

# ----------------------------
# Config & limits
# ----------------------------

# Pull caps from config if available; otherwise use sane defaults for devnets/tests.
try:
    from ..config import (
        AI_MAX_PROMPT_BYTES as _AI_MAX_PROMPT_BYTES,          # type: ignore[attr-defined]
        AI_MAX_MODEL_ID_LEN as _AI_MAX_MODEL_ID_LEN,          # type: ignore[attr-defined]
        Q_MAX_CIRCUIT_BYTES as _Q_MAX_CIRCUIT_BYTES,          # type: ignore[attr-defined]
        Q_MAX_SHOTS as _Q_MAX_SHOTS,                          # type: ignore[attr-defined]
    )
except Exception:  # pragma: no cover - defaults for when config isn't wired yet
    _AI_MAX_PROMPT_BYTES = 64 * 1024
    _AI_MAX_MODEL_ID_LEN = 64
    _Q_MAX_CIRCUIT_BYTES = 128 * 1024
    _Q_MAX_SHOTS = 8192

# Deterministic CBOR encoder (preferred) with a JSON fallback (still deterministic).
_CBOR_OK = False
try:
    from ..cbor.codec import dumps as _cbor_dumps  # type: ignore
    _CBOR_OK = True
except Exception:  # pragma: no cover
    _cbor_dumps = None

try:
    import json
    def _json_dumps_det(obj: Any) -> bytes:
        # Deterministic JSON (sorted keys, no whitespace), UTF-8 bytes
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
except Exception as _e:  # pragma: no cover
    _e  # silence linter
    def _json_dumps_det(obj: Any) -> bytes:
        raise CapError("No serializer available for quantum circuit.")


# Deterministic task-id derivation
try:
    from ..jobs.id import derive_task_id as _derive_task_id  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    def _derive_task_id(*, chain_id: int, height: int, tx_hash: bytes, caller: bytes, payload_digest: bytes) -> bytes:
        """Fallback: domain-separated SHA3-256 over context||payload_digest."""
        h = hashlib.sha3_256()
        h.update(b"animica:capabilities:task-id:v1")
        h.update(chain_id.to_bytes(8, "big", signed=False))
        h.update(height.to_bytes(8, "big", signed=False))
        if not isinstance(tx_hash, (bytes, bytearray)) or len(tx_hash) == 0:
            raise NotDeterministic("tx_hash missing for deterministic task-id")
        if not isinstance(caller, (bytes, bytearray)) or len(caller) == 0:
            raise NotDeterministic("caller missing for deterministic task-id")
        h.update(bytes(tx_hash))
        h.update(bytes(caller))
        h.update(payload_digest)
        return h.digest()


# Local queue fallback
_HAS_LOCAL_QUEUE = False
try:
    from ..jobs.queue import enqueue_job as _enqueue_job  # type: ignore[attr-defined]
    from ..jobs.types import JobKind, JobRequest  # type: ignore[attr-defined]
    _HAS_LOCAL_QUEUE = True
except Exception:  # pragma: no cover
    _enqueue_job = None
    JobKind = None
    JobRequest = None


# Optional AICF adapter
_HAS_AICF = False
try:
    from ..adapters import aicf as _aicf  # type: ignore
    _HAS_AICF = True
except Exception:  # pragma: no cover
    _aicf = None


# ----------------------------
# Helpers
# ----------------------------

def _hash_ai_payload(model: str, prompt: bytes) -> bytes:
    h = hashlib.sha3_256()
    h.update(b"animica:cap:ai:payload:v1")
    h.update(len(model).to_bytes(2, "big"))
    h.update(model.encode("utf-8", errors="strict"))
    h.update(len(prompt).to_bytes(4, "big"))
    h.update(prompt)
    return h.digest()


def _hash_quantum_payload(circuit_bytes: bytes, shots: int, extras: Optional[Dict[str, Any]]) -> bytes:
    h = hashlib.sha3_256()
    h.update(b"animica:cap:quantum:payload:v1")
    h.update(len(circuit_bytes).to_bytes(4, "big"))
    h.update(circuit_bytes)
    h.update(shots.to_bytes(4, "big"))
    if extras:
        # Canonicalize extras via deterministic serialization.
        if _CBOR_OK:
            eb = _cbor_dumps(extras)  # type: ignore[misc]
        else:
            eb = _json_dumps_det(extras)
        h.update(len(eb).to_bytes(4, "big"))
        h.update(eb)
    else:
        h.update((0).to_bytes(4, "big"))
    return h.digest()


def _mk_receipt(task_id: bytes, kind: str, provider: str, height: int) -> Dict[str, Any]:
    if not isinstance(task_id, (bytes, bytearray)) or len(task_id) != 32:
        # We tolerate other sizes but strongly prefer 32 bytes (sha3_256)
        log.debug("task_id length not 32 bytes; still returning", extra={"len": len(task_id)})
    return {
        "task_id": bytes(task_id),
        "kind": kind,
        "height": int(height),
        "provider": provider,
    }


# ----------------------------
# Providers
# ----------------------------

def _ai_enqueue(ctx: SyscallContext, *, model: str, prompt: Union[bytes, bytearray]) -> Dict[str, Any]:
    """Enqueue an AI job deterministically and return a minimal receipt."""
    if not isinstance(model, str):
        raise CapError("ai_enqueue: model must be a string")
    if not isinstance(prompt, (bytes, bytearray)):
        raise CapError("ai_enqueue: prompt must be bytes")
    if len(model.encode("utf-8")) > _AI_MAX_MODEL_ID_LEN:
        raise LimitExceeded(f"ai_enqueue: model id too long (>{_AI_MAX_MODEL_ID_LEN} bytes utf-8)")
    if len(prompt) == 0:
        raise CapError("ai_enqueue: prompt cannot be empty")
    if len(prompt) > _AI_MAX_PROMPT_BYTES:
        raise LimitExceeded(f"ai_enqueue: prompt exceeds {_AI_MAX_PROMPT_BYTES} bytes")

    payload_digest = _hash_ai_payload(model, bytes(prompt))

    task_id = _derive_task_id(
        chain_id=ctx.chain_id,
        height=ctx.height,
        tx_hash=ctx.tx_hash,
        caller=ctx.caller,
        payload_digest=payload_digest,
    )

    # Prefer the AICF adapter (global scheduler) if available.
    if _HAS_AICF and hasattr(_aicf, "enqueue_ai"):
        _aicf.enqueue_ai(  # type: ignore[attr-defined]
            ctx,
            task_id=task_id,
            model=model,
            prompt=bytes(prompt),
        )
        return _mk_receipt(task_id, "AI", "adapter.aicf", ctx.height)

    # Fallback to local queue if present.
    if _HAS_LOCAL_QUEUE and JobRequest is not None and JobKind is not None:
        jr = JobRequest(  # type: ignore[call-arg]
            task_id=task_id,
            kind=JobKind.AI,  # type: ignore[attr-defined]
            height=ctx.height,
            caller=bytes(ctx.caller),
            payload={"model": model, "prompt": bytes(prompt)},
        )
        _enqueue_job(ctx, jr)  # type: ignore[misc]
        return _mk_receipt(task_id, "AI", "local.queue", ctx.height)

    # If neither path exists, we still return a deterministic receipt so tests/devnets can proceed.
    log.warning("ai_enqueue: no adapter/queue available; returning receipt only (no persistence)")
    return _mk_receipt(task_id, "AI", "none", ctx.height)


def _quantum_enqueue(
    ctx: SyscallContext,
    *,
    circuit: Union[bytes, bytearray, Dict[str, Any]],
    shots: int,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Enqueue a Quantum job deterministically and return a minimal receipt."""
    # Normalize circuit into deterministic bytes
    if isinstance(circuit, (bytes, bytearray)):
        c_bytes = bytes(circuit)
    elif isinstance(circuit, dict):
        c_bytes = _cbor_dumps(circuit) if _CBOR_OK else _json_dumps_det(circuit)
    else:
        raise CapError("quantum_enqueue: circuit must be bytes or dict")

    if len(c_bytes) == 0:
        raise CapError("quantum_enqueue: circuit cannot be empty")
    if len(c_bytes) > _Q_MAX_CIRCUIT_BYTES:
        raise LimitExceeded(f"quantum_enqueue: circuit exceeds {_Q_MAX_CIRCUIT_BYTES} bytes")
    if not isinstance(shots, int) or shots <= 0:
        raise CapError("quantum_enqueue: shots must be a positive int")
    if shots > _Q_MAX_SHOTS:
        raise LimitExceeded(f"quantum_enqueue: shots exceeds max of {_Q_MAX_SHOTS}")

    payload_digest = _hash_quantum_payload(c_bytes, shots, extras)

    task_id = _derive_task_id(
        chain_id=ctx.chain_id,
        height=ctx.height,
        tx_hash=ctx.tx_hash,
        caller=ctx.caller,
        payload_digest=payload_digest,
    )

    # Prefer the AICF adapter if available.
    if _HAS_AICF and hasattr(_aicf, "enqueue_quantum"):
        _aicf.enqueue_quantum(  # type: ignore[attr-defined]
            ctx,
            task_id=task_id,
            circuit=c_bytes,
            shots=shots,
            extras=extras or {},
        )
        return _mk_receipt(task_id, "Quantum", "adapter.aicf", ctx.height)

    # Fallback to local queue if present.
    if _HAS_LOCAL_QUEUE and JobRequest is not None and JobKind is not None:
        jr = JobRequest(  # type: ignore[call-arg]
            task_id=task_id,
            kind=JobKind.Quantum,  # type: ignore[attr-defined]
            height=ctx.height,
            caller=bytes(ctx.caller),
            payload={"circuit": c_bytes, "shots": shots, "extras": extras or {}},
        )
        _enqueue_job(ctx, jr)  # type: ignore[misc]
        return _mk_receipt(task_id, "Quantum", "local.queue", ctx.height)

    log.warning("quantum_enqueue: no adapter/queue available; returning receipt only (no persistence)")
    return _mk_receipt(task_id, "Quantum", "none", ctx.height)


# Mark as deterministic (checked by registry)
_ai_enqueue._deterministic = True          # type: ignore[attr-defined]
_quantum_enqueue._deterministic = True     # type: ignore[attr-defined]


def register(registry: ProviderRegistry) -> None:
    """Register handlers into the provided registry."""
    registry.register(AI_ENQUEUE, _ai_enqueue)
    registry.register(QUANTUM_ENQUEUE, _quantum_enqueue)


# Auto-register on import (idempotent)
try:  # pragma: no cover - trivial
    register(get_registry())
except Exception as _e:  # pragma: no cover
    log.debug("compute provider auto-register skipped", extra={"reason": repr(_e)})


__all__ = ["register"]
