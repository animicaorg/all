"""
Service-layer integration tests for aicf.work — exercise real SQL via
in-memory SQLite so the four production-critical invariants land at the
DB layer, not at the Python type layer.

  1. Idempotent job creation (UNIQUE work_job.idempotency_key)
  2. Lease expiry returns claims to the pool
  3. Capability mismatch → no claim handed out
  4. No double payout (UNIQUE work_payout.result_id)

Plus result-submission idempotency and the unverified-payout refusal.
"""

from __future__ import annotations

import time

import pytest

from aicf.work import (
    AdapterBundle,
    ApprovePayoutRequest,
    ClaimNextRequest,
    CreateJobRequest,
    RegisterWorkerRequest,
    SubmitResultRequest,
    VerifyResultRequest,
    WorkError,
    approve_payout,
    claim_next,
    connect,
    create_job,
    register_worker,
    submit_result,
    verify_result,
)
from aicf.work.adapters.mock import (
    mock_payout,
    mock_planner,
    mock_rpc,
    mock_verifier,
)

VALID_WALLET = "anim1zqpxtrnrc0256kruxxwpx0kpknrsmxwhvmv6nv2wlju6e5re2g7kedgmjjxj9"
VALID_WORKER_WALLET = "anim1zqpye0muk7etljd2fh7wxsh9y9027cq7dykj3de8u80s2mcnfp6qxecpunkth"


@pytest.fixture
def adapters() -> AdapterBundle:
    return AdapterBundle(
        planner=mock_planner,
        verifier=mock_verifier,
        payout=mock_payout,
        rpc=mock_rpc,
    )


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def _make_create_req(**overrides) -> CreateJobRequest:
    base = dict(
        title="x",
        prompt="y",
        job_type="code_generation",
        reward_amount_anm="1",
        creator_wallet=VALID_WALLET,
        required_capabilities=[],
        priority=0,
        max_workers=1,
        verification_mode="user_acceptance",
        result_visibility="private",
        plan=True,
    )
    base.update(overrides)
    return CreateJobRequest(**base)


# ---------------------------------------------------------------------------
# Job creation idempotency
# ---------------------------------------------------------------------------


def test_create_job_idempotent_on_key(conn, adapters):
    req = _make_create_req(idempotency_key="dedup-key-1")
    a = create_job(conn, adapters, req)
    b = create_job(conn, adapters, req)
    assert a["idempotent"] is False
    assert b["idempotent"] is True
    assert a["job"]["id"] == b["job"]["id"]
    n = conn.execute("SELECT COUNT(*) FROM work_job").fetchone()[0]
    assert n == 1


def test_create_job_different_keys_make_two_jobs(conn, adapters):
    a = create_job(conn, adapters, _make_create_req(title="one", idempotency_key="key-aaa-001"))
    b = create_job(conn, adapters, _make_create_req(title="two", idempotency_key="key-aaa-002"))
    assert a["job"]["id"] != b["job"]["id"]
    n = conn.execute("SELECT COUNT(*) FROM work_job").fetchone()[0]
    assert n == 2


def test_create_job_unique_constraint_enforced_at_sql(conn, adapters):
    """Sanity: even without going through the service the SQL refuses dups."""
    create_job(conn, adapters, _make_create_req(idempotency_key="raw-dup-1"))
    # A direct duplicate insert must raise — service relies on this.
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO work_job (id, idempotency_key, title, prompt, job_type, status, "
            "reward_amount_anm, required_capabilities, priority, max_workers, "
            "verification_mode, result_visibility, created_at, updated_at) "
            "VALUES ('manual','raw-dup-1','x','y','code_generation','open','1','[]',0,1,"
            "'user_acceptance','private',?,?)",
            (int(time.time() * 1000), int(time.time() * 1000)),
        )


# ---------------------------------------------------------------------------
# Lease expiry returns claims to the pool
# ---------------------------------------------------------------------------


def test_expired_claim_returns_task_to_pool(conn, adapters):
    create_job(
        conn,
        adapters,
        _make_create_req(
            job_type="research",
            required_capabilities=["llm_inference"],
            plan=True,
        ),
    )
    w1 = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="machine-001",
            capabilities=["llm_inference"],
            device_type="cpu",
        ),
    )
    claim1 = claim_next(conn, ClaimNextRequest(worker_id=w1["id"], lease_seconds=60))
    assert claim1["task"] is not None
    task_id = claim1["task"]["id"]

    # Simulate lease expiry by manually pushing it into the past.
    conn.execute(
        "UPDATE work_claim SET lease_expires_at = ? WHERE id = ?",
        (int(time.time() * 1000) - 1000, claim1["claim"]["id"]),
    )

    # A different worker / machine claims again — claim_next's first action
    # is to expire stale claims and re-open their tasks.
    w2 = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="machine-002",
            capabilities=["llm_inference"],
            device_type="cpu",
        ),
    )
    claim2 = claim_next(conn, ClaimNextRequest(worker_id=w2["id"], lease_seconds=60))
    assert claim2["task"] is not None
    assert claim2["task"]["id"] == task_id  # same task came back to the pool

    expired = conn.execute(
        "SELECT status FROM work_claim WHERE id = ?", (claim1["claim"]["id"],)
    ).fetchone()
    assert expired["status"] == "expired"


def test_capability_mismatch_returns_no_claim(conn, adapters):
    create_job(
        conn,
        adapters,
        _make_create_req(
            job_type="smart_contract",
            required_capabilities=["smart_contracts"],
            plan=False,
        ),
    )
    worker = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="no-contracts-machine",
            capabilities=["llm_inference"],
            device_type="cpu",
        ),
    )
    out = claim_next(conn, ClaimNextRequest(worker_id=worker["id"], lease_seconds=60))
    assert out["claim"] is None


