from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, Optional


class JobKind(IntEnum):
    """
    Kind of off-chain job handled by the capabilities subsystem.

    - AI:     TEE-attested AI compute (model + prompt, etc.)
    - QUANTUM: Trap-circuit based quantum job (circuit, shots, params)
    """

    AI = 1
    QUANTUM = 2


UnixTime = int  # seconds since epoch


@dataclass(frozen=True, slots=True)
class JobRequest:
    """
    Canonical request envelope queued by the capabilities provider layer.

    Fields:
        kind:           Which pipeline handles this job.
        caller:         32-byte account/address payload (raw bytes; bech32 handled elsewhere).
        chain_id:       CAIP-2 chain id numeric component (e.g., 1 for animica:1).
        height_hint:    Optional height the request is tied to (for deterministic task id salts).
        payload:        Kind-specific payload (e.g., {"model": "...", "prompt": "..."}).
        created_at:     Unix time when the request object was formed (seconds).
    """

    kind: JobKind
    caller: bytes
    chain_id: int
    payload: Dict[str, Any]
    height_hint: Optional[int] = None
    created_at: UnixTime = field(default_factory=lambda: int(time.time()))

    def __post_init__(self) -> None:
        # Lightweight sanity checks without over-constraining formats.
        if not isinstance(self.kind, JobKind):
            raise TypeError("kind must be a JobKind")
        if not isinstance(self.caller, (bytes, bytearray)) or len(self.caller) == 0:
            raise ValueError("caller must be non-empty bytes")
        if self.chain_id <= 0:
            raise ValueError("chain_id must be a positive integer")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")

    # Convenience constructors (keep business logic out of callers)
    @staticmethod
    def ai(
        caller: bytes,
        chain_id: int,
        model: str,
        prompt: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        height_hint: Optional[int] = None,
    ) -> "JobRequest":
        payload: Dict[str, Any] = {"model": model, "prompt": prompt}
        if params:
            payload["params"] = params
        return JobRequest(
            kind=JobKind.AI,
            caller=caller,
            chain_id=chain_id,
            payload=payload,
            height_hint=height_hint,
        )

    @staticmethod
    def quantum(
        caller: bytes,
        chain_id: int,
        circuit: Dict[str, Any],
        shots: int,
        *,
        params: Optional[Dict[str, Any]] = None,
        height_hint: Optional[int] = None,
    ) -> "JobRequest":
        if shots <= 0:
            raise ValueError("shots must be > 0")
        payload: Dict[str, Any] = {"circuit": circuit, "shots": shots}
        if params:
            payload["params"] = params
        return JobRequest(
            kind=JobKind.QUANTUM,
            caller=caller,
            chain_id=chain_id,
            payload=payload,
            height_hint=height_hint,
        )


@dataclass(frozen=True, slots=True)
class JobReceipt:
    """
    Deterministic receipt returned at enqueue time.

    Fields:
        task_id:        32-byte deterministic id (H(chainId|height|txHash|caller|payload)) — exact
                        derivation is implemented in capabilities.jobs.id. Represented as raw bytes.
        kind:           Mirrors the request kind.
        caller:         Mirrors request caller.
        chain_id:       Mirrors request chain id.
        height_hint:    Mirrors request height_hint (may be None until bound).
        created_at:     Unix time when enqueued.
        note:           Optional human/diagnostic note (non-consensus).
    """

    task_id: bytes
    kind: JobKind
    caller: bytes
    chain_id: int
    height_hint: Optional[int]
    created_at: UnixTime
    note: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, (bytes, bytearray)) or len(self.task_id) == 0:
            raise ValueError("task_id must be non-empty bytes")
        if self.chain_id <= 0:
            raise ValueError("chain_id must be a positive integer")


@dataclass(frozen=True, slots=True)
class ResultRecord:
    """
    Persistent record representing the outcome of a job. Produced when on-chain
    proofs are observed (or when a devnet stub injects a result), and made
    available to contracts in the *next* block deterministically.

    Fields:
        task_id:            Task id this result corresponds to.
        kind:               Mirrors job kind.
        success:            True iff the job completed successfully and passed validation.
        height_available:   Block height from which `read_result(task_id)` is allowed.
        output_digest:      Digest of the returned payload (e.g., SHA3-256 bytes). Can be empty on failure.
        output_pointer:     Optional pointer to content (e.g., DA commitment, artifact id).
        metrics:            Key metrics used for pricing/ψ mapping (opaque to this layer).
        error:              Optional textual reason on failure (non-consensus).
        completed_at:       Unix time when the record was finalized.
    """

    task_id: bytes
    kind: JobKind
    success: bool
    height_available: int
    output_digest: bytes = b""
    output_pointer: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    completed_at: UnixTime = field(default_factory=lambda: int(time.time()))

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, (bytes, bytearray)) or len(self.task_id) == 0:
            raise ValueError("task_id must be non-empty bytes")
        if self.height_available < 0:
            raise ValueError("height_available must be >= 0")


__all__ = [
    "JobKind",
    "JobRequest",
    "JobReceipt",
    "ResultRecord",
    "UnixTime",
]
