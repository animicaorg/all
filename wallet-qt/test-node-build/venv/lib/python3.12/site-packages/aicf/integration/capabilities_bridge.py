from __future__ import annotations

from aicf.queue.jobkind import JobKind

"""
aicf.integration.capabilities_bridge
------------------------------------

A small glue layer that takes compute-enqueue calls originating from
`capabilities.host.compute` (AI / Quantum) and inserts them into the AICF
queue subsystem.

Design goals:
  * Deterministic task IDs: exactly the same derivation as capabilities/jobs/id.py
    (H(chainId | height | txHash | caller | payload_bytes)).
  * Loose coupling: duck-type the queue storage so this module can be wired in
    devnet or production without tight import constraints.
  * Minimal return surface: return (task_id, receipt) so the capabilities host
    can hand a JobReceipt back to the contract runtime immediately.

This module intentionally avoids importing heavy subsystems. If optional
helpers (e.g. receipts builder) are present, we use them; otherwise we return
a lightweight dict receipt with the essential fields.

"""


import hashlib
import json
from dataclasses import asdict
from typing import (Any, Callable, Mapping, Optional, Protocol, Tuple,
                    runtime_checkable)

# Types from AICF and Capabilities (used in type hints only; keep imports optional at runtime)
try:
    from aicf.aitypes.job import JobKind  # type: ignore
except Exception:  # pragma: no cover

    class JobKind:  # type: ignore
        AI = "AI"
        QUANTUM = "QUANTUM"


try:
    from capabilities.jobs import receipts as _cap_receipts  # type: ignore
except Exception:  # pragma: no cover
    _cap_receipts = None  # fallback later


