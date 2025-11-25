from __future__ import annotations
from aicf.queue.jobkind import JobKind
"""
Internal AICF event types.

These events are emitted on the in-process event bus to coordinate the queue,
matcher, SLA evaluator, settlement, and RPC/WebSocket layers. All events are
pure dataclasses with JSON-serializable fields and small helpers to (de)serialize.

Events:
  - Enqueued:  a job was accepted into the queue.
  - Assigned:  a job was leased to a provider.
  - Completed: a provider returned outputs/evidence for a job.
  - Settled:   a payout for a job was recorded.
  - Slashed:   a provider was penalized for a reason.

Timestamps use UNIX milliseconds. IDs are deterministic strings (see queue/ids.py).
"""


from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, Any, Optional, Union, Mapping
import time

from .provider import ProviderId
from .job import JobKind, JobStatus
from .payout import Payout


# ────────────────────────────────────────────────────────────────────────────────
# Common helpers
# ────────────────────────────────────────────────────────────────────────────────

def now_ms() -> int:
    """Current UNIX time in milliseconds (int)."""
    return int(time.time() * 1000)


class EventType(str, Enum):
    ENQUEUED = "Enqueued"
    ASSIGNED = "Assigned"
    COMPLETED = "Completed"
    SETTLED = "Settled"
    SLASHED = "Slashed"


class SlashReason(str, Enum):
    """Enumerates common slash reasons (policy may map these to magnitudes)."""
    MISSED_DEADLINE = "missed_deadline"
    BAD_ATTESTATION = "bad_attestation"
    LOW_TRAPS = "low_traps_ratio"
    LOW_QOS = "low_qos"
    UNAVAILABLE = "unavailable"
    DOUBLE_ASSIGN = "double_assign"
    OTHER = "other"


# ────────────────────────────────────────────────────────────────────────────────
# Event payloads
# ────────────────────────────────────────────────────────────────────────────────

