"""
aicf.work.rpc
-------------

JSON-RPC bindings for the work layer. Mirrors the ``aicf.rpc.methods``
``make_methods()`` convention: returns a dict of callables you can
register with any dispatcher.

Method names use the ``aicf.work.*`` namespace so they're discoverable
alongside the existing ``aicf.*`` methods without collision.

  aicf.work.createJob            POST /api/work/jobs
  aicf.work.getJob               GET  /api/work/jobs/:id
  aicf.work.listJobs             GET  /api/work/jobs
  aicf.work.registerWorker       POST /api/work/workers/register
  aicf.work.heartbeatWorker      POST /api/work/workers/heartbeat
  aicf.work.claimNext            POST /api/work/claims/next
  aicf.work.heartbeatClaim       POST /api/work/claims/:id/heartbeat
  aicf.work.releaseClaim         POST /api/work/claims/:id/release
  aicf.work.submitResult         POST /api/work/results
  aicf.work.verifyResult         POST /api/work/verify/:resultId
  aicf.work.approvePayout        POST /api/work/payouts/:resultId/approve

All inputs are validated with the Pydantic schemas in ``aicf.work.schemas``;
WorkError → ``{"code", "message", "details"}`` (dispatcher maps to JSON-RPC
error code).

Snake_case fields on the wire; the chat-animica TS proxy maps the
camelCase form on the way in.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import ValidationError

from .adapters import AdapterBundle, get_adapters
from .errors import WorkError
from .schemas import (
    ApprovePayoutRequest,
    ClaimNextRequest,
    CreateJobRequest,
    HeartbeatWorkerRequest,
    RegisterWorkerRequest,
    SubmitResultRequest,
    VerifyResultRequest,
)
from . import services as svc


class WorkService:
    """Bundle of (sqlite3.Connection factory + AdapterBundle).

    A factory is used so each method call gets its own connection — SQLite
    connections aren't safe across threads even with check_same_thread=False
    if a dispatcher fans out across worker threads. Pass the existing
    persistent connection back in your factory if you'd rather share.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        adapters: AdapterBundle | None = None,
    ) -> None:
        self._factory = connection_factory
        self._adapters = adapters

    @property
    def adapters(self) -> AdapterBundle:
        return self._adapters or get_adapters()

    def conn(self):
        return self._factory()


def _validate(model: type, params: Any):
    """Raise WorkError(400) on Pydantic failure with the full validation tree."""
    try:
        return model(**(params or {}))
    except ValidationError as ve:
        raise WorkError("VALIDATION", "Invalid request", 400, ve.errors()) from ve


def make_work_methods(service: WorkService) -> dict[str, Callable[..., Any]]:
    """Build the ``aicf.work.*`` method table for a JSON-RPC dispatcher.

    Use exactly like ``aicf.rpc.methods.make_methods``::

        from aicf.work.rpc import make_work_methods, WorkService
        from aicf.work.db import connect

        wsvc = WorkService(lambda: connect())
        dispatcher.register_many(make_work_methods(wsvc))
    """

    def create_job(**params: Any) -> dict[str, Any]:
        req = _validate(CreateJobRequest, params)
        with service.conn() as c:
            return svc.create_job(c, service.adapters, req)

    def get_job(*, job_id: str) -> dict[str, Any]:
        if not job_id:
            raise WorkError("BAD_REQUEST", "job_id is required", 400)
        with service.conn() as c:
            job = svc.get_job(c, job_id)
            if job is None:
                raise WorkError("JOB_NOT_FOUND", "job not found", 404)
            return {"job": job}

    def list_jobs(
        *,
        limit: int = 100,
        job_type: str | None = None,
        status_in: list[str] | None = None,
    ) -> dict[str, Any]:
        with service.conn() as c:
            return {
                "jobs": svc.list_open_jobs(
                    c, limit=limit, job_type=job_type, status_in=status_in,
                ),
            }

    def register_worker(**params: Any) -> dict[str, Any]:
        req = _validate(RegisterWorkerRequest, params)
        with service.conn() as c:
            return {"worker": svc.register_worker(c, req)}

    def heartbeat_worker(**params: Any) -> dict[str, Any]:
        req = _validate(HeartbeatWorkerRequest, params)
        with service.conn() as c:
            return {"worker": svc.heartbeat_worker(c, req.worker_id)}

    def get_worker(*, worker_id: str) -> dict[str, Any]:
        with service.conn() as c:
            w = svc.get_worker(c, worker_id)
            if w is None:
                raise WorkError("WORKER_NOT_FOUND", "worker not found", 404)
            return {"worker": w}

    def list_workers(*, limit: int = 100, status: str | None = None) -> dict[str, Any]:
        with service.conn() as c:
            return {"workers": svc.list_workers(c, limit=limit, status=status)}

    def list_pending_verifications(*, limit: int = 50) -> dict[str, Any]:
        with service.conn() as c:
            return {"results": svc.list_results_pending_review(c, limit=limit)}

    def claim_next(**params: Any) -> dict[str, Any]:
        req = _validate(ClaimNextRequest, params)
        with service.conn() as c:
            return svc.claim_next(c, req)

    def heartbeat_claim(*, claim_id: str, worker_id: str) -> dict[str, Any]:
        with service.conn() as c:
            svc.heartbeat_claim(c, claim_id, worker_id)
            return {"ok": True}

    def release_claim(*, claim_id: str, worker_id: str) -> dict[str, Any]:
        with service.conn() as c:
            svc.release_claim(c, claim_id, worker_id)
            return {"ok": True}

    def submit_result(**params: Any) -> dict[str, Any]:
        req = _validate(SubmitResultRequest, params)
        with service.conn() as c:
            return svc.submit_result(c, req)

    def verify_result(*, result_id: str, **params: Any) -> dict[str, Any]:
        req = _validate(VerifyResultRequest, params)
        with service.conn() as c:
            return {"verification": svc.verify_result(c, service.adapters, result_id, req)}

    def approve_payout(*, result_id: str, actor_id: str | None = None, **params: Any) -> dict[str, Any]:
        req = _validate(ApprovePayoutRequest, params)
        with service.conn() as c:
            return svc.approve_payout(
                c, service.adapters, result_id, req,
                actor_type="admin", actor_id=actor_id,
            )

    return {
        "aicf.work.createJob": create_job,
        "aicf.work.getJob": get_job,
        "aicf.work.listJobs": list_jobs,
        "aicf.work.registerWorker": register_worker,
        "aicf.work.heartbeatWorker": heartbeat_worker,
        "aicf.work.getWorker": get_worker,
        "aicf.work.listWorkers": list_workers,
        "aicf.work.claimNext": claim_next,
        "aicf.work.heartbeatClaim": heartbeat_claim,
        "aicf.work.releaseClaim": release_claim,
        "aicf.work.submitResult": submit_result,
        "aicf.work.verifyResult": verify_result,
        "aicf.work.listPendingVerifications": list_pending_verifications,
        "aicf.work.approvePayout": approve_payout,
    }


__all__ = ["WorkService", "make_work_methods"]