# --------------------------- helpers & utils ---------------------------------


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _b(x: Any) -> bytes:
    """Best-effort conversion to bytes for task-id derivation inputs."""
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        # accept hex strings like 0xabc...
        if x.startswith("0x"):
            try:
                return bytes.fromhex(x[2:])
            except ValueError:
                pass
        return x.encode("utf-8")
    if isinstance(x, int):
        # big-endian, minimal width
        if x < 0:
            raise ValueError("negative integers not supported in task-id derivation")
        width = max(1, (x.bit_length() + 7) // 8)
        return x.to_bytes(width, "big")
    if isinstance(x, Mapping):
        return _canon_bytes(x)
    # dataclasses or objects: shallow to-dict then canonicalize
    try:
        return _canon_bytes(asdict(x))  # type: ignore[arg-type]
    except Exception:
        return _canon_bytes(_as_public_dict(x))


def _canon_bytes(obj: Any) -> bytes:
    """
    Canonical, deterministic serialization for payload hashing.
    We use JSON with sorted keys and no whitespace; byte blobs should be
    pre-hashed to hex to keep this stable.
    """

    def _normalize(v: Any) -> Any:
        if isinstance(v, bytes):
            return "0x" + v.hex()
        if isinstance(v, dict):
            return {k: _normalize(v[k]) for k in sorted(v.keys())}
        if isinstance(v, (list, tuple)):
            return [_normalize(x) for x in v]
        return v

    norm = _normalize(obj)
    return json.dumps(
        norm, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _as_public_dict(obj: Any) -> Mapping[str, Any]:
    try:
        return {k: getattr(obj, k) for k in dir(obj) if not k.startswith("_")}
    except Exception:
        return {"repr": repr(obj)}


def _to_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _derive_task_id(*parts: Any) -> str:
    """
    Deterministic task-id derivation:

        task_id = H( chainId | height | txHash | caller | payload_bytes )

    where each element is canonicalized to bytes by `_b`.
    """
    h = hashlib.sha3_256()
    for p in parts:
        pb = _b(p)
        # prefix with length varint-like (simple 4-byte big-endian) to avoid ambiguity
        h.update(len(pb).to_bytes(4, "big"))
        h.update(pb)
    return _to_hex(h.digest())


def _digest_payload(payload_bytes: bytes) -> str:
    return _to_hex(_sha3_256(payload_bytes))


# --------------------------- queue interfaces --------------------------------


@runtime_checkable
class _QueueStorageLike(Protocol):
    """
    Minimal protocol for a queue storage backend. We purposefully keep this
    tiny; concrete implementations can have richer APIs.
    """

    def enqueue(  # noqa: D401
        self,
        *,
        kind: str,
        task_id: str,
        requester: str,
        spec: Mapping[str, Any],
        fee: Optional[int] = None,
        tier: Optional[str] = None,
        priority_hint: Optional[float] = None,
        payload_digest: Optional[str] = None,
        extras: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """
        Insert a job into persistent storage and return a job record id
        (often equal to task_id).
        """
        ...


# ------------------------------ main bridge ----------------------------------


class CapabilitiesAICFBridge:
    """
    Bridge that turns capability-host enqueue calls into AICF queue inserts.

    Instantiate with a queue storage backend and an optional `on_enqueued`
    callback to poke a dispatcher.
    """

    def __init__(
        self,
        queue_storage: _QueueStorageLike,
        *,
        on_enqueued: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not isinstance(queue_storage, _QueueStorageLike):
            # duck-type check but allow dynamic backends
            missing = [m for m in ("enqueue",) if not hasattr(queue_storage, m)]
            if missing:
                raise TypeError(f"queue_storage missing methods: {missing}")
        self._qs = queue_storage
        self._notify = on_enqueued

    # --------- Public API used by capabilities.host.compute ------------------

    def enqueue_ai(
        self,
        *,
        chain_id: int,
        height: int,
        tx_hash: bytes | str,
        caller: str,
        model: str,
        prompt: bytes | str,
        max_units: Optional[int] = None,
        fee: Optional[int] = None,
        tier: Optional[str] = None,
        qos: Optional[Mapping[str, Any]] = None,
        redundancy: Optional[int] = None,
        priority_hint: Optional[float] = None,
        extras: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[str, Mapping[str, Any]]:
        """
        Enqueue an AI job and return (task_id, receipt-like mapping).
        """
        # canonical payload for task-id derivation (do not include large raw prompt)
        payload_obj = {
            "kind": "AI",
            "model": model,
            "prompt_digest": _digest_payload(_b(prompt)),
            "max_units": max_units,
            "qos": qos,
            "redundancy": redundancy,
        }
        payload_bytes = _canon_bytes(payload_obj)
        task_id = _derive_task_id(chain_id, height, tx_hash, caller, payload_bytes)

        spec = {
            "model": model,
            "prompt_digest": _digest_payload(_b(prompt)),
            "max_units": max_units,
            "qos": qos,
            "redundancy": redundancy,
        }

        # Insert into queue
        self._qs.enqueue(
            kind=JobKind.AI if hasattr(JobKind, "AI") else "AI",
            task_id=task_id,
            requester=caller,
            spec=spec,
            fee=fee,
            tier=tier,
            priority_hint=priority_hint,
            payload_digest=_to_hex(_sha3_256(payload_bytes)),
            extras=extras,
        )

        if self._notify:
            try:
                self._notify(task_id)
            except Exception:
                # Notification is best-effort; do not fail the enqueue path
                pass

        return task_id, _build_receipt_fallback(
            kind="AI",
            task_id=task_id,
            chain_id=chain_id,
            height=height,
            caller=caller,
            fee=fee,
            tier=tier,
            payload_digest=_to_hex(_sha3_256(payload_bytes)),
        )

    def enqueue_quantum(
        self,
        *,
        chain_id: int,
        height: int,
        tx_hash: bytes | str,
        caller: str,
        circuit: Mapping[str, Any] | bytes | str,
        shots: int,
        depth: Optional[int] = None,
        width: Optional[int] = None,
        fee: Optional[int] = None,
        tier: Optional[str] = None,
        traps_ratio: Optional[float] = None,
        priority_hint: Optional[float] = None,
        extras: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[str, Mapping[str, Any]]:
        """
        Enqueue a Quantum job and return (task_id, receipt-like mapping).
        """
        payload_obj = {
            "kind": "QUANTUM",
            "circuit_digest": _digest_payload(_b(circuit)),
            "shots": shots,
            "depth": depth,
            "width": width,
            "traps_ratio": traps_ratio,
        }
        payload_bytes = _canon_bytes(payload_obj)
        task_id = _derive_task_id(chain_id, height, tx_hash, caller, payload_bytes)

        spec = {
            "circuit_digest": _digest_payload(_b(circuit)),
            "shots": shots,
            "depth": depth,
            "width": width,
            "traps_ratio": traps_ratio,
        }

        self._qs.enqueue(
            kind=JobKind.QUANTUM if hasattr(JobKind, "QUANTUM") else "QUANTUM",
            task_id=task_id,
            requester=caller,
            spec=spec,
            fee=fee,
            tier=tier,
            priority_hint=priority_hint,
            payload_digest=_to_hex(_sha3_256(payload_bytes)),
            extras=extras,
        )

        if self._notify:
            try:
                self._notify(task_id)
            except Exception:
                pass

        return task_id, _build_receipt_fallback(
            kind="QUANTUM",
            task_id=task_id,
            chain_id=chain_id,
            height=height,
            caller=caller,
            fee=fee,
            tier=tier,
            payload_digest=_to_hex(_sha3_256(payload_bytes)),
        )


# ----------------------------- receipt helper --------------------------------


def _build_receipt_fallback(
    *,
    kind: str,
    task_id: str,
    chain_id: int,
    height: int,
    caller: str,
    fee: Optional[int],
    tier: Optional[str],
    payload_digest: str,
) -> Mapping[str, Any]:
    """
    If capabilities.jobs.receipts is available, use it to build a canonical
    JobReceipt; otherwise return a minimal mapping.
    """
    if _cap_receipts and hasattr(_cap_receipts, "build_receipt"):
        try:
            rec = _cap_receipts.build_receipt(  # type: ignore[attr-defined]
                kind=kind,
                task_id=task_id,
                chain_id=chain_id,
                height=height,
                caller=caller,
                fee=fee,
                tier=tier,
                payload_digest=payload_digest,
            )
            # Return as plain mapping to keep the bridge's return surface simple.
            try:
                return asdict(rec)  # dataclass?
            except Exception:
                if isinstance(rec, Mapping):
                    return rec  # already a mapping
                return _as_public_dict(rec)
        except Exception:
            pass

    # Fallback minimal mapping
    return {
        "kind": kind,
        "task_id": task_id,
        "chain_id": chain_id,
        "height": height,
        "caller": caller,
        "fee": fee,
        "tier": tier,
        "payload_digest": payload_digest,
        "status": "QUEUED",
    }


__all__ = ["CapabilitiesAICFBridge"]