def test_offered_capabilities_must_be_subset(conn, adapters):
    worker = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="modest-machine-id",
            capabilities=["llm_inference"],
            device_type="cpu",
        ),
    )
    with pytest.raises(WorkError) as ei:
        claim_next(
            conn,
            ClaimNextRequest(
                worker_id=worker["id"],
                offered_capabilities=["llm_inference", "gpu"],
                lease_seconds=60,
            ),
        )
    assert ei.value.code == "CAPABILITY_OVERREACH"


# ---------------------------------------------------------------------------
# Payout double-approve prevention
# ---------------------------------------------------------------------------


def _drive_to_accepted(conn, adapters):
    """Helper: create → register → claim → submit → verify(accepted)."""
    job_out = create_job(
        conn,
        adapters,
        _make_create_req(
            job_type="research",
            required_capabilities=["llm_inference"],
            plan=False,
        ),
    )
    worker = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="machine-001",
            capabilities=["llm_inference"],
            device_type="cpu",
        ),
    )
    c = claim_next(conn, ClaimNextRequest(worker_id=worker["id"], lease_seconds=600))
    assert c["claim"] is not None
    result = submit_result(
        conn,
        SubmitResultRequest(
            job_id=job_out["job"]["id"],
            task_id=c["task"]["id"],
            worker_id=worker["id"],
            claim_id=c["claim"]["id"],
            output_text="x" * 300,
            artifact_urls=[],
            result_hash="a" * 64,
        ),
    )
    verify_result(
        conn,
        adapters,
        result["result"]["id"],
        VerifyResultRequest(verdict="accepted"),
    )
    return result["result"]["id"]


def test_payout_never_double_pays(conn, adapters):
    rid = _drive_to_accepted(conn, adapters)
    p1 = approve_payout(conn, adapters, rid, ApprovePayoutRequest())
    p2 = approve_payout(conn, adapters, rid, ApprovePayoutRequest())
    assert p1["payout"]["id"] == p2["payout"]["id"]
    assert p2["idempotent"] is True
    n = conn.execute("SELECT COUNT(*) FROM work_payout").fetchone()[0]
    assert n == 1


def test_payout_refuses_unverified_result(conn, adapters):
    job_out = create_job(
        conn,
        adapters,
        _make_create_req(
            job_type="research",
            required_capabilities=[],
            plan=False,
        ),
    )
    worker = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="machine-001",
            capabilities=[],
            device_type="cpu",
        ),
    )
    c = claim_next(conn, ClaimNextRequest(worker_id=worker["id"], lease_seconds=600))
    r = submit_result(
        conn,
        SubmitResultRequest(
            job_id=job_out["job"]["id"],
            task_id=c["task"]["id"],
            worker_id=worker["id"],
            claim_id=c["claim"]["id"],
            output_text="x",
            artifact_urls=[],
            result_hash="a" * 64,
        ),
    )
    with pytest.raises(WorkError) as ei:
        approve_payout(conn, adapters, r["result"]["id"], ApprovePayoutRequest())
    assert ei.value.code == "RESULT_NOT_ACCEPTED"


# ---------------------------------------------------------------------------
# Result submission idempotency
# ---------------------------------------------------------------------------


def test_result_submit_idempotent_returns_existing_row(conn, adapters):
    """Second submit with same hash must succeed even with the same claim only
    once — after that the claim is 'completed' so a second submit hits the
    claim-inactive guard. Either way: never a duplicate row."""
    job_out = create_job(
        conn,
        adapters,
        _make_create_req(plan=False),
    )
    worker = register_worker(
        conn,
        RegisterWorkerRequest(
            wallet_address=VALID_WORKER_WALLET,
            machine_id="machine-001",
            capabilities=[],
            device_type="cpu",
        ),
    )
    c = claim_next(conn, ClaimNextRequest(worker_id=worker["id"], lease_seconds=600))
    body = SubmitResultRequest(
        job_id=job_out["job"]["id"],
        task_id=c["task"]["id"],
        worker_id=worker["id"],
        claim_id=c["claim"]["id"],
        output_text="answer",
        artifact_urls=[],
        result_hash="a" * 64,
    )
    a = submit_result(conn, body)
    assert a["idempotent"] is False
    # Second submission rejected because claim is now completed.
    with pytest.raises(WorkError) as ei:
        submit_result(conn, body)
    assert ei.value.code == "CLAIM_INACTIVE"
    n = conn.execute("SELECT COUNT(*) FROM work_result").fetchone()[0]
    assert n == 1


# ---------------------------------------------------------------------------
# Full happy path produces the expected state transitions
# ---------------------------------------------------------------------------


def test_full_happy_path_drives_job_to_paid(conn, adapters):
    rid = _drive_to_accepted(conn, adapters)
    out = approve_payout(conn, adapters, rid, ApprovePayoutRequest())
    assert out["payout"]["status"] == "paid"
    assert out["payout"]["tx_hash"] and out["payout"]["tx_hash"].startswith("0x")
    # Job should be marked paid since the single task is paid.
    job = conn.execute(
        "SELECT status FROM work_job WHERE id = ?", (out["payout"]["job_id"],)
    ).fetchone()
    assert job["status"] == "paid"
    worker = conn.execute(
        "SELECT completed_jobs, total_earned_anm FROM work_worker WHERE id = ?",
        (out["payout"]["worker_id"],),
    ).fetchone()
    assert worker["completed_jobs"] == 1
    assert worker["total_earned_anm"] == "1"
