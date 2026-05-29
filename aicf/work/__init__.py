"""
aicf.work
---------

Animica useful-work AI layer — Python source of truth for jobs, workers,
claims, results, verification, and payouts.

The TypeScript Studio app at ``apps/chat-animica/`` is a UI proxy over
this module; both surfaces must stay in sync on enum values, statuses,
and invariants. See ``DESIGN.md`` for the architectural plan.

Public surface
~~~~~~~~~~~~~~

::

    from aicf.work import (
        connect, init_schema,                              # db
        create_job, register_worker, claim_next,           # services
        submit_result, verify_result, approve_payout,
        get_job, list_open_jobs,
        get_adapters, set_adapters_for_test, AdapterBundle, # adapters
        WorkError,
        CreateJobRequest, RegisterWorkerRequest,           # schemas
        ClaimNextRequest, SubmitResultRequest,
        VerifyResultRequest, ApprovePayoutRequest,
    )

    from aicf.work.rpc import WorkService, make_work_methods
"""

from __future__ import annotations

from .adapters import AdapterBundle, get_adapters, set_adapters_for_test
from .db import connect, init_schema, default_db_path
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
from .services import (
    approve_payout,
    claim_next,
    create_job,
    get_job,
    heartbeat_claim,
    heartbeat_worker,
    list_open_jobs,
    register_worker,
    release_claim,
    submit_result,
    verify_result,
)

__all__ = [
    # db
    "connect", "init_schema", "default_db_path",
    # adapters
    "AdapterBundle", "get_adapters", "set_adapters_for_test",
    # errors
    "WorkError",
    # schemas
    "CreateJobRequest", "RegisterWorkerRequest", "HeartbeatWorkerRequest",
    "ClaimNextRequest", "SubmitResultRequest", "VerifyResultRequest",
    "ApprovePayoutRequest",
    # services
    "create_job", "get_job", "list_open_jobs",
    "register_worker", "heartbeat_worker",
    "claim_next", "heartbeat_claim", "release_claim",
    "submit_result", "verify_result", "approve_payout",
]
