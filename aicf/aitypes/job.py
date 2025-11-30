from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from aicf.queue.jobkind import JobKind

JobStatus = Literal["queued", "leased", "stale", "done", "canceled"]


@dataclass
class JobRecord:
    """
    Test contract (used by helpers in tests):
      JobRecord(job_id: str, kind: JobKind, fee: int,
                size_bytes: int, created_at: float, tier: str = "standard")
    """

    job_id: str
    kind: JobKind
    fee: int
    size_bytes: int
    created_at: float
    tier: str = "standard"

    # Optional fields used by queue/assignment
    status: JobStatus = "queued"
    assigned_to: Optional[str] = None
    lease_expires_at: Optional[float] = None


@dataclass
class Lease:
    """
    Minimal lease shape expected by queue/assignment tests:
      Lease(job_id: str, provider_id: str, acquired_at: float, expires_at: float)
    """

    job_id: str
    provider_id: str
    acquired_at: float
    expires_at: float


__all__ = ["JobKind", "JobRecord", "JobStatus", "Lease"]
