"""
aicf.work.services
------------------

Business logic for the Animica useful-work AI layer. Each function takes
an explicit ``sqlite3.Connection`` (so callers control transactions and
tests can use ``":memory:"``) plus an explicit ``AdapterBundle``.

Invariants this module enforces, all checked at the SQL layer:

  1. Idempotent job creation (UNIQUE work_job.idempotency_key)
  2. Lease expiry returns claims to the pool (UPDATE ... WHERE lease_expires_at < now)
  3. Capability gating (in-memory subset check before claim_next)
  4. No double payout (UNIQUE work_payout.result_id)
  5. Cannot pay an unverified result (latest verdict must be 'accepted')
  6. Result submission idempotent on hash (UNIQUE work_result(job_id, worker_id, result_hash))

Ports the TS prototype (apps/chat-animica/src/server/work/services.ts)
line-for-line. Status name set is the same; both sides must change
together when extending.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import asdict
from typing import Any, Iterable, Sequence

from .adapters import AdapterBundle, PlanInput, VerifyInput, PayoutInput
from .decimals import add_anm, split_reward
from .errors import WorkError
from .schemas import (
    ApprovePayoutRequest,
    ClaimNextRequest,
    CreateJobRequest,
    RegisterWorkerRequest,
    SubmitResultRequest,
    VerifyResultRequest,
)

_DEFAULT_EXPIRES_SEC = 24 * 3600


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    """26-char cuid-like identifier."""
    return prefix + "_" + secrets.token_hex(12)


def _json_dump(v: Any) -> str | None:
    if v is None:
        return None
    return json.dumps(v, separators=(",", ":"), default=str)


def _json_load(s: str | None) -> Any:
    if s is None:
        return None
    return json.loads(s)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _to_public_job(row: sqlite3.Row) -> dict[str, Any]:
    """SQLite row → API-shaped dict. Decodes JSON columns, mirrors TS field names."""
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "idempotency_key": d.get("idempotency_key"),
        "title": d["title"],
        "description": d.get("description"),
        "prompt": d["prompt"],
        "job_type": d["job_type"],
        "status": d["status"],
        "creator_user_id": d.get("creator_user_id"),
        "creator_wallet": d.get("creator_wallet"),
        "reward_amount_anm": d["reward_amount_anm"],
        "required_capabilities": _json_load(d["required_capabilities"]) or [],
        "priority": d["priority"],
        "max_workers": d["max_workers"],
        "verification_mode": d["verification_mode"],
        "result_visibility": d["result_visibility"],
        "metadata_json": _json_load(d.get("metadata_json")),
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
        "expires_at": d.get("expires_at"),
    }


def _to_public_task(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "job_id": d["job_id"],
        "title": d["title"],
        "description": d.get("description"),
        "task_type": d["task_type"],
        "status": d["status"],
        "depends_on_task_ids": _json_load(d["depends_on_task_ids"]) or [],
        "assigned_worker_id": d.get("assigned_worker_id"),
        "required_capabilities": _json_load(d["required_capabilities"]) or [],
        "reward_amount_anm": d["reward_amount_anm"],
        "result_id": d.get("result_id"),
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
    }


def _to_public_worker(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "wallet_address": d["wallet_address"],
        "display_name": d.get("display_name"),
        "machine_id": d["machine_id"],
        "device_type": d["device_type"],
        "hardware_summary": _json_load(d.get("hardware_summary")),
        "capabilities": _json_load(d["capabilities"]) or [],
        "status": d["status"],
        "reputation_score": d["reputation_score"],
        "completed_jobs": d["completed_jobs"],
        "failed_jobs": d["failed_jobs"],
        "total_earned_anm": d["total_earned_anm"],
        "last_seen_at": d["last_seen_at"],
        "created_at": d["created_at"],
    }


def _to_public_claim(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "job_id": d["job_id"],
        "task_id": d.get("task_id"),
        "worker_id": d["worker_id"],
        "status": d["status"],
        "claimed_at": d["claimed_at"],
        "heartbeat_at": d["heartbeat_at"],
        "lease_expires_at": d["lease_expires_at"],
        "released_at": d.get("released_at"),
    }


def _to_public_result(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "job_id": d["job_id"],
        "task_id": d.get("task_id"),
        "worker_id": d["worker_id"],
        "output_text": d.get("output_text"),
        "output_json": _json_load(d.get("output_json")),
        "artifact_urls": _json_load(d["artifact_urls"]) or [],
        "result_hash": d["result_hash"],
        "logs_hash": d.get("logs_hash"),
        "model_info": _json_load(d.get("model_info")),
        "runtime_info": _json_load(d.get("runtime_info")),
        "submitted_at": d["submitted_at"],
    }


def _to_public_verification(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "job_id": d["job_id"],
        "task_id": d.get("task_id"),
        "result_id": d["result_id"],
        "verifier_worker_id": d.get("verifier_worker_id"),
        "mode": d["mode"],
        "score": d.get("score"),
        "verdict": d["verdict"],
        "notes": d.get("notes"),
        "test_output": _json_load(d.get("test_output")),
        "created_at": d["created_at"],
    }


def _to_public_payout(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row) or {}
    return {
        "id": d["id"],
        "job_id": d["job_id"],
        "task_id": d.get("task_id"),
        "worker_id": d["worker_id"],
        "result_id": d["result_id"],
        "amount_anm": d["amount_anm"],
        "status": d["status"],
        "aicf_claim_id": d.get("aicf_claim_id"),
        "tx_hash": d.get("tx_hash"),
        "explorer_url": d.get("explorer_url"),
        "failure_reason": d.get("failure_reason"),
        "created_at": d["created_at"],
        "paid_at": d.get("paid_at"),
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit(
    conn: sqlite3.Connection,
    actor_type: str,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: Any | None = None,
) -> None:
    conn.execute(
        "INSERT INTO work_audit_log "
        "(id, actor_type, actor_id, action, target_type, target_id, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _new_id("aud"),
            actor_type,
            actor_id,
            action,
            target_type,
            target_id,
            _json_dump(metadata),
            _now_ms(),
        ),
    )


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------


def create_job(
    conn: sqlite3.Connection,
    adapters: AdapterBundle,
    req: CreateJobRequest,
) -> dict[str, Any]:
    """Create a job and (optionally) plan it into tasks.

    Returns ``{"job": <job dict>, "tasks": [...], "idempotent": bool}``.
    """
    expires_at = (
        _now_ms() + req.expires_in_seconds * 1000
        if req.expires_in_seconds
        else _now_ms() + _DEFAULT_EXPIRES_SEC * 1000
    )

    # Idempotency short-circuit before we do any work.
    if req.idempotency_key:
        existing = conn.execute(
            "SELECT * FROM work_job WHERE idempotency_key = ?",
            (req.idempotency_key,),
        ).fetchone()
        if existing:
            tasks = conn.execute(
                "SELECT * FROM work_task WHERE job_id = ? ORDER BY created_at",
                (existing["id"],),
            ).fetchall()
            return {
                "job": _to_public_job(existing),
                "tasks": [_to_public_task(t) for t in tasks],
                "idempotent": True,
            }

    job_id = _new_id("job")
    now = _now_ms()
    initial_status = "planning" if req.plan else "open"

    conn.execute("BEGIN IMMEDIATE")
    try:
        try:
            conn.execute(
                """
                INSERT INTO work_job (
                    id, idempotency_key, title, description, prompt, job_type, status,
                    creator_user_id, creator_wallet, reward_amount_anm, required_capabilities,
                    priority, max_workers, verification_mode, result_visibility,
                    metadata_json, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    req.idempotency_key,
                    req.title,
                    req.description,
                    req.prompt,
                    req.job_type,
                    initial_status,
                    req.creator_user_id,
                    req.creator_wallet,
                    req.reward_amount_anm,
                    _json_dump(req.required_capabilities),
                    req.priority,
                    req.max_workers,
                    req.verification_mode,
                    req.result_visibility,
                    None,
                    now,
                    now,
                    expires_at,
                ),
            )
        except sqlite3.IntegrityError as e:
            # Race-recovery: another writer landed the same idempotency key.
            conn.execute("ROLLBACK")
            if req.idempotency_key:
                winner = conn.execute(
                    "SELECT * FROM work_job WHERE idempotency_key = ?",
                    (req.idempotency_key,),
                ).fetchone()
                if winner:
                    tasks = conn.execute(
                        "SELECT * FROM work_task WHERE job_id = ? ORDER BY created_at",
                        (winner["id"],),
                    ).fetchall()
                    return {
                        "job": _to_public_job(winner),
                        "tasks": [_to_public_task(t) for t in tasks],
                        "idempotent": True,
                    }
            raise WorkError("DB_INTEGRITY", str(e), 500) from e

        # Plan
        planned = []
        if req.plan:
            out = adapters.planner.plan_job(
                PlanInput(
                    prompt=req.prompt,
                    job_type=req.job_type,
                    required_capabilities=list(req.required_capabilities),
                    total_reward_anm=req.reward_amount_anm,
                )
            )
            planned = list(out.tasks)
        if not planned:
            from .adapters import PlannedTask

            planned = [
                PlannedTask(
                    title=req.title,
                    task_type=req.job_type,
                    required_capabilities=list(req.required_capabilities),
                    reward_weight=1.0,
                    depends_on_indices=[],
                )
            ]

        rewards = split_reward(req.reward_amount_anm, [t.reward_weight for t in planned])
        created_ids: list[str] = []
        for i, t in enumerate(planned):
            tid = _new_id("task")
            created_ids.append(tid)
            conn.execute(
                """
                INSERT INTO work_task (
                    id, job_id, title, description, task_type, status,
                    depends_on_task_ids, required_capabilities, reward_amount_anm,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'open', '[]', ?, ?, ?, ?)
                """,
                (
                    tid,
                    job_id,
                    t.title,
                    t.description,
                    t.task_type,
                    _json_dump(t.required_capabilities),
                    rewards[i] if i < len(rewards) else "0",
                    now,
                    now,
                ),
            )
        # Resolve dependencies now that all IDs exist.
        for i, t in enumerate(planned):
            if t.depends_on_indices:
                deps = [created_ids[j] for j in t.depends_on_indices if 0 <= j < len(created_ids)]
                conn.execute(
                    "UPDATE work_task SET depends_on_task_ids = ?, updated_at = ? WHERE id = ?",
                    (_json_dump(deps), now, created_ids[i]),
                )
        if req.plan:
            conn.execute(
                "UPDATE work_job SET status = 'open', updated_at = ? WHERE id = ?",
                (_now_ms(), job_id),
            )

        _audit(
            conn,
            "user",
            req.creator_user_id,
            "job.create",
            "WorkJob",
            job_id,
            {
                "job_type": req.job_type,
                "task_count": len(created_ids),
                "reward_amount_anm": req.reward_amount_anm,
            },
        )
        conn.execute("COMMIT")
    except WorkError:
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    job_row = conn.execute("SELECT * FROM work_job WHERE id = ?", (job_id,)).fetchone()
    task_rows = conn.execute(
        "SELECT * FROM work_task WHERE job_id = ? ORDER BY created_at",
        (job_id,),
    ).fetchall()
    return {
        "job": _to_public_job(job_row),
        "tasks": [_to_public_task(t) for t in task_rows],
        "idempotent": False,
    }


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    """Return a job with all related rows. ``None`` if it doesn't exist."""
    job = conn.execute("SELECT * FROM work_job WHERE id = ?", (job_id,)).fetchone()
    if not job:
        return None
    tasks = conn.execute(
        "SELECT * FROM work_task WHERE job_id = ? ORDER BY created_at",
        (job_id,),
    ).fetchall()
    claims = conn.execute(
        "SELECT * FROM work_claim WHERE job_id = ? ORDER BY claimed_at",
        (job_id,),
    ).fetchall()
    results = conn.execute(
        "SELECT * FROM work_result WHERE job_id = ? ORDER BY submitted_at",
        (job_id,),
    ).fetchall()
    verifs = conn.execute(
        "SELECT * FROM work_verification WHERE job_id = ? ORDER BY created_at",
        (job_id,),
    ).fetchall()
    payouts = conn.execute(
        "SELECT * FROM work_payout WHERE job_id = ? ORDER BY created_at",
        (job_id,),
    ).fetchall()
    out = _to_public_job(job)
    out["tasks"] = [_to_public_task(t) for t in tasks]
    out["claims"] = [_to_public_claim(c) for c in claims]
    out["results"] = [_to_public_result(r) for r in results]
    out["verifications"] = [_to_public_verification(v) for v in verifs]
    out["payouts"] = [_to_public_payout(p) for p in payouts]
    return out