@dataclass
class Enqueued:
    etype: EventType
    ts_ms: int
    job_id: str
    kind: JobKind
    requester: Optional[str] = None  # bech32m address (if known)
    units: Optional[int] = None      # normalized job units (AI/Quantum-specific)
    status: JobStatus = JobStatus.QUEUED
    priority_score: Optional[float] = None

    @staticmethod
    def new(job_id: str, kind: JobKind, requester: Optional[str] = None,
            units: Optional[int] = None, priority_score: Optional[float] = None) -> "Enqueued":
        return Enqueued(
            etype=EventType.ENQUEUED,
            ts_ms=now_ms(),
            job_id=job_id,
            kind=kind,
            requester=requester,
            units=units,
            status=JobStatus.QUEUED,
            priority_score=priority_score,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["etype"] = self.etype.value
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Enqueued":
        return Enqueued(
            etype=EventType(d["etype"]),
            ts_ms=int(d["ts_ms"]),
            job_id=str(d["job_id"]),
            kind=JobKind(d["kind"]),
            requester=d.get("requester"),
            units=int(d["units"]) if d.get("units") is not None else None,
            status=JobStatus(d.get("status", JobStatus.QUEUED.value)),
            priority_score=float(d["priority_score"]) if d.get("priority_score") is not None else None,
        )


@dataclass
class Assigned:
    etype: EventType
    ts_ms: int
    job_id: str
    provider_id: ProviderId
    lease_id: str
    lease_expires_ms: int
    # For observability:
    previous_attempts: int = 0

    @staticmethod
    def new(job_id: str, provider_id: ProviderId, lease_id: str,
            lease_expires_ms: int, previous_attempts: int = 0) -> "Assigned":
        return Assigned(
            etype=EventType.ASSIGNED,
            ts_ms=now_ms(),
            job_id=job_id,
            provider_id=provider_id,
            lease_id=lease_id,
            lease_expires_ms=int(lease_expires_ms),
            previous_attempts=int(previous_attempts),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["etype"] = self.etype.value
        d["provider_id"] = str(self.provider_id)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Assigned":
        return Assigned(
            etype=EventType(d["etype"]),
            ts_ms=int(d["ts_ms"]),
            job_id=str(d["job_id"]),
            provider_id=ProviderId(str(d["provider_id"])),
            lease_id=str(d["lease_id"]),
            lease_expires_ms=int(d["lease_expires_ms"]),
            previous_attempts=int(d.get("previous_attempts", 0)),
        )


@dataclass
class Completed:
    etype: EventType
    ts_ms: int
    job_id: str
    provider_id: ProviderId
    success: bool
    # References & telemetry (hashes/ids are hex strings; metrics are small JSON numbers)
    task_id: Optional[str] = None          # capabilities/jobs deterministic id
    proof_type: Optional[str] = None       # "AIProof" | "QuantumProof" | ...
    proof_nullifier: Optional[str] = None  # domain-separated nullifier (hex)
    metrics: Optional[Dict[str, float]] = None  # traps_ratio, qos, latency_ms, etc.
    note: Optional[str] = None

    @staticmethod
    def ok(job_id: str, provider_id: ProviderId, *,
           task_id: Optional[str] = None, proof_type: Optional[str] = None,
           proof_nullifier: Optional[str] = None,
           metrics: Optional[Dict[str, float]] = None, note: Optional[str] = None) -> "Completed":
        return Completed(
            etype=EventType.COMPLETED,
            ts_ms=now_ms(),
            job_id=job_id,
            provider_id=provider_id,
            success=True,
            task_id=task_id,
            proof_type=proof_type,
            proof_nullifier=proof_nullifier,
            metrics=metrics,
            note=note,
        )

    @staticmethod
    def fail(job_id: str, provider_id: ProviderId, *,
             note: Optional[str] = None, metrics: Optional[Dict[str, float]] = None) -> "Completed":
        return Completed(
            etype=EventType.COMPLETED,
            ts_ms=now_ms(),
            job_id=job_id,
            provider_id=provider_id,
            success=False,
            task_id=None,
            proof_type=None,
            proof_nullifier=None,
            metrics=metrics,
            note=note,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["etype"] = self.etype.value
        d["provider_id"] = str(self.provider_id)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Completed":
        return Completed(
            etype=EventType(d["etype"]),
            ts_ms=int(d["ts_ms"]),
            job_id=str(d["job_id"]),
            provider_id=ProviderId(str(d["provider_id"])),
            success=bool(d["success"]),
            task_id=d.get("task_id"),
            proof_type=d.get("proof_type"),
            proof_nullifier=d.get("proof_nullifier"),
            metrics=dict(d["metrics"]) if d.get("metrics") is not None else None,
            note=d.get("note"),
        )


@dataclass
class Settled:
    etype: EventType
    ts_ms: int
    job_id: str
    provider_id: ProviderId
    payout: Payout  # includes split details & amounts

    @staticmethod
    def new(job_id: str, provider_id: ProviderId, payout: Payout) -> "Settled":
        return Settled(
            etype=EventType.SETTLED,
            ts_ms=now_ms(),
            job_id=job_id,
            provider_id=provider_id,
            payout=payout,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["etype"] = self.etype.value
        d["provider_id"] = str(self.provider_id)
        # ensure payout is JSON-y
        d["payout"] = self.payout.to_dict() if hasattr(self.payout, "to_dict") else asdict(self.payout)
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Settled":
        # Payout has a from_dict in aicf/types/payout.py
        payout_dict = d["payout"]
        payout = Payout.from_dict(payout_dict) if hasattr(Payout, "from_dict") else Payout(**payout_dict)
        return Settled(
            etype=EventType(d["etype"]),
            ts_ms=int(d["ts_ms"]),
            job_id=str(d["job_id"]),
            provider_id=ProviderId(str(d["provider_id"])),
            payout=payout,
        )


@dataclass
class Slashed:
    etype: EventType
    ts_ms: int
    provider_id: ProviderId
    reason: SlashReason
    amount: int  # slash amount in minimal units
    job_id: Optional[str] = None  # if tied to a job context
    note: Optional[str] = None

    @staticmethod
    def new(provider_id: ProviderId, reason: SlashReason, amount: int,
            job_id: Optional[str] = None, note: Optional[str] = None) -> "Slashed":
        if amount < 0:
            raise ValueError("slash amount must be >= 0")
        return Slashed(
            etype=EventType.SLASHED,
            ts_ms=now_ms(),
            provider_id=provider_id,
            reason=reason,
            amount=int(amount),
            job_id=job_id,
            note=note,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["etype"] = self.etype.value
        d["provider_id"] = str(self.provider_id)
        d["reason"] = self.reason.value
        return d

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Slashed":
        return Slashed(
            etype=EventType(d["etype"]),
            ts_ms=int(d["ts_ms"]),
            provider_id=ProviderId(str(d["provider_id"])),
            reason=SlashReason(d["reason"]),
            amount=int(d["amount"]),
            job_id=d.get("job_id"),
            note=d.get("note"),
        )


# Union of all events
AicfEvent = Union[Enqueued, Assigned, Completed, Settled, Slashed]


# ────────────────────────────────────────────────────────────────────────────────
# Generic (de)serialization
# ────────────────────────────────────────────────────────────────────────────────

def serialize_event(ev: AicfEvent) -> Dict[str, Any]:
    """Serialize any AICF event to a JSON-serializable dict."""
    return ev.to_dict()  # type: ignore[attr-defined]


def deserialize_event(d: Mapping[str, Any]) -> AicfEvent:
    """Instantiate a concrete event from a dict with an 'etype' discriminator."""
    etype = EventType(d["etype"])
    if etype is EventType.ENQUEUED:
        return Enqueued.from_dict(d)
    if etype is EventType.ASSIGNED:
        return Assigned.from_dict(d)
    if etype is EventType.COMPLETED:
        return Completed.from_dict(d)
    if etype is EventType.SETTLED:
        return Settled.from_dict(d)
    if etype is EventType.SLASHED:
        return Slashed.from_dict(d)
    raise ValueError(f"Unknown event etype: {etype!r}")


__all__ = [
    "EventType",
    "SlashReason",
    "Enqueued",
    "Assigned",
    "Completed",
    "Settled",
    "Slashed",
    "AicfEvent",
    "serialize_event",
    "deserialize_event",
    "now_ms",
]
