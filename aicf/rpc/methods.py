from __future__ import annotations
from aicf.queue.jobkind import JobKind

"""
aicf.rpc.methods
----------------

JSON-RPC style method implementations for the AICF (AI/Quantum Compute Fund).

Exposed methods (bind via `make_methods`):
  • aicf.listProviders
  • aicf.getProvider
  • aicf.listJobs
  • aicf.getJob
  • aicf.claimPayout
  • aicf.getBalance

Design:
  - This module is transport-agnostic. It returns a dict of callables that a
    JSON-RPC dispatcher can register. A separate mount module can expose the
    same callables via FastAPI REST if desired.
  - We accept a "service" object with minimal capabilities (registry/queue/
    treasury views). This avoids tight coupling to storage or adapters.

Usage:
    from aicf.rpc.methods import make_methods
    methods = make_methods(service=MyService())
    dispatcher.register_many(methods)
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple, TypedDict, Union

# ---- Optional imports from aicf.* (graceful fallbacks if not present) ----

try:
    from aicf.errors import AICFError  # type: ignore
except Exception:  # pragma: no cover - fallback if package not wired yet
    class AICFError(Exception):
        """Generic AICF error (fallback)."""


# ---- Public DTOs (transport-facing) ----------------------------------------

class Capability(str, Enum):
    AI = "AI"
    QUANTUM = "QUANTUM"


class ProviderStatus(str, Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    JAILED = "jailed"


class JobKind(str, Enum):
    AI = "AI"
    QUANTUM = "QUANTUM"


class JobStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


@dataclass(frozen=True)
class ProviderView:
    id: str
    name: Optional[str]
    capabilities: List[Capability]
    stake: int  # smallest unit
    status: ProviderStatus
    endpoint: Optional[str] = None
    region: Optional[str] = None


@dataclass(frozen=True)
class JobView:
    id: str
    kind: JobKind
    status: JobStatus
    requester: Optional[str]
    provider_id: Optional[str]
    fee: int  # smallest unit
    units: int  # abstract compute units (ai_units/quantum_units)
    created_at: int  # unix seconds
    updated_at: int  # unix seconds


@dataclass(frozen=True)
class BalanceView:
    provider_id: str
    available: int
    pending: int
    escrow: int
    last_settlement_epoch: Optional[int] = None


@dataclass(frozen=True)
class PayoutClaimResult:
    provider_id: str
    total_paid: int
    epoch_from: Optional[int]
    epoch_to: Optional[int]
    payouts: List[Tuple[str, int]]  # (job_id, amount)
    tx_hash: Optional[str] = None


# ---- Service protocol (integration surface) --------------------------------

class RegistryService(Protocol):
    def list_providers(self, *, offset: int, limit: int) -> Iterable[ProviderView]: ...
    def get_provider(self, provider_id: str) -> ProviderView: ...


class QueueService(Protocol):
    def list_jobs(
        self,
        *,
        kind: Optional[JobKind],
        status: Optional[JobStatus],
        provider_id: Optional[str],
        requester: Optional[str],
        offset: int,
        limit: int,
    ) -> Iterable[JobView]: ...
    def get_job(self, job_id: str) -> JobView: ...


class TreasuryService(Protocol):
    def get_balance(self, provider_id: str) -> BalanceView: ...
    def claim_payout(
        self, provider_id: str, *, upto_epoch: Optional[int]
    ) -> PayoutClaimResult: ...


@dataclass
class ServiceBundle:
    """Minimal bundle the methods need. Your mount/wiring should provide this."""
    registry: RegistryService
    queue: QueueService
    treasury: TreasuryService


# ---- Helpers ---------------------------------------------------------------

def _coerce_int(value: Any, name: str) -> int:
    try:
        iv = int(value)
        if iv < 0:
            raise ValueError
        return iv
    except Exception as e:  # noqa: BLE001
        raise AICFError(f"invalid {name}: must be a non-negative integer") from e


def _coerce_enum(value: Optional[str], enum_cls: Union[type[JobKind], type[JobStatus]]) -> Optional[Enum]:
    if value is None:
        return None
    try:
        return enum_cls(value)  # type: ignore[return-value]
    except ValueError as e:  # noqa: BLE001
        allowed = ", ".join([m.value for m in enum_cls])  # type: ignore[attr-defined]
        raise AICFError(f"invalid value '{value}', allowed: {allowed}") from e


# ---- JSON-RPC method factory ----------------------------------------------

def make_methods(service: ServiceBundle) -> Dict[str, Callable[..., Any]]:
    """
    Build a mapping of JSON-RPC method name -> callable.
    Each callable returns plain JSON-serializable structures.
    """

    def aicf_list_providers(
        *,
        offset: Optional[int] = 0,
        limit: Optional[int] = 100,
    ) -> Dict[str, Any]:
        off = _coerce_int(offset, "offset")
        lim = _coerce_int(limit, "limit")
        items = [pv.__dict__ for pv in service.registry.list_providers(offset=off, limit=lim)]
        next_offset = off + len(items)
        return {"items": items, "nextOffset": next_offset}

    def aicf_get_provider(*, providerId: str) -> Dict[str, Any]:
        if not providerId:
            raise AICFError("providerId is required")
        pv = service.registry.get_provider(providerId)
        return pv.__dict__

    def aicf_list_jobs(
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        providerId: Optional[str] = None,
        requester: Optional[str] = None,
        offset: Optional[int] = 0,
        limit: Optional[int] = 100,
    ) -> Dict[str, Any]:
        off = _coerce_int(offset, "offset")
        lim = _coerce_int(limit, "limit")
        k = _coerce_enum(kind, JobKind)  # type: ignore[arg-type]
        st = _coerce_enum(status, JobStatus)  # type: ignore[arg-type]
        items = [
            jv.__dict__
            for jv in service.queue.list_jobs(
                kind=k, status=st, provider_id=providerId, requester=requester, offset=off, limit=lim
            )
        ]
        next_offset = off + len(items)
        return {"items": items, "nextOffset": next_offset}

    def aicf_get_job(*, jobId: str) -> Dict[str, Any]:
        if not jobId:
            raise AICFError("jobId is required")
        jv = service.queue.get_job(jobId)
        return jv.__dict__

    def aicf_claim_payout(*, providerId: str, uptoEpoch: Optional[int] = None) -> Dict[str, Any]:
        if not providerId:
            raise AICFError("providerId is required")
        upto = None if uptoEpoch is None else _coerce_int(uptoEpoch, "uptoEpoch")
        res = service.treasury.claim_payout(providerId, upto_epoch=upto)
        out = {
            "providerId": res.provider_id,
            "totalPaid": res.total_paid,
            "epochFrom": res.epoch_from,
            "epochTo": res.epoch_to,
            "payouts": [{"jobId": jid, "amount": amt} for (jid, amt) in res.payouts],
            "txHash": res.tx_hash,
        }
        return out

    def aicf_get_balance(*, providerId: str) -> Dict[str, Any]:
        if not providerId:
            raise AICFError("providerId is required")
        b = service.treasury.get_balance(providerId)
        return {
            "providerId": b.provider_id,
            "available": b.available,
            "pending": b.pending,
            "escrow": b.escrow,
            "lastSettlementEpoch": b.last_settlement_epoch,
        }

    # Map JSON-RPC names → callables
    return {
        "aicf.listProviders": aicf_list_providers,
        "aicf.getProvider": aicf_get_provider,
        "aicf.listJobs": aicf_list_jobs,
        "aicf.getJob": aicf_get_job,
        "aicf.claimPayout": aicf_claim_payout,
        "aicf.getBalance": aicf_get_balance,
    }


# ---- Optional REST adapter (FastAPI) ---------------------------------------
# This is a convenience for projects that also want simple REST endpoints.
# It is safe to import even if FastAPI is not installed (no hard dependency).

def build_rest_router(service: ServiceBundle):
    """
    Return a FastAPI APIRouter exposing read/write-safe endpoints.
    Mount path suggestion: f"{RPC_PREFIX}" (import from aicf.rpc).
    """
    try:
        from fastapi import APIRouter, HTTPException, Query
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("FastAPI is required to build the REST router") from exc

    router = APIRouter()

    @router.get("/providers")
    def http_list_providers(
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        try:
            return make_methods(service)["aicf.listProviders"](offset=offset, limit=limit)
        except AICFError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.get("/providers/{provider_id}")
    def http_get_provider(provider_id: str):
        try:
            return make_methods(service)["aicf.getProvider"](providerId=provider_id)
        except AICFError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.get("/jobs")
    def http_list_jobs(
        kind: Optional[str] = None,
        status: Optional[str] = None,
        providerId: Optional[str] = None,
        requester: Optional[str] = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        try:
            return make_methods(service)["aicf.listJobs"](
                kind=kind, status=status, providerId=providerId, requester=requester, offset=offset, limit=limit
            )
        except AICFError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.get("/jobs/{job_id}")
    def http_get_job(job_id: str):
        try:
            return make_methods(service)["aicf.getJob"](jobId=job_id)
        except AICFError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.post("/providers/{provider_id}/claim")
    def http_claim_payout(provider_id: str, uptoEpoch: Optional[int] = None):
        try:
            return make_methods(service)["aicf.claimPayout"](providerId=provider_id, uptoEpoch=uptoEpoch)
        except AICFError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.get("/providers/{provider_id}/balance")
    def http_get_balance(provider_id: str):
        try:
            return make_methods(service)["aicf.getBalance"](providerId=provider_id)
        except AICFError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    return router


__all__ = [
    "Capability",
    "ProviderStatus",
    "JobKind",
    "JobStatus",
    "ProviderView",
    "JobView",
    "BalanceView",
    "PayoutClaimResult",
    "RegistryService",
    "QueueService",
    "TreasuryService",
    "ServiceBundle",
    "make_methods",
    "build_rest_router",
]
