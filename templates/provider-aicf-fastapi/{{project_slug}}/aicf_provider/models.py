"""
Typed request/response models for the AICF Provider FastAPI template.

This module keeps all externally visible JSON shapes in one place so:
- Your FastAPI routes can import and reuse these types directly.
- CLI tools and tests can serialize/deserialize payloads consistently.
- You can evolve wire formats in a controlled, versioned way.

Design notes
------------
- Uses Pydantic v2-style models (FastAPI 0.110+).
- Leans on discriminated unions for {AI|Quantum} job and result payloads.
- Intentionally small but extensible: add fields as your provider grows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (BaseModel, Field, HttpUrl, ValidationError,
                      field_validator)

# -----------------------------------------------------------------------------
# Common / meta models
# -----------------------------------------------------------------------------


class Problem(BaseModel):
    """
    RFC 7807-ish error document for consistent failures.

    Example:
        {
          "title": "Invalid job",
          "status": 400,
          "detail": "shots must be <= 100000",
          "code": "bad_request"
        }
    """

    type: Optional[HttpUrl] = None
    title: str
    status: int
    detail: Optional[str] = None
    instance: Optional[str] = None
    code: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

    @classmethod
    def from_exc(
        cls, exc: Exception, *, status: int = 400, code: Optional[str] = None
    ) -> "Problem":
        if isinstance(exc, ValidationError):
            return cls(
                title="Validation error",
                status=status,
                code=code or "validation_error",
                detail=str(exc),
                data={"errors": exc.errors()},
            )
        return cls(
            title=exc.__class__.__name__, status=status, code=code, detail=str(exc)
        )


class HealthStatus(str, Enum):
    ok = "ok"
    degraded = "degraded"
    down = "down"


class Health(BaseModel):
    """Simple health payload used by /health."""

    status: HealthStatus = HealthStatus.ok
    uptime_s: float = 0.0
    checks: Dict[str, str] = Field(
        default_factory=dict
    )  # e.g. {"rpc": "ok", "queue": "ok"}


class Version(BaseModel):
    """
    Provider identity + feature advertisement (used by /version or IDENTIFY).

    `capabilities` keys are intentionally simple and stable:
    - "ai": supports AI jobs
    - "quantum": supports Quantum jobs
    """

    provider_id: str
    version: str = "0.1.0"
    capabilities: Dict[str, bool] = Field(default_factory=dict)


# -----------------------------------------------------------------------------
# Job models (discriminated unions)
# -----------------------------------------------------------------------------

# ---- Base job ----


class BaseJobIn(BaseModel):
    """
    Base fields for a new job submitted to the provider.

    - `client_job_id`: optional correlation key provided by the client.
    - `priority`: higher numbers are treated as higher priority (implementation-specific).
    - `timeout_s`: hard processing timeout hint (enforced by your worker).
    """

    kind: Literal["ai", "quantum"]
    client_job_id: Optional[str] = None
    priority: int = Field(0, ge=0, le=100)
    timeout_s: Optional[float] = Field(default=None, gt=0)


# ---- AI job ----


class AIJobIn(BaseJobIn):
    kind: Literal["ai"] = "ai"
    prompt: str
    model: Optional[str] = None
    max_tokens: Optional[int] = Field(default=None, gt=0, le=8192)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    stop: Optional[List[str]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


# ---- Quantum job ----


class QuantumJobIn(BaseJobIn):
    kind: Literal["quantum"] = "quantum"
    circuit: Union[str, Dict[str, Any]]  # e.g. OpenQASM string or a JSON IR
    shots: int = Field(default=1000, gt=0, le=100_000)
    include_traps: bool = True
    backend: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("circuit")
    @classmethod
    def _circuit_non_empty(cls, v: Union[str, Dict[str, Any]]):
        if isinstance(v, str) and not v.strip():
            raise ValueError("circuit string must not be empty")
        if isinstance(v, dict) and not v:
            raise ValueError("circuit object must not be empty")
        return v


# Union used by POST /jobs and job validation layers
JobIn = Annotated[Union[AIJobIn, QuantumJobIn], Field(discriminator="kind")]


# -----------------------------------------------------------------------------
# Queueing / status / results
# -----------------------------------------------------------------------------


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    canceled = "canceled"


class JobEnqueued(BaseModel):
    """
    Response returned when a job is accepted by the provider.
    """

    job_id: str
    queued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    position: Optional[int] = None
    estimated_start_s: Optional[float] = Field(default=None, ge=0)


class JobStatus(BaseModel):
    """
    Poll-able status document for any job.
    """

    job_id: str
    state: JobState
    submitted_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    progress: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    last_error: Optional[str] = None
    # Echo essential job traits for UX (without sensitive inputs)
    kind: Literal["ai", "quantum"]
    priority: int = 0


# ---- Results (discriminated) ----


class BaseResult(BaseModel):
    job_id: str
    kind: Literal["ai", "quantum"]
    duration_s: float = Field(ge=0, default=0.0)
    produced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AIResult(BaseResult):
    kind: Literal["ai"] = "ai"
    text: str
    tokens_in: Optional[int] = Field(default=None, ge=0)
    tokens_out: Optional[int] = Field(default=None, ge=0)
    model: Optional[str] = None
    finish_reason: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None  # optional passthrough of provider raw payload


def _is_bitstring(s: str) -> bool:
    return s != "" and all(ch in "01" for ch in s)


class QuantumResult(BaseResult):
    kind: Literal["quantum"] = "quantum"
    # Compact set of measurements as bitstrings, e.g. ["001", "111", ...]
    bitstrings: List[str] = Field(default_factory=list)
    # Aggregated histogram for convenience; keys must be valid bitstrings.
    histogram: Dict[str, int] = Field(default_factory=dict)
    # Trap verification (if you implement trap-based correctness checks)
    trap_checks: Optional[Dict[str, bool]] = None
    backend: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    @field_validator("bitstrings")
    @classmethod
    def _validate_bitstrings(cls, v: List[str]) -> List[str]:
        bad = [b for b in v if not _is_bitstring(b)]
        if bad:
            raise ValueError(f"invalid bitstrings: {bad!r}")
        return v

    @field_validator("histogram")
    @classmethod
    def _validate_histogram(cls, v: Dict[str, int]) -> Dict[str, int]:
        for k, n in v.items():
            if not _is_bitstring(k):
                raise ValueError(f"histogram key is not a bitstring: {k!r}")
            if n < 0:
                raise ValueError(f"histogram count must be >= 0 for {k!r}, got {n}")
        return v


JobResult = Annotated[Union[AIResult, QuantumResult], Field(discriminator="kind")]


# -----------------------------------------------------------------------------
# Listing helpers (simple pagination envelope)
# -----------------------------------------------------------------------------


class JobListResponse(BaseModel):
    items: List[JobStatus]
    next_page_token: Optional[str] = None


# -----------------------------------------------------------------------------
# Convenience factories
# -----------------------------------------------------------------------------


def new_job_status_queued(
    job_id: str, *, kind: Literal["ai", "quantum"], priority: int = 0
) -> JobStatus:
    return JobStatus(
        job_id=job_id,
        state=JobState.queued,
        submitted_at=datetime.now(timezone.utc),
        kind=kind,
        priority=priority,
    )


__all__ = [
    # meta
    "Problem",
    "Health",
    "HealthStatus",
    "Version",
    # jobs
    "BaseJobIn",
    "AIJobIn",
    "QuantumJobIn",
    "JobIn",
    # queue/status/results
    "JobState",
    "JobEnqueued",
    "JobStatus",
    "BaseResult",
    "AIResult",
    "QuantumResult",
    "JobResult",
    # listing
    "JobListResponse",
    # helpers
    "new_job_status_queued",
]