_DEFAULT_OPEN_STATUSES: tuple[str, ...] = ("open", "planning")


def list_open_jobs(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    job_type: str | None = None,
    status_in: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """List jobs filtered by status set.

    Default behavior (no ``status_in``) keeps backwards compatibility: only
    open + planning jobs are returned. Callers that want ``paid`` (or any
    other terminal status) pass an explicit ``status_in`` list. We keep the
    name "list_open_jobs" for API stability — the new filter generalises
    rather than supersedes.
    """
    limit = max(1, min(limit, 500))
    statuses = tuple(status_in) if status_in else _DEFAULT_OPEN_STATUSES
    placeholders = ",".join("?" * len(statuses))
    if job_type:
        rows = conn.execute(
            f"SELECT * FROM work_job WHERE status IN ({placeholders}) AND job_type = ? "
            f"ORDER BY priority DESC, created_at DESC LIMIT ?",
            (*statuses, job_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT * FROM work_job WHERE status IN ({placeholders}) "
            f"ORDER BY priority DESC, created_at DESC LIMIT ?",
            (*statuses, limit),
        ).fetchall()
    return [_to_public_job(r) for r in rows]


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------


def register_worker(
    conn: sqlite3.Connection,
    req: RegisterWorkerRequest,
) -> dict[str, Any]:
    """Upsert by (wallet_address, machine_id). Returns the worker row."""
    now = _now_ms()
    existing = conn.execute(
        "SELECT * FROM work_worker WHERE wallet_address = ? AND machine_id = ?",
        (req.wallet_address, req.machine_id),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE work_worker SET
              display_name = ?, device_type = ?, hardware_summary = ?,
              capabilities = ?, status = 'idle', last_seen_at = ?
            WHERE id = ?
            """,
            (
                req.display_name,
                req.device_type,
                _json_dump(req.hardware_summary),
                _json_dump(req.capabilities),
                now,
                existing["id"],
            ),
        )
        wid = existing["id"]
    else:
        wid = _new_id("wrk")
        conn.execute(
            """
            INSERT INTO work_worker (
                id, wallet_address, display_name, machine_id, device_type,
                hardware_summary, capabilities, status, reputation_score,
                completed_jobs, failed_jobs, total_earned_anm, last_seen_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'idle', 0, 0, 0, '0', ?, ?)
            """,
            (
                wid,
                req.wallet_address,
                req.display_name,
                req.machine_id,
                req.device_type,
                _json_dump(req.hardware_summary),
                _json_dump(req.capabilities),
                now,
                now,
            ),
        )
    _audit(conn, "worker", wid, "worker.register", "WorkerNode", wid)
    row = conn.execute("SELECT * FROM work_worker WHERE id = ?", (wid,)).fetchone()
    return _to_public_worker(row)


def heartbeat_worker(conn: sqlite3.Connection, worker_id: str) -> dict[str, Any]:
    now = _now_ms()
    cur = conn.execute(
        "UPDATE work_worker SET last_seen_at = ? WHERE id = ?", (now, worker_id)
    )
    if cur.rowcount == 0:
        raise WorkError("WORKER_NOT_FOUND", f"worker {worker_id} not registered", 404)
    row = conn.execute("SELECT * FROM work_worker WHERE id = ?", (worker_id,)).fetchone()
    return _to_public_worker(row)


def get_worker(conn: sqlite3.Connection, worker_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM work_worker WHERE id = ?", (worker_id,)).fetchone()
    return _to_public_worker(row) if row else None


def list_workers(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List workers ordered by total_earned_anm desc (Studio leaderboard).

    SQLite has no native numeric ordering for decimal-as-string, but our
    total_earned_anm column is always either '0' or '<integer>.<frac>'.
    We coerce to REAL for ordering — fine up to ~10^15 ANM, more than
    chain supply. For exact display the caller uses the raw string.
    """
    limit = max(1, min(500, limit))
    if status:
        rows = conn.execute(
            "SELECT * FROM work_worker WHERE status = ? "
            "ORDER BY CAST(total_earned_anm AS REAL) DESC, completed_jobs DESC, last_seen_at DESC "
            "LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM work_worker "
            "ORDER BY CAST(total_earned_anm AS REAL) DESC, completed_jobs DESC, last_seen_at DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return [_to_public_worker(r) for r in rows]


def list_results_pending_review(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Results that need a verifier verdict.

    "Pending review" = no verification row at all yet, or the most-recent
    one is ``needs_review``. We deliberately exclude already-accepted or
    rejected results — those are settled state, the verify queue is for
    actionable work.
    """
    limit = max(1, min(200, limit))
    rows = conn.execute(
        """
        SELECT r.*, j.title AS _job_title, j.job_type AS _job_type,
               j.prompt AS _job_prompt, j.reward_amount_anm AS _job_reward
        FROM work_result r
        JOIN work_job j ON j.id = r.job_id
        WHERE NOT EXISTS (
              SELECT 1 FROM work_verification v
              WHERE v.result_id = r.id AND v.verdict IN ('accepted','rejected')
              )
          AND (
              NOT EXISTS (
                  SELECT 1 FROM work_verification v WHERE v.result_id = r.id
              )
              OR EXISTS (
                  SELECT 1 FROM work_verification v
                  WHERE v.result_id = r.id
                  AND v.created_at = (
                      SELECT MAX(created_at) FROM work_verification v2
                      WHERE v2.result_id = r.id
                  )
                  AND v.verdict = 'needs_review'
              )
          )
        ORDER BY r.submitted_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        item = _to_public_result(r)
        item["job_title"] = r["_job_title"]
        item["job_type"] = r["_job_type"]
        item["job_prompt"] = r["_job_prompt"]
        item["job_reward_anm"] = r["_job_reward"]
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Claims — lease-based, capability-matched, single-pick.
# ---------------------------------------------------------------------------


def _matches_capabilities(have: Sequence[str], need: Sequence[str]) -> bool:
    if not need:
        return True
    have_set = set(have)
    return all(c in have_set for c in need)


def _deps_resolved(conn: sqlite3.Connection, depends_on: Sequence[str]) -> bool:
    if not depends_on:
        return True
    placeholders = ",".join("?" * len(depends_on))
    rows = conn.execute(
        f"SELECT status FROM work_task WHERE id IN ({placeholders})",
        tuple(depends_on),
    ).fetchall()
    return all(r["status"] in ("submitted", "accepted", "paid") for r in rows)


def claim_next(
    conn: sqlite3.Connection,
    req: ClaimNextRequest,
) -> dict[str, Any]:
    """Lease a task to a worker. Returns claim+task+job or null fields."""
    now = _now_ms()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1. Expire stale claims so their tasks return to the pool.
        expired_rows = conn.execute(
            "SELECT id, task_id FROM work_claim "
            "WHERE status = 'active' AND lease_expires_at < ?",
            (now,),
        ).fetchall()
        if expired_rows:
            conn.execute(
                "UPDATE work_claim SET status = 'expired', released_at = ? "
                "WHERE status = 'active' AND lease_expires_at < ?",
                (now, now),
            )
            # And re-open their tasks.
            task_ids = [r["task_id"] for r in expired_rows if r["task_id"]]
            if task_ids:
                placeholders = ",".join("?" * len(task_ids))
                conn.execute(
                    f"UPDATE work_task SET status = 'open', assigned_worker_id = NULL, "
                    f"updated_at = ? WHERE id IN ({placeholders}) AND status = 'claimed'",
                    (now, *task_ids),
                )

        worker = conn.execute(
            "SELECT * FROM work_worker WHERE id = ?", (req.worker_id,)
        ).fetchone()
        if not worker:
            raise WorkError("WORKER_NOT_FOUND", f"worker {req.worker_id} not found", 404)
        if worker["status"] == "banned":
            raise WorkError("WORKER_BANNED", "worker is banned", 403)

        worker_caps = _json_load(worker["capabilities"]) or []
        offered = list(req.offered_capabilities) if req.offered_capabilities else list(worker_caps)
        if not _matches_capabilities(worker_caps, offered):
            raise WorkError(
                "CAPABILITY_OVERREACH",
                "offered_capabilities is not a subset of registered worker.capabilities",
                409,
                {"offered": offered, "registered": worker_caps},
            )

        # 2. Find open tasks with no active claim.
        candidates = conn.execute(
            """
            SELECT t.*, j.priority AS job_priority
            FROM work_task t
            JOIN work_job j ON j.id = t.job_id
            WHERE t.status = 'open'
              AND NOT EXISTS (
                SELECT 1 FROM work_claim c
                WHERE c.task_id = t.id AND c.status = 'active'
              )
            ORDER BY j.priority DESC, t.created_at ASC
            LIMIT 50
            """
        ).fetchall()
        chosen = None
        for t in candidates:
            req_caps = _json_load(t["required_capabilities"]) or []
            if not _matches_capabilities(offered, req_caps):
                continue
            deps = _json_load(t["depends_on_task_ids"]) or []
            if not _deps_resolved(conn, deps):
                continue
            chosen = t
            break
        if chosen is None:
            conn.execute("COMMIT")
            return {"claim": None, "task": None, "job": None}

        lease_sec = max(60, min(1800, req.lease_seconds))
        claim_id = _new_id("clm")
        conn.execute(
            """
            INSERT INTO work_claim (
                id, job_id, task_id, worker_id, status,
                claimed_at, heartbeat_at, lease_expires_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                claim_id,
                chosen["job_id"],
                chosen["id"],
                req.worker_id,
                now,
                now,
                now + lease_sec * 1000,
            ),
        )
        conn.execute(
            "UPDATE work_task SET status = 'claimed', assigned_worker_id = ?, "
            "updated_at = ? WHERE id = ?",
            (req.worker_id, now, chosen["id"]),
        )
        conn.execute(
            "UPDATE work_job SET status = 'claimed', updated_at = ? "
            "WHERE id = ? AND status = 'open'",
            (now, chosen["job_id"]),
        )
        conn.execute(
            "UPDATE work_worker SET status = 'working', last_seen_at = ? WHERE id = ?",
            (now, req.worker_id),
        )
        _audit(
            conn,
            "worker",
            req.worker_id,
            "claim.create",
            "WorkClaim",
            claim_id,
            {"job_id": chosen["job_id"], "task_id": chosen["id"], "lease_sec": lease_sec},
        )
        conn.execute("COMMIT")
    except WorkError:
        conn.execute("ROLLBACK")
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    claim_row = conn.execute(
        "SELECT * FROM work_claim WHERE id = ?", (claim_id,)
    ).fetchone()
    task_row = conn.execute(
        "SELECT * FROM work_task WHERE id = ?", (chosen["id"],)
    ).fetchone()
    job_row = conn.execute(
        "SELECT * FROM work_job WHERE id = ?", (chosen["job_id"],)
    ).fetchone()
    return {
        "claim": _to_public_claim(claim_row),
        "task": _to_public_task(task_row),
        "job": _to_public_job(job_row),
    }


def heartbeat_claim(
    conn: sqlite3.Connection, claim_id: str, worker_id: str
) -> None:
    cur = conn.execute(
        "UPDATE work_claim SET heartbeat_at = ? "
        "WHERE id = ? AND worker_id = ? AND status = 'active'",
        (_now_ms(), claim_id, worker_id),
    )
    if cur.rowcount == 0:
        raise WorkError("CLAIM_NOT_ACTIVE", "claim is not active for this worker", 409)


def release_claim(conn: sqlite3.Connection, claim_id: str, worker_id: str) -> None:
    now = _now_ms()
    conn.execute("BEGIN IMMEDIATE")
    try:
        claim = conn.execute(
            "SELECT * FROM work_claim WHERE id = ?", (claim_id,)
        ).fetchone()
        if not claim or claim["worker_id"] != worker_id:
            raise WorkError("CLAIM_NOT_FOUND", "claim not found", 404)
        conn.execute(
            "UPDATE work_claim SET status = 'released', released_at = ? WHERE id = ?",
            (now, claim_id),
        )
        if claim["task_id"]:
            conn.execute(
                "UPDATE work_task SET status = 'open', assigned_worker_id = NULL, "
                "updated_at = ? WHERE id = ?",
                (now, claim["task_id"]),
            )
        conn.execute("COMMIT")
    except WorkError:
        conn.execute("ROLLBACK")
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def submit_result(
    conn: sqlite3.Connection,
    req: SubmitResultRequest,
) -> dict[str, Any]:
    claim = conn.execute(
        "SELECT * FROM work_claim WHERE id = ?", (req.claim_id,)
    ).fetchone()
    if not claim:
        raise WorkError("CLAIM_NOT_FOUND", "claim not found", 404)
    if claim["worker_id"] != req.worker_id:
        raise WorkError("CLAIM_WORKER_MISMATCH", "claim belongs to another worker", 403)
    if claim["status"] != "active":
        raise WorkError("CLAIM_INACTIVE", f"claim is {claim['status']}", 409)
    if claim["job_id"] != req.job_id:
        raise WorkError("CLAIM_JOB_MISMATCH", "claim's job does not match job_id", 409)
    if req.task_id and claim["task_id"] != req.task_id:
        raise WorkError("CLAIM_TASK_MISMATCH", "claim's task does not match task_id", 409)

    now = _now_ms()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT * FROM work_result "
            "WHERE job_id = ? AND worker_id = ? AND result_hash = ?",
            (req.job_id, req.worker_id, req.result_hash),
        ).fetchone()
        if existing:
            conn.execute("COMMIT")
            return {"result": _to_public_result(existing), "idempotent": True}

        result_id = _new_id("res")
        task_id = req.task_id or claim["task_id"]
        conn.execute(
            """
            INSERT INTO work_result (
                id, job_id, task_id, worker_id,
                output_text, output_json, artifact_urls,
                result_hash, logs_hash, model_info, runtime_info, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                req.job_id,
                task_id,
                req.worker_id,
                req.output_text,
                _json_dump(req.output_json),
                _json_dump(list(req.artifact_urls)),
                req.result_hash,
                req.logs_hash,
                _json_dump(req.model_info),
                _json_dump(req.runtime_info),
                now,
            ),
        )
        if task_id:
            conn.execute(
                "UPDATE work_task SET status = 'submitted', result_id = ?, updated_at = ? "
                "WHERE id = ?",
                (result_id, now, task_id),
            )
        conn.execute(
            "UPDATE work_claim SET status = 'completed' WHERE id = ?", (claim["id"],)
        )
        # Advance job state if every task has a submitted result.
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM work_task "
            "WHERE job_id = ? AND status NOT IN ('submitted','accepted','paid')",
            (req.job_id,),
        ).fetchone()
        if row["n"] == 0:
            conn.execute(
                "UPDATE work_job SET status = 'result_submitted', updated_at = ? "
                "WHERE id = ?",
                (now, req.job_id),
            )
        _audit(
            conn,
            "worker",
            req.worker_id,
            "result.submit",
            "WorkResult",
            result_id,
            {"result_hash": req.result_hash},
        )
        conn.execute("COMMIT")
    except WorkError:
        conn.execute("ROLLBACK")
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise

    res = conn.execute("SELECT * FROM work_result WHERE id = ?", (result_id,)).fetchone()
    return {"result": _to_public_result(res), "idempotent": False}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_result(
    conn: sqlite3.Connection,
    adapters: AdapterBundle,
    result_id: str,
    req: VerifyResultRequest,
    *,
    actor_type: str = "verifier",
    actor_id: str | None = None,
) -> dict[str, Any]:
    result = conn.execute(
        "SELECT * FROM work_result WHERE id = ?", (result_id,)
    ).fetchone()
    if not result:
        raise WorkError("RESULT_NOT_FOUND", "result not found", 404)
    job = conn.execute(
        "SELECT * FROM work_job WHERE id = ?", (result["job_id"],)
    ).fetchone()

    verdict = req.verdict
    score = req.score
    notes = req.notes
    test_output = req.test_output

    if verdict is None:
        out = adapters.verifier.verify(
            VerifyInput(
                job_type=job["job_type"],
                prompt=job["prompt"],
                output_text=result["output_text"],
                output_json=_json_load(result["output_json"]),
                artifact_urls=_json_load(result["artifact_urls"]) or [],
                result_hash=result["result_hash"],
            )
        )
        verdict = out.verdict
        score = score if score is not None else out.score
        notes = notes if notes is not None else out.notes

    now = _now_ms()
    conn.execute("BEGIN IMMEDIATE")
    try:
        vid = _new_id("vrf")
        conn.execute(
            """
            INSERT INTO work_verification (
                id, job_id, task_id, result_id, verifier_worker_id, mode,
                score, verdict, notes, test_output, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vid,
                result["job_id"],
                result["task_id"],
                result["id"],
                req.verifier_worker_id,
                req.mode or job["verification_mode"],
                score,
                verdict,
                notes,
                _json_dump(test_output),
                now,
            ),
        )
        if verdict == "accepted":
            if result["task_id"]:
                conn.execute(
                    "UPDATE work_task SET status = 'accepted', updated_at = ? WHERE id = ?",
                    (now, result["task_id"]),
                )
            still_open = conn.execute(
                "SELECT COUNT(*) AS n FROM work_task "
                "WHERE job_id = ? AND status NOT IN ('accepted','paid')",
                (result["job_id"],),
            ).fetchone()["n"]
            if still_open == 0:
                conn.execute(
                    "UPDATE work_job SET status = 'accepted', updated_at = ? WHERE id = ?",
                    (now, result["job_id"]),
                )
        elif verdict == "rejected":
            if result["task_id"]:
                conn.execute(
                    "UPDATE work_task SET status = 'rejected', updated_at = ? WHERE id = ?",
                    (now, result["task_id"]),
                )
            conn.execute(
                "UPDATE work_job SET status = 'rejected', updated_at = ? "
                "WHERE id = ? AND status = 'result_submitted'",
                (now, result["job_id"]),
            )
        _audit(
            conn,
            actor_type,
            actor_id,
            "result.verify",
            "WorkVerification",
            vid,
            {"verdict": verdict, "score": score},
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute(
        "SELECT * FROM work_verification WHERE id = ?", (vid,)
    ).fetchone()
    return _to_public_verification(row)


# ---------------------------------------------------------------------------
# Payouts
# ---------------------------------------------------------------------------


def approve_payout(
    conn: sqlite3.Connection,
    adapters: AdapterBundle,
    result_id: str,
    req: ApprovePayoutRequest,
    *,
    actor_type: str = "admin",
    actor_id: str | None = None,
) -> dict[str, Any]:
    result = conn.execute(
        "SELECT * FROM work_result WHERE id = ?", (result_id,)
    ).fetchone()
    if not result:
        raise WorkError("RESULT_NOT_FOUND", "result not found", 404)
    job = conn.execute(
        "SELECT * FROM work_job WHERE id = ?", (result["job_id"],)
    ).fetchone()
    task = (
        conn.execute("SELECT * FROM work_task WHERE id = ?", (result["task_id"],)).fetchone()
        if result["task_id"]
        else None
    )

    last_verdict = conn.execute(
        "SELECT verdict FROM work_verification WHERE result_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (result_id,),
    ).fetchone()
    if not last_verdict or last_verdict["verdict"] != "accepted":
        raise WorkError(
            "RESULT_NOT_ACCEPTED",
            "result has not been accepted by verification",
            409,
            {"latest_verdict": last_verdict["verdict"] if last_verdict else None},
        )

    # Idempotency: one payout per resultId.
    existing = conn.execute(
        "SELECT * FROM work_payout WHERE result_id = ?", (result_id,)
    ).fetchone()
    if existing:
        if existing["status"] == "paid":
            return {"payout": _to_public_payout(existing), "idempotent": True}
        # Mid-flight: drive the existing payout forward; do not create a new row.
        return _execute_payout(conn, adapters, existing, actor_type, actor_id)

    amount = req.amount_override_anm or (task["reward_amount_anm"] if task else job["reward_amount_anm"])
    pid = _new_id("pay")
    now = _now_ms()
    try:
        conn.execute(
            """
            INSERT INTO work_payout (
                id, job_id, task_id, worker_id, result_id,
                amount_anm, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)
            """,
            (
                pid,
                result["job_id"],
                result["task_id"],
                result["worker_id"],
                result["id"],
                amount,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        # Race-recovery: someone created the payout between our find and insert.
        winner = conn.execute(
            "SELECT * FROM work_payout WHERE result_id = ?", (result_id,)
        ).fetchone()
        if winner:
            return _execute_payout(conn, adapters, winner, actor_type, actor_id)
        raise

    _audit(conn, actor_type, actor_id, "payout.approve", "WorkPayout", pid, {"amount": amount})

    created = conn.execute(
        "SELECT * FROM work_payout WHERE id = ?", (pid,)
    ).fetchone()
    return _execute_payout(conn, adapters, created, actor_type, actor_id)


def _execute_payout(
    conn: sqlite3.Connection,
    adapters: AdapterBundle,
    payout_row: sqlite3.Row,
    actor_type: str,
    actor_id: str | None,
) -> dict[str, Any]:
    if payout_row["status"] == "paid":
        return {"payout": _to_public_payout(payout_row), "idempotent": True}
    worker = conn.execute(
        "SELECT * FROM work_worker WHERE id = ?", (payout_row["worker_id"],)
    ).fetchone()
    if not worker:
        raise WorkError("WORKER_NOT_FOUND", "worker missing for payout", 500)

    out = adapters.payout.send_payout(
        PayoutInput(
            payout_id=payout_row["id"],
            worker_wallet=worker["wallet_address"],
            amount_anm=payout_row["amount_anm"],
            result_id=payout_row["result_id"],
        )
    )

    now = _now_ms()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE work_payout SET
              status = ?, tx_hash = ?, explorer_url = ?, aicf_claim_id = ?,
              failure_reason = ?, paid_at = ?
            WHERE id = ?
            """,
            (
                out.status,
                out.tx_hash,
                out.explorer_url,
                out.aicf_claim_id,
                out.failure_reason,
                now if out.status == "paid" else None,
                payout_row["id"],
            ),
        )
        if out.status == "paid":
            new_total = add_anm(worker["total_earned_anm"], payout_row["amount_anm"])
            conn.execute(
                """
                UPDATE work_worker SET
                  completed_jobs = completed_jobs + 1,
                  total_earned_anm = ?,
                  status = 'idle'
                WHERE id = ?
                """,
                (new_total, worker["id"]),
            )
            if payout_row["task_id"]:
                conn.execute(
                    "UPDATE work_task SET status = 'paid', updated_at = ? WHERE id = ?",
                    (now, payout_row["task_id"]),
                )
            still_open = conn.execute(
                "SELECT COUNT(*) AS n FROM work_task WHERE job_id = ? AND status != 'paid'",
                (payout_row["job_id"],),
            ).fetchone()["n"]
            if still_open == 0:
                conn.execute(
                    "UPDATE work_job SET status = 'paid', updated_at = ? WHERE id = ?",
                    (now, payout_row["job_id"]),
                )
        elif out.status in ("failed", "manual_review"):
            conn.execute(
                "UPDATE work_worker SET failed_jobs = failed_jobs + 1, status = 'idle' "
                "WHERE id = ?",
                (worker["id"],),
            )
        _audit(
            conn,
            actor_type,
            actor_id,
            "payout.execute",
            "WorkPayout",
            payout_row["id"],
            {"status": out.status, "tx_hash": out.tx_hash},
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    row = conn.execute(
        "SELECT * FROM work_payout WHERE id = ?", (payout_row["id"],)
    ).fetchone()
    return {"payout": _to_public_payout(row), "idempotent": False}


__all__ = [
    "create_job", "get_job", "list_open_jobs",
    "register_worker", "heartbeat_worker", "get_worker", "list_workers",
    "claim_next", "heartbeat_claim", "release_claim",
    "submit_result",
    "verify_result", "list_results_pending_review",
    "approve_payout",
]
