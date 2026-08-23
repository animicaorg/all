"""
aicf.* job-submission RPC methods.

Implements the inference-job API the `distributed-aicf` provider in
ai/agent_runtime/src/agent_runtime/aicf_client.py expects to find on the
node. Methods registered:

    aicf.estimateJobCost
    aicf.submitInferenceJob
    aicf.streamJob
    aicf.jobStatus
    aicf.settleJob
    aicf.workerRegister
    aicf.workerStatus
    aicf.workerClaimNextJob
    aicf.workerSubmitResult

This is a single-process, in-memory implementation of the protocol — it
keeps the job lifecycle correct end-to-end (estimate → submit → stream →
settle) so `animica chat` works, and supports external workers registering
and claiming jobs. If no external worker claims a job within a short
grace period, a built-in local fallback completes it with a stub
response. The stub clearly identifies itself so it isn't mistaken for
real inference output.

State is reset on every node restart by design — distributed jobs are not
yet persisted to chain state. Long-term, the queue/storage and economics
modules under aicf/queue/* and aicf/economics/* will replace this; for
now they provide the helpers we use here (pricing, job id derivation).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from rpc.errors import InternalError, InvalidParams
from rpc.methods import method

log = logging.getLogger("animica.rpc.aicf_jobs")


# ---------------------------------------------------------------------------
# Worker liveness-touch throttle.
# ---------------------------------------------------------------------------
# touch_worker() bumps a worker's last_seen and is called on the HOT path
# aicf.workerClaimNextJob — which every miner polls in a tight uncached loop.
# On the SQLite store each touch is a write that contends with claim_next()'s
# BEGIN IMMEDIATE write lock (busy_timeout up to 5 s), so writing on every claim
# serialized miners and made claims take seconds → RPC timeouts → 503s cascading
# to everyone pointing at the node. Liveness only needs ~seconds granularity, so
# throttle the DB write to at most once per worker per TOUCH_THROTTLE_S and make
# it best-effort (a touch must never slow or fail a claim).
_TOUCH_THROTTLE_S = float(os.environ.get("ANIMICA_AICF_TOUCH_THROTTLE_S", "30"))
_last_touch: Dict[str, float] = {}
_last_touch_lock = threading.Lock()


def _should_touch(address: str) -> bool:
    now = time.monotonic()
    with _last_touch_lock:
        if now - _last_touch.get(address, 0.0) < _TOUCH_THROTTLE_S:
            return False
        _last_touch[address] = now
        if len(_last_touch) > 20000:  # unbounded-growth guard
            cutoff = now - _TOUCH_THROTTLE_S
            for k in [k for k, v in _last_touch.items() if v < cutoff]:
                _last_touch.pop(k, None)
        return True


# ---------------------------------------------------------------------------
# Treasury address & settlement config
# ---------------------------------------------------------------------------

# Where user → AICF-pool transfers go on-chain. Env-overridable so each
# deployment can rotate. The default is a documented well-known burn-style
# address per chain_id; protocol can later sweep it into the AICF state pool.
_DEFAULT_TREASURY_BY_CHAIN: Dict[int, str] = {
    # Mainnet treasury — set by the operator. User-funded inference jobs
    # transfer their estimated cost into this address; the protocol will
    # later sweep it into the AICF state pool for epoch payouts. Operators
    # can override per-node via ANIMICA_AICF_TREASURY_ADDRESS.
    1: "anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt",
}
_TREASURY_ENV = "ANIMICA_AICF_TREASURY_ADDRESS"

# Should submitInferenceJob require a valid signed payment txn that we
# successfully broadcast to the mempool? Default off so node startups
# without mining/peering still serve the chat protocol — flip to "1" in
# production to enforce on-chain settlement strictly.
_REQUIRE_SETTLEMENT_ENV = "ANIMICA_AICF_REQUIRE_SETTLEMENT"


def _treasury_address_for(chain_id: int) -> str:
    override = os.environ.get(_TREASURY_ENV, "").strip()
    if override:
        return override
    if chain_id in _DEFAULT_TREASURY_BY_CHAIN:
        return _DEFAULT_TREASURY_BY_CHAIN[chain_id]
    # Derive a deterministic placeholder for unknown chains. SHA3-256 of
    # a label is the same algorithm the constants above use; this keeps
    # them reproducible without ever needing a private key.
    digest = hashlib.sha3_256(f"aicf-treasury-chain-{chain_id}".encode()).digest()
    try:
        from pq.py.address import encode_address  # type: ignore
        return encode_address(digest, alg_id=0x1001)
    except Exception:
        # Last-resort hex form if bech32 encoder isn't reachable. The
        # tx-body builder accepts hex too.
        return "0x" + digest.hex()


def _require_settlement() -> bool:
    raw = os.environ.get(_REQUIRE_SETTLEMENT_ENV, "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Pricing & defaults
# ---------------------------------------------------------------------------

_TIER_PRICE_PER_KTOK: Dict[str, float] = {
    # Cost in ANM per 1k tokens (prompt + max output combined).
    # Deliberately small — these defaults exist so estimateJobCost is
    # never zero; real pricing should come from chain-anchored policy
    # once the on-chain pricing schedule is wired up.
    "free":     0.0001,
    "standard": 0.001,
    "premium":  0.01,
    "elite":    0.05,
}
_DEFAULT_TIER = "standard"
_TIER_LATENCY_MS: Dict[str, int] = {
    "free": 6000,
    "standard": 3000,
    "premium": 1500,
    "elite": 800,
}

# How long to wait for an external worker to claim a job before the local
# fallback kicks in. Keeps chat usable on a fresh node with no providers
# while still giving real miners a fair window to receive the notify,
# load the model, run inference, and submit results back. Override with
# ANIMICA_AICF_WORKER_CLAIM_GRACE_S to favor stubs on a no-miner node.
_WORKER_CLAIM_GRACE_S = float(
    os.environ.get("ANIMICA_AICF_WORKER_CLAIM_GRACE_S", "30.0")
)
# How long a worker lease is valid; if a worker claims and goes silent,
# the job becomes reclaimable after this many seconds. The default is
# generous so a miner downloading multi-GB model weights on its first
# inference job does not have its lease reclaimed mid-download. Override
# with ANIMICA_AICF_WORKER_LEASE_S to enforce stricter SLAs.
_WORKER_LEASE_S = float(
    os.environ.get("ANIMICA_AICF_WORKER_LEASE_S", "600.0")
)
# Race replication factor: how many distinct workers can claim the same
# job concurrently. First valid worker submit wins (and is paid); losers
# get a clean "lost_race" reply. K=1 reverts to the pre-race assignment
# behavior. Sensible defaults assume tiers have cheap per-token pricing
# so K=3 fanout is affordable for chat latency wins.
_REPLICAS_DEFAULT = max(1, int(
    os.environ.get("ANIMICA_AICF_REPLICAS", "3") or "3"
))

# Pipeline (model-parallel) defaults. When at least this many workers
# advertise the "pipeline" tier, new jobs default to pipeline mode and
# distribute layer ranges across stages. With fewer workers we fall
# back to race replication so chat never hangs waiting for stages that
# nobody can serve. ANIMICA_AICF_PIPELINE=0 disables the path entirely
# (race-only); ANIMICA_AICF_PIPELINE_STAGES sets the default stage
# count when pipeline mode is selected.
_PIPELINE_ENABLED = (
    os.environ.get("ANIMICA_AICF_PIPELINE", "1").strip().lower()
    not in {"0", "false", "off", "no"}
)
_PIPELINE_STAGES_DEFAULT = max(2, int(
    os.environ.get("ANIMICA_AICF_PIPELINE_STAGES", "2") or "2"
))
_PIPELINE_TIER_LABEL = "pipeline"
# How long a single pipeline stage may sit in "claimed" before its lease
# expires and another worker may reclaim it. Shorter than the regular
# job lease because a stalled stage blocks the WHOLE pipeline, not just
# one race competitor.
_PIPELINE_STAGE_LEASE_S = float(
    os.environ.get("ANIMICA_AICF_PIPELINE_STAGE_LEASE_S", "120.0")
)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------


@dataclass
class _JobRecord:
    job_id: str
    spec: Dict[str, Any]
    payment: Dict[str, Any]
    estimated_cost: float
    tier: str
    state: str = "pending"          # pending → claimed → completed | failed
    text: str = ""
    provider_id: str = ""
    created_at: float = field(default_factory=time.time)
    claimed_at: Optional[float] = None
    completed_at: Optional[float] = None
    # `claim_owner` / `claim_expires_at` reflect the MOST RECENT claim and
    # are kept for backward-compat logging. The authoritative list of
    # active claims (for K-way race replication) lives in `claims`.
    claim_owner: Optional[str] = None
    claim_expires_at: Optional[float] = None
    error: Optional[str] = None
    # On-chain settlement: filled when the payment txn is broadcast.
    payment_tx_hash: Optional[str] = None
    payment_accepted: bool = False
    payment_status: Optional[str] = None
    settled_chain_id: Optional[int] = None
    # Race replication. `replicas_wanted` is the max number of workers
    # that may claim this job in parallel; the first to submit a valid
    # result wins (and is credited). `claims` is the canonical list of
    # currently-active leases: each entry is
    # `{"address": str, "claimed_at": float, "expires_at": float}`.
    replicas_wanted: int = 1
    claims: List[Dict[str, Any]] = field(default_factory=list)
    winner_address: Optional[str] = None
    # Pipeline (model-parallel) mode. When mode == "pipeline", `stages`
    # holds an ordered list of stage records that chain together to
    # produce the final result. Each stage dict is:
    #   {
    #     "index": int,
    #     "state": "pending" | "claimed" | "completed" | "failed",
    #     "worker_address": Optional[str],
    #     "claimed_at": Optional[float],
    #     "expires_at": Optional[float],
    #     "output_b64": Optional[str],   # downstream-visible payload
    #     "output_text": Optional[str],  # final-stage rendered text
    #     "meta": Dict[str, Any],         # shape/dtype hints
    #   }
    # The final stage's `output_text` becomes `_JobRecord.text` on
    # completion so the existing aicf.streamJob / settleJob surface
    # still works unchanged.
    mode: str = "race"
    stages: List[Dict[str, Any]] = field(default_factory=list)
    # Streaming-decode coordinator state. The final stage appends the
    # next-token id to ``decode_inbox`` after sampling each token;
    # stage 0 long-polls the inbox to learn what to embed for the
    # next decode step. Plain in-memory FIFO; bounded by max_new_tokens
    # so it can never grow without bound. ``decode_step_cursor`` is
    # the highest step_index any stage has produced — final stage uses
    # it to label new steps without race conditions.
    decode_inbox: List[int] = field(default_factory=list)
    decode_step_cursor: int = 0


@dataclass
class _WorkerInfo:
    address: str
    tiers: List[str]
    hardware: Dict[str, Any]
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    jobs_completed: int = 0
    # Earnings ledger. `earnings_pending_animica` accumulates the cost
    # of each job the worker has completed but which has not yet been
    # settled on-chain (treasury → state-pool sweep is a future
    # consensus upgrade). `earnings_paid_animica` is the running total
    # of confirmed payouts; right now only ever populated manually.
    earnings_pending_animica: float = 0.0
    earnings_paid_animica: float = 0.0
    # Optional reachable HTTP(S) endpoint for direct worker-to-worker
    # activation transport in pipeline mode. When set, downstream
    # stages fetch activations directly from this URL (saving a round
    # trip through the node). Empty string means "node-proxy only" —
    # see aicf.pipelineGetUpstreamActivation for that path.
    direct_endpoint: str = ""


class _AicfJobStore:
    """Thread-safe in-memory store for AICF jobs and workers."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, _JobRecord] = {}
        self._workers: Dict[str, _WorkerInfo] = {}

    # ---------- jobs ----------

    def submit(self, job: _JobRecord) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[_JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def claim_next(self, worker_addr: str, tiers: List[str]) -> Optional[_JobRecord]:
        # Empty tiers means "this worker advertised no capability" — match
        # nothing. Past inverted semantic ("not tiers → match all") let
        # bare-RPC callers and stack-less miners (no torch) claim every
        # job and return stubs the user still paid for.
        if not tiers:
            return None
        now = time.time()
        with self._lock:
            for job in self._jobs.values():
                if job.state in {"completed", "failed"}:
                    continue
                if job.tier not in tiers:
                    continue
                # Pipeline jobs are claimed stage-by-stage via
                # pipelineClaimStage, not as a single race winner.
                if job.mode == "pipeline":
                    continue
                # Drop expired claims so a slow worker doesn't lock out
                # the slot indefinitely. Note this mutates the live job.
                live_claims = [
                    c for c in job.claims
                    if float(c.get("expires_at") or 0.0) > now
                ]
                job.claims = live_claims
                # Don't let the same worker double-claim a job.
                if any(c.get("address") == worker_addr for c in live_claims):
                    continue
                # Cap concurrent claims at replicas_wanted (K-way race).
                cap = max(1, int(job.replicas_wanted or 1))
                if len(live_claims) >= cap:
                    continue
                claim = {
                    "address": worker_addr,
                    "claimed_at": now,
                    "expires_at": now + _WORKER_LEASE_S,
                }
                job.claims.append(claim)
                job.state = "claimed"
                # Mirror to legacy single-claim fields for logging compat.
                job.claim_owner = worker_addr
                job.claimed_at = now
                job.claim_expires_at = now + _WORKER_LEASE_S
                return job
        return None

    def complete(
        self,
        job_id: str,
        *,
        text: str,
        provider_id: str,
    ) -> Optional[_JobRecord]:
        """Atomic first-writer-wins. Returns the updated record on success
        or None if the job was already terminal (someone else won the
        race, or the stub fallback already answered). Callers must treat
        None as "lost the race — do not credit this worker."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state in {"completed", "failed"}:
                return None
            job.text = text
            job.provider_id = provider_id
            job.state = "completed"
            job.completed_at = time.time()
            job.winner_address = provider_id
            return job

    def fail(self, job_id: str, error: str) -> Optional[_JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.state = "failed"
            job.error = error
            job.completed_at = time.time()
            return job

    # ---------- pipeline-stage operations ----------

    def append_decode_step(
        self,
        job_id: str,
        stage_index: int,
        *,
        worker_addr: str,
        step_index: int,
        output_b64: Optional[str],
        output_text: Optional[str],
        meta: Dict[str, Any],
    ) -> tuple[Optional[_JobRecord], str]:
        """Append a decode-step entry to ``stage.decode_steps`` (in-memory).

        See _SqliteAicfJobStore.append_decode_step for the contract and
        return values; both implementations are kept behaviour-equivalent.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, "unknown_job"
            if job.state in {"completed", "failed"}:
                return job, "already_terminal"
            stage = next(
                (s for s in job.stages if int(s.get("index", -1)) == int(stage_index)),
                None,
            )
            if stage is None:
                return job, "unknown_stage"
            owner = stage.get("worker_address")
            if owner and owner != worker_addr:
                return job, "lost_lease"
            steps = stage.setdefault("decode_steps", [])
            if any(int(s.get("step_index", -1)) == int(step_index) for s in steps):
                return job, "duplicate"
            now = time.time()
            steps.append({
                "step_index": int(step_index),
                "output_b64": output_b64,
                "output_text": output_text,
                "completed_at": now,
                "meta": dict(meta or {}),
            })
            stage["current_step"] = int(step_index)
            highest = max(int(s.get("index", 0)) for s in job.stages)
            is_final_stage = int(stage_index) == highest
            # Final-stage steps stream characters into job.text inline
            # so the terminal-step transition + the last token append
            # happen under the same lock — otherwise a terminal step
            # whose text-append runs after the state flip would silently
            # drop the last character.
            if is_final_stage and output_text:
                job.text = (job.text or "") + str(output_text)
            if is_final_stage and bool((meta or {}).get("is_terminal")):
                job.state = "completed"
                job.completed_at = now
                job.provider_id = worker_addr
                job.winner_address = worker_addr
                return job, "completed_job"
            return job, "appended"

    def append_job_text(self, job_id: str, delta: str) -> Optional[_JobRecord]:
        """Append ``delta`` to ``job.text`` atomically. Drives the
        per-token streamJob deltas the chat client picks up."""
        if not delta:
            return self._jobs.get(job_id)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state in {"completed", "failed"}:
                return job
            job.text = (job.text or "") + delta
            return job

    def push_decode_inbox(
        self, job_id: str, token_id: int, *, max_inbox: int = 4096,
    ) -> Optional[_JobRecord]:
        """Append a token id to the per-job decode inbox.

        The final stage pushes the just-sampled token; stage 0 will
        pop it on its next poll and embed it for the next decode step.
        Bounded by ``max_inbox`` so a runaway producer can't OOM the
        node; an overflow drops the oldest token (oldest tokens have
        already been embedded by stage 0 — see pop_decode_inbox)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.state in {"completed", "failed"}:
                return job
            job.decode_inbox.append(int(token_id))
            if len(job.decode_inbox) > int(max_inbox):
                job.decode_inbox = job.decode_inbox[-int(max_inbox):]
            return job

    def pop_decode_inbox(self, job_id: str) -> tuple[Optional[_JobRecord], Optional[int]]:
        """Pop the oldest token from the decode inbox. Returns (job, token)
        with token=None when the inbox is empty (caller polls again)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, None
            if not job.decode_inbox:
                return job, None
            token = int(job.decode_inbox.pop(0))
            return job, token

    def next_decode_step_index(self, job_id: str) -> Optional[int]:
        """Atomically allocate a new monotonic decode step index for a job.
        Used by the final stage so labelling step k+1 is race-free even
        across multiple node processes sharing the SQLite store."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.decode_step_cursor = int(job.decode_step_cursor or 0) + 1
            return job.decode_step_cursor

    def get_decode_step(
        self,
        job_id: str,
        stage_index: int,
        step_index: int,
    ) -> Optional[Dict[str, Any]]:
        """Snapshot lookup of a recorded decode-step. Returns a copy so
        callers can read without holding the lock."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            stage = next(
                (s for s in job.stages if int(s.get("index", -1)) == int(stage_index)),
                None,
            )
            if stage is None:
                return None
            for s in stage.get("decode_steps") or []:
                if int(s.get("step_index", -1)) == int(step_index):
                    return dict(s)
            return None

    def claim_pipeline_stage(
        self,
        worker_addr: str,
        *,
        job_id_filter: Optional[str] = None,
    ) -> Optional[tuple[_JobRecord, int]]:
        """Atomically claim the lowest unfilled stage in some pipeline job.

        Returns ``(job, stage_index)`` on success, or None when nothing is
        claimable. Expired claims are reclaimed before pending stages are
        considered. A worker cannot hold two stages of the same job
        simultaneously — that would deadlock the pipeline (stage k+1
        would wait on its own activation from stage k).
        """
        if not worker_addr:
            return None
        now = time.time()
        with self._lock:
            candidates = [
                j for j in self._jobs.values()
                if j.mode == "pipeline"
                and j.state not in {"completed", "failed"}
                and (job_id_filter is None or j.job_id == job_id_filter)
            ]
            candidates.sort(key=lambda j: j.created_at)
            for job in candidates:
                # Refuse to assign a second stage to the same worker.
                if any(
                    s.get("worker_address") == worker_addr
                    and s.get("state") in {"claimed", "completed"}
                    and float(s.get("expires_at") or 0.0) > now
                    for s in job.stages
                ):
                    continue
                stages_sorted = sorted(
                    job.stages,
                    key=lambda s: int(s.get("index", 0)),
                )
                for stage in stages_sorted:
                    st = stage.get("state")
                    if st == "claimed":
                        exp = float(stage.get("expires_at") or 0.0)
                        if exp > now:
                            continue
                    if st in {"pending", "claimed"}:
                        stage["state"] = "claimed"
                        stage["worker_address"] = worker_addr
                        stage["claimed_at"] = now
                        stage["expires_at"] = now + _PIPELINE_STAGE_LEASE_S
                        job.state = "claimed"
                        return job, int(stage["index"])
        return None

    def submit_pipeline_stage(
        self,
        job_id: str,
        stage_index: int,
        *,
        worker_addr: str,
        output_b64: Optional[str],
        output_text: Optional[str],
        meta: Dict[str, Any],
    ) -> tuple[Optional[_JobRecord], str]:
        """Record a stage's output. Returns ``(job, status)`` where status
        is one of: "completed_stage" (recorded), "completed_job" (final
        stage, job is now terminal), "lost_lease" (stage was reassigned),
        "unknown_stage", "unknown_job", "already_terminal"."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None, "unknown_job"
            if job.state in {"completed", "failed"}:
                return job, "already_terminal"
            stage = next(
                (s for s in job.stages if int(s.get("index", -1)) == int(stage_index)),
                None,
            )
            if stage is None:
                return job, "unknown_stage"
            if stage.get("state") == "completed":
                return job, "already_terminal"
            owner = stage.get("worker_address")
            if owner and owner != worker_addr:
                # Lease still belongs to someone else — refuse so we
                # don't accept a result from a worker the scheduler
                # already preempted.
                return job, "lost_lease"
            stage["state"] = "completed"
            stage["worker_address"] = worker_addr
            stage["completed_at"] = time.time()
            stage["output_b64"] = output_b64
            stage["output_text"] = output_text
            stage["meta"] = dict(meta or {})
            # Final stage closes the job — UNLESS the caller flagged
            # the submit as a streaming-decode prefill landmark
            # (meta.non_terminal). In that case the stage record stays
            # "completed" but the wrapping job stays "claimed" so the
            # decode loop has somewhere to write.
            highest = max(int(s.get("index", 0)) for s in job.stages)
            non_terminal = bool((meta or {}).get("non_terminal"))
            if int(stage_index) == highest and not non_terminal:
                job.state = "completed"
                job.completed_at = time.time()
                job.text = output_text or job.text
                job.provider_id = worker_addr
                job.winner_address = worker_addr
                return job, "completed_job"
            return job, "completed_stage"

    # ---------- workers ----------

    def register_worker(self, info: _WorkerInfo) -> None:
        with self._lock:
            self._workers[info.address] = info

    def get_worker(self, address: str) -> Optional[_WorkerInfo]:
        # Read-only: status reads must not count as liveness, or tier
        # availability becomes self-fulfilling (probing a worker "revives" it).
        with self._lock:
            return self._workers.get(address)

    def touch_worker(self, address: str) -> None:
        # In-memory store: the write is cheap, but throttle anyway for parity
        # so behaviour matches the SQLite path (and to skip the lock churn).
        if not _should_touch(address):
            return
        with self._lock:
            w = self._workers.get(address)
            if w is not None:
                w.last_seen = time.time()

    def sign_off_worker(self, address: str) -> bool:
        """Tab-closed / ctrl-C sign-off: drop the worker from every ONLINE view
        without touching its earnings ledger (the settlement anchors pay from
        that — deleting the row would wipe unpaid IOUs). last_seen=0 removes it
        from workerCount online windows; tiers=["offline"] removes it from
        estimateJobCost provider counts (whose `not tiers` means ALL tiers)."""
        with self._lock:
            w = self._workers.get(address)
            if w is None:
                return False
            w.last_seen = 0.0
            w.tiers = ["offline"]
            return True

    def all_workers(self) -> List[_WorkerInfo]:
        with self._lock:
            return list(self._workers.values())


# ---------------------------------------------------------------------------
# SQLite-backed persistent store
# ---------------------------------------------------------------------------
#
# Same API as _AicfJobStore so the rest of the module is store-agnostic.
# Persists job queue + worker ledger across node restarts under a
# SQLite file at ANIMICA_AICF_JOB_DB (default: <ANIMICA_DATA_DIR>/aicf_jobs.db).
# WAL journal mode + busy_timeout makes the same file safe to open
# concurrently from multiple pool/node processes — that's how
# "cross-pool job sharing" works without consensus replication: every
# process pointing at the same DB file (or a shared mount) sees the
# same claims/results in real time.

import json as _json
import sqlite3 as _sqlite3

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    payment_json TEXT NOT NULL,
    estimated_cost REAL NOT NULL,
    tier TEXT NOT NULL,
    state TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    provider_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    claimed_at REAL,
    completed_at REAL,
    claim_owner TEXT,
    claim_expires_at REAL,
    error TEXT,
    payment_tx_hash TEXT,
    payment_accepted INTEGER NOT NULL DEFAULT 0,
    payment_status TEXT,
    settled_chain_id INTEGER,
    replicas_wanted INTEGER NOT NULL DEFAULT 1,
    claims_json TEXT NOT NULL DEFAULT '[]',
    winner_address TEXT,
    mode TEXT NOT NULL DEFAULT 'race',
    stages_json TEXT NOT NULL DEFAULT '[]',
    decode_inbox_json TEXT NOT NULL DEFAULT '[]',
    decode_step_cursor INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_state_tier ON jobs(state, tier);
CREATE INDEX IF NOT EXISTS idx_jobs_claim_owner ON jobs(claim_owner);

-- Migrations for jobs DBs that pre-date race-replication columns. ALTER
-- TABLE ADD COLUMN with a default is constant-time in SQLite, so this is
-- safe for large stores. Errors are swallowed at the call site when the
-- column already exists.

CREATE TABLE IF NOT EXISTS workers (
    address TEXT PRIMARY KEY,
    tiers_json TEXT NOT NULL,
    hardware_json TEXT NOT NULL,
    registered_at REAL NOT NULL,
    last_seen REAL NOT NULL,
    jobs_completed INTEGER NOT NULL DEFAULT 0,
    earnings_pending_animica REAL NOT NULL DEFAULT 0,
    earnings_paid_animica REAL NOT NULL DEFAULT 0,
    direct_endpoint TEXT NOT NULL DEFAULT ''
);
"""


def _resolve_job_db_path() -> Optional[str]:
    override = os.environ.get("ANIMICA_AICF_JOB_DB", "").strip()
    if override:
        return override
    data_dir = os.environ.get("ANIMICA_DATA_DIR", "").strip()
    if not data_dir:
        return None  # caller falls back to in-memory store
    try:
        os.makedirs(data_dir, exist_ok=True)
    except Exception as exc:
        log.warning("aicf_jobs: cannot create data dir %s: %s", data_dir, exc)
        return None
    return os.path.join(data_dir, "aicf_jobs.db")


class _SqliteAicfJobStore:
    """SQLite-backed AICF job + worker store.

    Same API surface as _AicfJobStore so call sites are unchanged.
    Connection is opened per-thread (sqlite3 connections aren't safe
    to share across threads); we route all writes through a single
    file lock by using WAL + busy_timeout instead of an in-process
    lock so multi-process operation works the same as multi-thread.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._tls = threading.local()
        # Initialize schema once.
        conn = self._conn()
        with conn:
            conn.executescript(_SCHEMA_SQL)
        self._migrate_race_columns(conn)

    @staticmethod
    def _migrate_race_columns(conn: _sqlite3.Connection) -> None:
        """Add the race-replication columns to a pre-existing jobs table.

        ALTER TABLE ... ADD COLUMN is idempotent only when wrapped in a
        try/except — SQLite returns OperationalError if the column is
        already present. The defaults match _JobRecord so pre-race rows
        keep behaving like K=1 with no active replicas.
        """
        for col, ddl in (
            ("replicas_wanted",
             "ALTER TABLE jobs ADD COLUMN replicas_wanted INTEGER NOT NULL DEFAULT 1"),
            ("claims_json",
             "ALTER TABLE jobs ADD COLUMN claims_json TEXT NOT NULL DEFAULT '[]'"),
            ("winner_address",
             "ALTER TABLE jobs ADD COLUMN winner_address TEXT"),
            ("mode",
             "ALTER TABLE jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'race'"),
            ("stages_json",
             "ALTER TABLE jobs ADD COLUMN stages_json TEXT NOT NULL DEFAULT '[]'"),
            ("decode_inbox_json",
             "ALTER TABLE jobs ADD COLUMN decode_inbox_json TEXT NOT NULL DEFAULT '[]'"),
            ("decode_step_cursor",
             "ALTER TABLE jobs ADD COLUMN decode_step_cursor INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(ddl)
            except _sqlite3.OperationalError:
                pass
        # Workers table migrations.
        for ddl in (
            "ALTER TABLE workers ADD COLUMN direct_endpoint TEXT NOT NULL DEFAULT ''",
        ):
            try:
                conn.execute(ddl)
            except _sqlite3.OperationalError:
                pass

    def _conn(self) -> _sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = _sqlite3.connect(
                self._db_path,
                timeout=10.0,
                isolation_level=None,  # autocommit; we use explicit txns
                check_same_thread=True,
            )
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._tls.conn = conn
        return conn

    # ---------- (de)serialization ----------

    @staticmethod
    def _job_from_row(row: _sqlite3.Row) -> _JobRecord:
        keys = row.keys()
        replicas = int(row["replicas_wanted"]) if "replicas_wanted" in keys else 1
        try:
            claims_raw = row["claims_json"] if "claims_json" in keys else "[]"
            claims = _json.loads(claims_raw) if claims_raw else []
            if not isinstance(claims, list):
                claims = []
        except (ValueError, TypeError):
            claims = []
        winner = row["winner_address"] if "winner_address" in keys else None
        mode = row["mode"] if "mode" in keys else "race"
        try:
            stages_raw = row["stages_json"] if "stages_json" in keys else "[]"
            stages = _json.loads(stages_raw) if stages_raw else []
            if not isinstance(stages, list):
                stages = []
        except (ValueError, TypeError):
            stages = []
        try:
            inbox_raw = (row["decode_inbox_json"]
                         if "decode_inbox_json" in keys else "[]")
            inbox = _json.loads(inbox_raw) if inbox_raw else []
            if not isinstance(inbox, list):
                inbox = []
        except (ValueError, TypeError):
            inbox = []
        decode_cursor = int(row["decode_step_cursor"]) if "decode_step_cursor" in keys else 0
        return _JobRecord(
            job_id=row["job_id"],
            spec=_json.loads(row["spec_json"]),
            payment=_json.loads(row["payment_json"]),
            estimated_cost=float(row["estimated_cost"]),
            tier=row["tier"],
            state=row["state"],
            text=row["text"] or "",
            provider_id=row["provider_id"] or "",
            created_at=float(row["created_at"]),
            claimed_at=row["claimed_at"],
            completed_at=row["completed_at"],
            claim_owner=row["claim_owner"],
            claim_expires_at=row["claim_expires_at"],
            error=row["error"],
            payment_tx_hash=row["payment_tx_hash"],
            payment_accepted=bool(row["payment_accepted"]),
            payment_status=row["payment_status"],
            settled_chain_id=row["settled_chain_id"],
            replicas_wanted=replicas,
            claims=claims,
            winner_address=winner,
            mode=mode or "race",
            stages=stages,
            decode_inbox=[int(t) for t in inbox],
            decode_step_cursor=int(decode_cursor),
        )

    @staticmethod
    def _worker_from_row(row: _sqlite3.Row) -> _WorkerInfo:
        keys = row.keys()
        endpoint = row["direct_endpoint"] if "direct_endpoint" in keys else ""
        return _WorkerInfo(
            address=row["address"],
            tiers=_json.loads(row["tiers_json"]),
            hardware=_json.loads(row["hardware_json"]),
            registered_at=float(row["registered_at"]),
            last_seen=float(row["last_seen"]),
            jobs_completed=int(row["jobs_completed"]),
            earnings_pending_animica=float(row["earnings_pending_animica"]),
            earnings_paid_animica=float(row["earnings_paid_animica"]),
            direct_endpoint=endpoint or "",
        )

    # ---------- jobs ----------

    def submit(self, job: _JobRecord) -> None:
        conn = self._conn()
        conn.execute(
            "INSERT OR REPLACE INTO jobs ("
            "  job_id,spec_json,payment_json,estimated_cost,tier,state,text,"
            "  provider_id,created_at,claimed_at,completed_at,claim_owner,"
            "  claim_expires_at,error,payment_tx_hash,payment_accepted,"
            "  payment_status,settled_chain_id,replicas_wanted,claims_json,"
            "  winner_address,mode,stages_json,decode_inbox_json,"
            "  decode_step_cursor"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job.job_id,
                _json.dumps(job.spec),
                _json.dumps(job.payment),
                job.estimated_cost,
                job.tier,
                job.state,
                job.text,
                job.provider_id,
                job.created_at,
                job.claimed_at,
                job.completed_at,
                job.claim_owner,
                job.claim_expires_at,
                job.error,
                job.payment_tx_hash,
                1 if job.payment_accepted else 0,
                job.payment_status,
                job.settled_chain_id,
                int(job.replicas_wanted or 1),
                _json.dumps(job.claims or []),
                job.winner_address,
                job.mode or "race",
                _json.dumps(job.stages or []),
                _json.dumps(job.decode_inbox or []),
                int(job.decode_step_cursor or 0),
            ),
        )

    def get(self, job_id: str) -> Optional[_JobRecord]:
        row = self._conn().execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return self._job_from_row(row) if row else None

    def claim_next(self, worker_addr: str, tiers: List[str]) -> Optional[_JobRecord]:
        # Empty tiers means "worker has no advertised capability" — match
        # nothing. See _AicfJobStore.claim_next above for the rationale.
        if not tiers:
            return None
        now = time.time()
        conn = self._conn()
        # Single transaction so concurrent claimers can't pick the
        # same job. SQLite serializes writers, busy_timeout handles
        # the cross-process case.
        conn.execute("BEGIN IMMEDIATE")
        try:
            placeholders = ",".join(["?"] * len(tiers))
            tier_filter = f" AND tier IN ({placeholders})"
            # Scan candidate non-terminal jobs in this worker's tier and
            # pick the first one with an open replica slot. We can't
            # express "claims count below cap" as a pure SQL filter
            # without a join table, so do it in Python with a small N.
            # Race-mode rows only — pipeline jobs are surfaced via the
            # pipelineClaimStage path so the race claim_next must never
            # see them, even if a misconfigured worker passed the same
            # tier as a pipeline job.
            candidates = conn.execute(
                "SELECT * FROM jobs WHERE state IN ('pending','claimed')"
                " AND mode='race'"
                + tier_filter
                + " ORDER BY created_at ASC LIMIT 64",
                tuple(tiers),
            ).fetchall()
            chosen_row = None
            chosen_claims: List[Dict[str, Any]] = []
            for row in candidates:
                cap = int(row["replicas_wanted"]) if "replicas_wanted" in row.keys() else 1
                cap = max(1, cap)
                try:
                    raw = row["claims_json"] if "claims_json" in row.keys() else "[]"
                    claims = _json.loads(raw) if raw else []
                    if not isinstance(claims, list):
                        claims = []
                except (ValueError, TypeError):
                    claims = []
                live = [
                    c for c in claims
                    if float(c.get("expires_at") or 0.0) > now
                ]
                if any(c.get("address") == worker_addr for c in live):
                    continue
                if len(live) >= cap:
                    continue
                chosen_row = row
                chosen_claims = live
                break
            if chosen_row is None:
                conn.execute("COMMIT")
                return None
            chosen_claims.append({
                "address": worker_addr,
                "claimed_at": now,
                "expires_at": now + _WORKER_LEASE_S,
            })
            conn.execute(
                "UPDATE jobs SET state='claimed',claim_owner=?,claimed_at=?,"
                "claim_expires_at=?,claims_json=? WHERE job_id=?",
                (
                    worker_addr,
                    now,
                    now + _WORKER_LEASE_S,
                    _json.dumps(chosen_claims),
                    chosen_row["job_id"],
                ),
            )
            conn.execute("COMMIT")
            updated = self.get(chosen_row["job_id"])
            return updated
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def complete(
        self,
        job_id: str,
        *,
        text: str,
        provider_id: str,
    ) -> Optional[_JobRecord]:
        """First-writer-wins via guarded UPDATE. Returns None when the
        row was already terminal (lost the race) so callers know not to
        credit the worker. SQLite's rowcount tells us how many rows the
        WHERE clause matched after the update."""
        now = time.time()
        conn = self._conn()
        cur = conn.execute(
            "UPDATE jobs SET state='completed',text=?,provider_id=?,"
            "completed_at=?,winner_address=? "
            "WHERE job_id=? AND state IN ('pending','claimed')",
            (text, provider_id, now, provider_id, job_id),
        )
        if (cur.rowcount or 0) <= 0:
            return None
        return self.get(job_id)

    def fail(self, job_id: str, error: str) -> Optional[_JobRecord]:
        now = time.time()
        with self._conn():
            self._conn().execute(
                "UPDATE jobs SET state='failed',error=?,completed_at=? "
                "WHERE job_id=?",
                (error, now, job_id),
            )
        return self.get(job_id)

    # ---------- pipeline-stage operations ----------

    def append_decode_step(
        self,
        job_id: str,
        stage_index: int,
        *,
        worker_addr: str,
        step_index: int,
        output_b64: Optional[str],
        output_text: Optional[str],
        meta: Dict[str, Any],
    ) -> tuple[Optional[_JobRecord], str]:
        """Atomically append a decode-step entry inside stages_json.

        Returns one of: ("unknown_job", "already_terminal",
        "unknown_stage", "lost_lease", "duplicate", "appended",
        "completed_job"). See _AicfJobStore.append_decode_step for
        prose-level semantics.
        """
        now = time.time()
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None, "unknown_job"
            job = self._job_from_row(row)
            if job.state in {"completed", "failed"}:
                conn.execute("COMMIT")
                return job, "already_terminal"
            stages = list(job.stages)
            target = next(
                (i for i, s in enumerate(stages)
                 if int(s.get("index", -1)) == int(stage_index)),
                None,
            )
            if target is None:
                conn.execute("COMMIT")
                return job, "unknown_stage"
            stage = dict(stages[target])
            owner = stage.get("worker_address")
            if owner and owner != worker_addr:
                conn.execute("COMMIT")
                return job, "lost_lease"
            steps = list(stage.get("decode_steps") or [])
            if any(int(s.get("step_index", -1)) == int(step_index) for s in steps):
                conn.execute("COMMIT")
                return job, "duplicate"
            steps.append({
                "step_index": int(step_index),
                "output_b64": output_b64,
                "output_text": output_text,
                "completed_at": now,
                "meta": dict(meta or {}),
            })
            stage["decode_steps"] = steps
            stage["current_step"] = int(step_index)
            stages[target] = stage

            highest = max(int(s.get("index", 0)) for s in stages)
            is_final_stage = int(stage_index) == highest
            terminal = bool((meta or {}).get("is_terminal")) and is_final_stage
            new_text = job.text or ""
            if is_final_stage and output_text:
                new_text = new_text + str(output_text)
            if terminal:
                conn.execute(
                    "UPDATE jobs SET state='completed', provider_id=?,"
                    " completed_at=?, winner_address=?, stages_json=?, text=?"
                    " WHERE job_id=?",
                    (worker_addr, now, worker_addr,
                     _json.dumps(stages), new_text, job_id),
                )
            elif is_final_stage and output_text:
                conn.execute(
                    "UPDATE jobs SET stages_json=?, text=? WHERE job_id=?",
                    (_json.dumps(stages), new_text, job_id),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET stages_json=? WHERE job_id=?",
                    (_json.dumps(stages), job_id),
                )
            conn.execute("COMMIT")
            return self.get(job_id), ("completed_job" if terminal else "appended")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def append_job_text(self, job_id: str, delta: str) -> Optional[_JobRecord]:
        if not delta:
            return self.get(job_id)
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job = self._job_from_row(row)
            if job.state in {"completed", "failed"}:
                conn.execute("COMMIT")
                return job
            new_text = (job.text or "") + delta
            conn.execute(
                "UPDATE jobs SET text=? WHERE job_id=?",
                (new_text, job_id),
            )
            conn.execute("COMMIT")
            return self.get(job_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def push_decode_inbox(
        self, job_id: str, token_id: int, *, max_inbox: int = 4096,
    ) -> Optional[_JobRecord]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job = self._job_from_row(row)
            if job.state in {"completed", "failed"}:
                conn.execute("COMMIT")
                return job
            inbox = list(job.decode_inbox)
            inbox.append(int(token_id))
            if len(inbox) > int(max_inbox):
                inbox = inbox[-int(max_inbox):]
            conn.execute(
                "UPDATE jobs SET decode_inbox_json=? WHERE job_id=?",
                (_json.dumps(inbox), job_id),
            )
            conn.execute("COMMIT")
            return self.get(job_id)
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def pop_decode_inbox(self, job_id: str) -> tuple[Optional[_JobRecord], Optional[int]]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None, None
            job = self._job_from_row(row)
            if not job.decode_inbox:
                conn.execute("COMMIT")
                return job, None
            inbox = list(job.decode_inbox)
            token = int(inbox.pop(0))
            conn.execute(
                "UPDATE jobs SET decode_inbox_json=? WHERE job_id=?",
                (_json.dumps(inbox), job_id),
            )
            conn.execute("COMMIT")
            return self.get(job_id), token
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def next_decode_step_index(self, job_id: str) -> Optional[int]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job = self._job_from_row(row)
            new_cursor = int(job.decode_step_cursor or 0) + 1
            conn.execute(
                "UPDATE jobs SET decode_step_cursor=? WHERE job_id=?",
                (new_cursor, job_id),
            )
            conn.execute("COMMIT")
            return new_cursor
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get_decode_step(
        self,
        job_id: str,
        stage_index: int,
        step_index: int,
    ) -> Optional[Dict[str, Any]]:
        job = self.get(job_id)
        if job is None:
            return None
        stage = next(
            (s for s in job.stages
             if int(s.get("index", -1)) == int(stage_index)),
            None,
        )
        if stage is None:
            return None
        for s in stage.get("decode_steps") or []:
            if int(s.get("step_index", -1)) == int(step_index):
                return dict(s)
        return None

    def claim_pipeline_stage(
        self,
        worker_addr: str,
        *,
        job_id_filter: Optional[str] = None,
    ) -> Optional[tuple[_JobRecord, int]]:
        """Same semantics as _AicfJobStore.claim_pipeline_stage but
        durable. Uses BEGIN IMMEDIATE so two concurrent workers can't
        each be told they own the same stage."""
        if not worker_addr:
            return None
        now = time.time()
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            params: tuple[Any, ...] = ()
            sql = (
                "SELECT * FROM jobs WHERE mode='pipeline'"
                " AND state NOT IN ('completed','failed')"
            )
            if job_id_filter:
                sql += " AND job_id=?"
                params = (job_id_filter,)
            sql += " ORDER BY created_at ASC LIMIT 64"
            candidates = conn.execute(sql, params).fetchall()
            for row in candidates:
                job = self._job_from_row(row)
                # No double-staging the same worker.
                if any(
                    s.get("worker_address") == worker_addr
                    and s.get("state") in {"claimed", "completed"}
                    and float(s.get("expires_at") or 0.0) > now
                    for s in job.stages
                ):
                    continue
                stages = sorted(job.stages, key=lambda s: int(s.get("index", 0)))
                target_idx: Optional[int] = None
                for i, stage in enumerate(stages):
                    st = stage.get("state")
                    if st == "claimed":
                        exp = float(stage.get("expires_at") or 0.0)
                        if exp > now:
                            continue
                    if st in {"pending", "claimed"}:
                        stages[i] = {
                            **stage,
                            "state": "claimed",
                            "worker_address": worker_addr,
                            "claimed_at": now,
                            "expires_at": now + _PIPELINE_STAGE_LEASE_S,
                        }
                        target_idx = int(stage.get("index", i))
                        break
                if target_idx is None:
                    continue
                conn.execute(
                    "UPDATE jobs SET state='claimed', stages_json=? WHERE job_id=?",
                    (_json.dumps(stages), job.job_id),
                )
                conn.execute("COMMIT")
                updated = self.get(job.job_id)
                if updated is None:
                    return None
                return updated, target_idx
            conn.execute("COMMIT")
            return None
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def submit_pipeline_stage(
        self,
        job_id: str,
        stage_index: int,
        *,
        worker_addr: str,
        output_b64: Optional[str],
        output_text: Optional[str],
        meta: Dict[str, Any],
    ) -> tuple[Optional[_JobRecord], str]:
        now = time.time()
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None, "unknown_job"
            job = self._job_from_row(row)
            if job.state in {"completed", "failed"}:
                conn.execute("COMMIT")
                return job, "already_terminal"
            stages = list(job.stages)
            target = None
            for i, s in enumerate(stages):
                if int(s.get("index", -1)) == int(stage_index):
                    target = i
                    break
            if target is None:
                conn.execute("COMMIT")
                return job, "unknown_stage"
            stage = stages[target]
            if stage.get("state") == "completed":
                conn.execute("COMMIT")
                return job, "already_terminal"
            owner = stage.get("worker_address")
            if owner and owner != worker_addr:
                conn.execute("COMMIT")
                return job, "lost_lease"
            stages[target] = {
                **stage,
                "state": "completed",
                "worker_address": worker_addr,
                "completed_at": now,
                "output_b64": output_b64,
                "output_text": output_text,
                "meta": dict(meta or {}),
            }
            highest = max(int(s.get("index", 0)) for s in stages)
            non_terminal = bool((meta or {}).get("non_terminal"))
            is_final = int(stage_index) == highest and not non_terminal
            if is_final:
                conn.execute(
                    "UPDATE jobs SET state='completed', text=?, provider_id=?,"
                    " completed_at=?, winner_address=?, stages_json=? WHERE job_id=?",
                    (
                        output_text or job.text,
                        worker_addr,
                        now,
                        worker_addr,
                        _json.dumps(stages),
                        job_id,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET stages_json=? WHERE job_id=?",
                    (_json.dumps(stages), job_id),
                )
            conn.execute("COMMIT")
            return self.get(job_id), ("completed_job" if is_final else "completed_stage")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ---------- workers ----------

    def register_worker(self, info: _WorkerInfo) -> None:
        self._conn().execute(
            "INSERT INTO workers (address,tiers_json,hardware_json,"
            "registered_at,last_seen,jobs_completed,"
            "earnings_pending_animica,earnings_paid_animica,direct_endpoint) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(address) DO UPDATE SET "
            "  tiers_json=excluded.tiers_json,"
            "  hardware_json=excluded.hardware_json,"
            "  last_seen=excluded.last_seen,"
            "  direct_endpoint=excluded.direct_endpoint",
            (
                info.address,
                _json.dumps(info.tiers),
                _json.dumps(info.hardware),
                info.registered_at,
                info.last_seen,
                info.jobs_completed,
                info.earnings_pending_animica,
                info.earnings_paid_animica,
                info.direct_endpoint or "",
            ),
        )

    def get_worker(self, address: str) -> Optional[_WorkerInfo]:
        # Read-only: see the in-memory store — reads must not refresh last_seen.
        row = self._conn().execute(
            "SELECT * FROM workers WHERE address=?", (address,)
        ).fetchone()
        return self._worker_from_row(row) if row else None

    def touch_worker(self, address: str) -> None:
        # Throttled + best-effort: at most one DB write per worker per
        # _TOUCH_THROTTLE_S, and a locked/slow write never blocks the caller
        # (the claim path). Liveness granularity of tens of seconds is fine.
        if not _should_touch(address):
            return
        try:
            self._conn().execute(
                "UPDATE workers SET last_seen=? WHERE address=?", (time.time(), address)
            )
        except Exception:  # noqa: BLE001 — a liveness touch must never fail a claim
            pass

    def sign_off_worker(self, address: str) -> bool:
        """See the in-memory twin: offline everywhere, ledger untouched."""
        cur = self._conn().execute(
            "UPDATE workers SET last_seen=0, tiers_json='[\"offline\"]' WHERE address=?",
            (address,),
        )
        return bool(cur.rowcount)

    def all_workers(self) -> List[_WorkerInfo]:
        rows = self._conn().execute("SELECT * FROM workers").fetchall()
        return [self._worker_from_row(r) for r in rows]

    # ---------- earnings credit (atomic) ----------
    # The call sites do `w.jobs_completed += 1; w.earnings_pending_animica += ...`
    # but with SQLite, mutating an in-memory copy doesn't persist. We
    # expose a credit() helper used at the worker-credit call site.

    def credit_worker_completion(self, address: str, amount: float) -> None:
        conn = self._conn()
        with conn:
            conn.execute(
                "UPDATE workers SET jobs_completed=jobs_completed+1,"
                "earnings_pending_animica=earnings_pending_animica+? "
                "WHERE address=?",
                (float(amount or 0.0), address),
            )


def _make_store() -> Any:
    """Pick a job store at module load. Prefer SQLite when a data dir
    is configured; fall back to in-memory otherwise. Set
    ANIMICA_AICF_JOB_STORE=memory to force the in-memory path (useful
    in tests or when running on a read-only filesystem)."""
    forced = os.environ.get("ANIMICA_AICF_JOB_STORE", "").strip().lower()
    if forced == "memory":
        log.info("aicf_jobs: using in-memory store (forced)")
        return _AicfJobStore()
    path = _resolve_job_db_path()
    if path is None:
        log.info("aicf_jobs: using in-memory store (no data dir)")
        return _AicfJobStore()
    try:
        store = _SqliteAicfJobStore(path)
        log.info("aicf_jobs: using SQLite store at %s", path)
        return store
    except Exception as exc:
        log.warning(
            "aicf_jobs: SQLite store init failed (%s); falling back to memory", exc
        )
        return _AicfJobStore()


_STORE = _make_store()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_str(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _resolve_tier(requested: Optional[str]) -> str:
    if not requested:
        return _DEFAULT_TIER
    t = str(requested).lower().strip()
    return t if t in _TIER_PRICE_PER_KTOK else _DEFAULT_TIER


def _price_job(prompt_tokens: int, max_output_tokens: int, tier: str) -> float:
    rate = _TIER_PRICE_PER_KTOK.get(tier, _TIER_PRICE_PER_KTOK[_DEFAULT_TIER])
    total_tokens = max(0, prompt_tokens) + max(0, max_output_tokens)
    return round((total_tokens / 1000.0) * rate, 9)


def _decode_payment_envelope(blob: Any) -> Optional[Dict[str, Any]]:
    """Decode the txn_hex hex string from a SignedPayment blob into the
    canonical {sig, body} dict. Returns None when the input doesn't look
    like a signed-tx envelope (so callers can fall back to "no settlement")."""
    if not isinstance(blob, Mapping):
        return None
    txn_hex = str(blob.get("txn_hex") or blob.get("txnHex") or "").strip()
    if not txn_hex:
        return None
    raw = txn_hex
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    try:
        raw_bytes = bytes.fromhex(raw)
    except ValueError:
        return None
    try:
        import cbor2  # type: ignore
        decoded = cbor2.loads(raw_bytes)
    except Exception:
        return None
    if not isinstance(decoded, Mapping):
        return None
    return {"raw_bytes": raw_bytes, "decoded": dict(decoded), "txn_hex": "0x" + raw}


async def _broadcast_payment(raw_hex: str) -> Dict[str, Any]:
    """Submit the payment txn to the local mempool via the existing
    tx.sendRawTransaction dispatcher. Returns {accepted, tx_hash, status, reason}."""
    try:
        from rpc.methods import dispatch
    except Exception as exc:
        return {"accepted": False, "tx_hash": None, "status": "dispatcher_unavailable", "reason": str(exc)}
    try:
        result = await dispatch("tx.sendRawTransaction", {"rawTx": raw_hex})
    except Exception as exc:
        return {"accepted": False, "tx_hash": None, "status": "submit_failed", "reason": str(exc)}
    # tx.sendRawTransaction returns either a plain hex tx_hash string or a
    # structured dict (when it wants to flag warnings / status).
    if isinstance(result, str):
        return {"accepted": True, "tx_hash": result, "status": "accepted", "reason": None}
    if isinstance(result, Mapping):
        tx_hash = result.get("tx_hash") or result.get("hash")
        accepted = bool(result.get("accepted_to_mempool") or result.get("persisted_to_chain"))
        return {
            "accepted": accepted,
            "tx_hash": tx_hash,
            "status": result.get("status") or ("accepted" if accepted else "rejected"),
            "reason": result.get("reason"),
        }
    return {"accepted": False, "tx_hash": None, "status": "unexpected_result", "reason": repr(result)[:200]}


def _stub_response(prompt: str, tier: str) -> str:
    return (
        f"[distributed-aicf stub @ tier={tier}] No external workers have "
        f"claimed this job; the node returned this placeholder so the "
        f"protocol round-trip completes. The original prompt was: "
        f"{prompt[:240]}{'…' if len(prompt) > 240 else ''}"
    )


async def _local_fallback_after_grace(job_id: str) -> None:
    """Stub-complete a job whose worker never came back, so chat doesn't hang.

    Two cases the chat user perceives identically as "no response":

    1. No worker claimed the job (state stays "pending"). Stub immediately
       after the grace window — no provider is coming.
    2. A worker claimed it (state -> "claimed") but never sent a result —
       worker disconnected, errored mid-inference, or got wedged loading a
       model. The lease default is _WORKER_LEASE_S (10 minutes); without
       this branch, chat is unresponsive that whole time. We give the
       worker one more grace window after claim, then ship the stub
       anyway.
    """
    await asyncio.sleep(_WORKER_CLAIM_GRACE_S)
    job = _STORE.get(job_id)
    if job is None or job.state in {"completed", "failed"}:
        return
    if job.state == "claimed":
        # Give the worker one more grace window to actually return a result
        # before giving up — small chat completions normally finish well
        # inside this; a worker that hasn't is wedged.
        await asyncio.sleep(_WORKER_CLAIM_GRACE_S)
        job = _STORE.get(job_id)
        if job is None or job.state in {"completed", "failed"}:
            return
    prompt = str(job.spec.get("prompt", ""))
    text = _stub_response(prompt, job.tier)
    prior_state = job.state
    _STORE.complete(job_id, text=text, provider_id="local-stub")
    log.info(
        "aicf_jobs: local-stub completed job_id=%s tier=%s prior_state=%s",
        job_id, job.tier, prior_state,
    )


_FALLBACK_TASKS: set = set()


def _schedule_fallback(job_id: str) -> None:
    """Schedule the local fallback in whatever event loop is running.
    Falls back to a thread if no loop is reachable from this thread.

    The created task is held in a module-level set until it completes,
    so the asyncio gc doesn't emit "Task was destroyed but it is
    pending" warnings when the loop tears down (which happens in
    tests where _WORKER_CLAIM_GRACE_S is set high to disable the
    stub). Production loops never tear down — the set stays small.
    """
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_local_fallback_after_grace(job_id))
        _FALLBACK_TASKS.add(task)
        task.add_done_callback(_FALLBACK_TASKS.discard)
        return
    except RuntimeError:
        pass

    def _run() -> None:
        time.sleep(_WORKER_CLAIM_GRACE_S)
        job = _STORE.get(job_id)
        if job is None or job.state in {"completed", "failed"}:
            return
        if job.state == "claimed":
            time.sleep(_WORKER_CLAIM_GRACE_S)
            job = _STORE.get(job_id)
            if job is None or job.state in {"completed", "failed"}:
                return
        prompt = str(job.spec.get("prompt", ""))
        text = _stub_response(prompt, job.tier)
        prior_state = job.state
        _STORE.complete(job_id, text=text, provider_id="local-stub")
        log.info(
            "aicf_jobs: local-stub completed (thread) job_id=%s tier=%s prior_state=%s",
            job_id, job.tier, prior_state,
        )

    threading.Thread(target=_run, daemon=True, name=f"aicf-stub-{job_id[:8]}").start()


# ---------------------------------------------------------------------------
# Client-facing methods (estimate / submit / stream / status / settle)
# ---------------------------------------------------------------------------


@method("aicf.estimateJobCost", desc="Estimate cost of a distributed-AICF job")
async def estimate_job_cost(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    prompt_tokens = _coerce_int(p.get("prompt_tokens"), 0)
    max_output_tokens = _coerce_int(p.get("max_output_tokens"), 256)
    tier = _resolve_tier(p.get("tier_preferred"))
    cost = _price_job(prompt_tokens, max_output_tokens, tier)
    providers = len([w for w in _STORE.all_workers() if not w.tiers or tier in w.tiers])
    return {
        "cost_animica": cost,
        "latency_ms": _TIER_LATENCY_MS.get(tier, _TIER_LATENCY_MS[_DEFAULT_TIER]),
        "tier": tier,
        "providers": providers,
        "schedule": "in-memory-defaults",
    }


@method("aicf.submitInferenceJob", desc="Submit a distributed-AICF inference job")
async def submit_inference_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    spec = p.get("spec")
    payment = p.get("payment")
    if not isinstance(spec, Mapping):
        raise InvalidParams("submitInferenceJob: 'spec' must be an object")
    if not isinstance(payment, Mapping):
        raise InvalidParams("submitInferenceJob: 'payment' must be an object")
    prompt = _coerce_str(spec.get("prompt"))
    if not prompt:
        raise InvalidParams("submitInferenceJob: spec.prompt is required")

    tier = _resolve_tier(spec.get("tier_preferred"))
    max_out = _coerce_int(spec.get("max_output_tokens"), 256)
    prompt_tokens = max(1, len(prompt) // 4)  # rough token estimate
    cost = _price_job(prompt_tokens, max_out, tier)

    job_id = "0x" + uuid.uuid4().hex
    # Per-job replicas override: caller can pass spec.replicas to ask
    # for narrower or wider racing than the node default (e.g. elite
    # tier might want 1, debugging might want a single replica). Bound
    # to [1, 16] so a misconfig can't fan out forever.
    requested_replicas = spec.get("replicas") if isinstance(spec, Mapping) else None
    try:
        replicas = int(requested_replicas) if requested_replicas is not None else _REPLICAS_DEFAULT
    except (TypeError, ValueError):
        replicas = _REPLICAS_DEFAULT
    replicas = max(1, min(16, replicas))
    # Pipeline mode selection. With "auto" (the default) we promote to
    # pipeline only when there are enough pipeline-tier workers to fill
    # every stage; otherwise we fall back to race so chat never hangs
    # waiting for stage workers that aren't registered. "race" forces
    # the legacy path, "pipeline" forces it regardless of worker count
    # (callers that pass "pipeline" without enough workers get stub
    # results from the fallback timer — same as the race-without-miners
    # path).
    requested_stages = spec.get("stages") if isinstance(spec, Mapping) else None
    try:
        stages_count = int(requested_stages) if requested_stages is not None else 0
    except (TypeError, ValueError):
        stages_count = 0
    mode, resolved_stages = _resolve_pipeline_mode(dict(spec), stages_count)
    rec = _JobRecord(
        job_id=job_id,
        spec=dict(spec),
        payment=dict(payment),
        estimated_cost=cost,
        tier=tier,
        replicas_wanted=replicas,
        mode=mode,
        stages=(_build_initial_stages(resolved_stages) if mode == "pipeline" else []),
    )

    # Real on-chain settlement: decode the SignedPayment envelope and
    # broadcast the underlying tx via tx.sendRawTransaction. This moves
    # value from the user's wallet into the AICF treasury on the next
    # mined block. The body's `to` field must already match the
    # treasury address — we don't rewrite it server-side.
    decoded = _decode_payment_envelope(payment)
    if decoded is not None:
        broadcast = await _broadcast_payment(decoded["txn_hex"])
        rec.payment_tx_hash = broadcast.get("tx_hash")
        rec.payment_accepted = bool(broadcast.get("accepted"))
        rec.payment_status = broadcast.get("status")
        try:
            body = decoded["decoded"].get("body") or {}
            chain_field = body.get("chainId")
            if isinstance(chain_field, int):
                rec.settled_chain_id = chain_field
        except Exception:
            pass
        if not rec.payment_accepted and _require_settlement():
            reason = broadcast.get("reason") or broadcast.get("status") or "unknown"
            raise InvalidParams(
                f"submitInferenceJob: payment rejected by mempool: {reason}"
            )
        # Surface the rejection reason — historically only status was
        # logged, which hid the actual mempool error and let payments
        # silently fail for every chat turn. When acceptance is False
        # we want the cause front-and-center so debit regressions are
        # caught immediately.
        log_fn = log.warning if not rec.payment_accepted else log.info
        log_fn(
            "aicf_jobs: payment broadcast accepted=%s tx_hash=%s status=%s reason=%s",
            rec.payment_accepted,
            rec.payment_tx_hash,
            rec.payment_status,
            broadcast.get("reason"),
        )
    elif _require_settlement():
        raise InvalidParams(
            "submitInferenceJob: payment envelope could not be decoded; "
            "settlement is required by ANIMICA_AICF_REQUIRE_SETTLEMENT"
        )
    else:
        log.info(
            "aicf_jobs: payment envelope not decoded; accepting without "
            "on-chain settlement (set ANIMICA_AICF_REQUIRE_SETTLEMENT=1 to "
            "make this a hard error)"
        )

    _STORE.submit(rec)
    _schedule_fallback(job_id)
    log.info(
        "aicf_jobs: submitted job_id=%s tier=%s mode=%s stages=%d est_cost=%.9f",
        job_id, tier, rec.mode, len(rec.stages), cost,
    )
    return {
        "job_id": job_id,
        "accepted_tier": tier,
        "provider_id": "",   # filled in on completion
        "estimated_cost_animica": cost,
        "payment_tx_hash": rec.payment_tx_hash,
        "payment_accepted": rec.payment_accepted,
        "payment_status": rec.payment_status,
        "mode": rec.mode,
        "stages": len(rec.stages),
    }


@method("aicf.getTreasuryAddress", desc="Get the on-chain AICF treasury address for this node")
async def get_treasury_address(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    chain_id = _coerce_int((params or {}).get("chain_id"), 0)
    if chain_id <= 0:
        # Best-effort: pull from the RPC ctx if available.
        try:
            chain_id = int(getattr(ctx, "chain_id", 0) or 0)
        except Exception:
            chain_id = 0
    if chain_id <= 0:
        chain_id = 1
    addr = _treasury_address_for(chain_id)
    return {
        "treasury_address": addr,
        "chain_id": chain_id,
        "source": "env" if os.environ.get(_TREASURY_ENV) else "default",
        "require_settlement": _require_settlement(),
    }


@method("aicf.streamJob", desc="Stream chunks for an in-flight AICF job")
async def stream_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    cursor = _coerce_int(p.get("cursor"), 0)
    if not job_id:
        raise InvalidParams("streamJob: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"streamJob: unknown job_id {job_id}")

    # Long-ish poll: wait up to ~1.5s for new text or completion.
    deadline = time.time() + 1.5
    while time.time() < deadline:
        if job.state in {"completed", "failed"}:
            break
        if len(job.text) > cursor:
            break
        await asyncio.sleep(0.05)
        job = _STORE.get(job_id) or job

    chunk_text = job.text[cursor:] if len(job.text) > cursor else ""
    is_final = job.state in {"completed", "failed"} and (cursor + len(chunk_text)) >= len(job.text)
    return {
        "text": chunk_text,
        "final": is_final,
        "next_cursor": cursor + len(chunk_text),
        "token_count": max(1, len(chunk_text) // 4) if chunk_text else 0,
        "state": job.state,
    }


@method("aicf.jobStatus", desc="Get the current status of an AICF job")
async def job_status(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    if not job_id:
        raise InvalidParams("jobStatus: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"jobStatus: unknown job_id {job_id}")
    return {
        "job_id": job.job_id,
        "state": "running" if job.state in {"pending", "claimed"} else job.state,
        "text": job.text,
        "tier": job.tier,
        "provider_id": job.provider_id,
        "error": job.error,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
        "payment_tx_hash": job.payment_tx_hash,
        "payment_accepted": job.payment_accepted,
        "payment_status": job.payment_status,
    }


@method("aicf.settleJob", desc="Settle and return final result for a completed AICF job")
async def settle_job(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id"))
    if not job_id:
        raise InvalidParams("settleJob: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"settleJob: unknown job_id {job_id}")
    if job.state not in {"completed", "failed"}:
        # Block briefly for completion (cap at ~3s).
        deadline = time.time() + 3.0
        while time.time() < deadline and job.state not in {"completed", "failed"}:
            await asyncio.sleep(0.05)
            job = _STORE.get(job_id) or job
    latency_ms = 0
    if job.completed_at and job.created_at:
        latency_ms = int(max(0.0, (job.completed_at - job.created_at) * 1000))
    return {
        "text": job.text,
        "cost_animica": job.estimated_cost,
        "provider_id": job.provider_id or "local-stub",
        "settled": job.state == "completed",
        "latency_ms": latency_ms,
        "state": job.state,
        "error": job.error,
        "payment_tx_hash": job.payment_tx_hash,
        "payment_accepted": job.payment_accepted,
        "payment_status": job.payment_status,
    }


# ---------------------------------------------------------------------------
# Worker-facing methods
# ---------------------------------------------------------------------------


@method("aicf.workerRegister", desc="Register a worker to claim AICF jobs", aliases=("aicf_workerRegister",))
async def worker_register(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerRegister: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerRegister: 'tiers' must be a list")
    # Preserve the special "pipeline" capability label so pipeline-mode
    # routing can find workers; pricing tiers get normalized through
    # _resolve_tier so a misconfigured string doesn't show up as a
    # zero-cost free tier.
    tiers: List[str] = []
    for t in tiers_raw:
        label = str(t).lower().strip()
        if label == _PIPELINE_TIER_LABEL:
            tiers.append(_PIPELINE_TIER_LABEL)
        else:
            tiers.append(_resolve_tier(t))
    hardware = p.get("hardware") or {}
    if not isinstance(hardware, Mapping):
        raise InvalidParams("workerRegister: 'hardware' must be an object")
    # Optional direct-transport endpoint. Only used by pipeline-mode
    # downstream stages to fetch upstream activations without a node
    # round-trip. Workers that don't expose a reachable port omit it
    # and the node-proxy fallback path serves them.
    direct_endpoint = _coerce_str(p.get("direct_endpoint")).strip()
    if direct_endpoint and not (
        direct_endpoint.startswith("http://")
        or direct_endpoint.startswith("https://")
    ):
        raise InvalidParams(
            "workerRegister: 'direct_endpoint' must be an http(s) URL"
        )
    info = _WorkerInfo(
        address=address, tiers=tiers, hardware=dict(hardware),
        direct_endpoint=direct_endpoint,
    )
    _STORE.register_worker(info)
    log.info(
        "aicf_jobs: worker registered address=%s tiers=%s direct=%s",
        address, tiers, bool(direct_endpoint),
    )
    return {
        "registered": True,
        "address": address,
        "tiers": tiers,
        "direct_endpoint": direct_endpoint,
    }


@method(
    "aicf.workerSignOff",
    desc="Worker signs off (tab closed / ctrl-C): removed from online views, earnings ledger kept",
    aliases=("aicf_workerSignOff", "aicf.workerUnregister"),
)
async def worker_sign_off(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerSignOff: 'address' is required")
    ok = bool(getattr(_STORE, "sign_off_worker", lambda a: False)(address))
    return {"signed_off": ok, "address": address}


@method("aicf.workerStatus", desc="Get status of a registered AICF worker", aliases=("aicf_workerStatus",))
async def worker_status(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerStatus: 'address' is required")
    w = _STORE.get_worker(address)
    if w is None:
        return {"registered": False, "address": address}
    return {
        "registered": True,
        "address": w.address,
        "tiers": list(w.tiers),
        "hardware": dict(w.hardware),
        "registered_at": w.registered_at,
        "last_seen": w.last_seen,
        "jobs_completed": w.jobs_completed,
        "earnings_pending_animica": w.earnings_pending_animica,
        "earnings_paid_animica": w.earnings_paid_animica,
        "direct_endpoint": w.direct_endpoint or "",
    }


@method(
    "aicf.workerEarnings",
    desc="Get pending and paid AICF earnings for a worker address",
    aliases=("aicf_workerEarnings",),
)
async def worker_earnings(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Per-worker earnings ledger.

    ``earnings_pending_animica`` accumulates each completed-job cost; it
    represents value the worker has earned but which has not been swept
    from the on-chain treasury into the AICF state pool yet. That sweep
    is a future consensus-level operation — when wired, the pending
    amount becomes claimable via the existing aicf.claim / aicf.getClaimable
    flow.

    Until then, this ledger is the canonical reference for "what does
    the network owe me." Persistence is in-memory and resets on node
    restart, just like the rest of the AICF job queue.
    """
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerEarnings: 'address' is required")
    w = _STORE.get_worker(address)
    if w is None:
        return {
            "registered": False,
            "address": address,
            "earnings_pending_animica": 0.0,
            "earnings_paid_animica": 0.0,
            "jobs_completed": 0,
            "note": "settlement_pending_consensus_upgrade",
        }
    return {
        "registered": True,
        "address": w.address,
        "jobs_completed": w.jobs_completed,
        "earnings_pending_animica": w.earnings_pending_animica,
        "earnings_paid_animica": w.earnings_paid_animica,
        "note": "settlement_pending_consensus_upgrade",
    }


@method(
    "aicf.workerCount",
    desc="Aggregate stats over registered AICF workers (no addresses enumerated)",
    aliases=("aicf_workerCount",),
)
async def worker_count(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Public, aggregate-only view of the worker fleet for dashboards
    (pool.animica.org/stats shows "phones serving" from this). Buckets by the
    self-reported hardware.engine: "webllm" = the browser Serve&Earn page
    (phones + desktops), "animica-serve" = the Termux/CLI thin worker (its
    hardware.termux flag separates actual phones), everything else = full
    nodes (animica up). No addresses are enumerated — per-address data stays
    behind aicf.workerStatus(address), which requires knowing the address."""
    p = dict(params or {})
    online_window = float(p.get("online_window_s") or 300.0)
    online_window = max(30.0, min(online_window, 86400.0))
    now = time.time()
    workers = _STORE.all_workers()
    total = len(workers)
    online = 0
    engines: Dict[str, int] = {}
    engines_online: Dict[str, int] = {}
    phones_online = 0
    jobs_completed = 0
    pending = 0.0
    for w in workers:
        hw = w.hardware if isinstance(w.hardware, Mapping) else {}
        eng = str(hw.get("engine") or "node").lower()[:32]
        is_online = (now - float(w.last_seen or 0)) <= online_window
        engines[eng] = engines.get(eng, 0) + 1
        if is_online:
            online += 1
            engines_online[eng] = engines_online.get(eng, 0) + 1
            if eng in ("webllm", "wllama") or (eng == "animica-serve" and hw.get("termux")):
                phones_online += 1
        jobs_completed += int(w.jobs_completed or 0)
        pending += float(w.earnings_pending_animica or 0.0)
    return {
        "total_registered": total,
        "online": online,
        "online_window_s": online_window,
        "phones_online": phones_online,
        "engines": engines,
        "engines_online": engines_online,
        "jobs_completed_total": jobs_completed,
        "earnings_pending_animica_total": round(pending, 9),
        "ts": now,
    }


@method("aicf.workerClaimNextJob", desc="Claim the next pending AICF job", aliases=("aicf_workerClaimNextJob",))
async def worker_claim_next_job(
    ctx: Any = None,
    **params: Any,
) -> Optional[Dict[str, Any]]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerClaimNextJob: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerClaimNextJob: 'tiers' must be a list")
    tiers = [_resolve_tier(t) for t in tiers_raw]
    # A worker polling for work is the honest liveness signal (get_worker no
    # longer refreshes last_seen on reads).
    _STORE.touch_worker(address)
    # claim_next walks every job in tier filter — pipeline jobs already
    # have mode != "race" so they're skipped here naturally (their stages
    # are surfaced via pipelineClaimStage). Belt-and-braces: refuse to
    # hand a pipeline job to a race-mode worker.
    job = _STORE.claim_next(address, tiers)
    if job is not None and getattr(job, "mode", "race") == "pipeline":
        job = None
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "spec": dict(job.spec),
        "tier": job.tier,
        # Flatten prompt + sampling to the TOP LEVEL too: the single-worker chat
        # path reads job["prompt"]/max_output_tokens/temperature/top_p top-level
        # (only the pipeline path reads job["spec"][...]). Without these the
        # worker passed an EMPTY prompt and every answer was "I didn't receive a
        # question". Additive + backward-compatible.
        "prompt": job.spec.get("prompt", ""),
        "max_output_tokens": job.spec.get("max_output_tokens", 512),
        "temperature": job.spec.get("temperature", 0.2),
        "top_p": job.spec.get("top_p", 0.95),
        "estimated_cost_animica": job.estimated_cost,
        "claim_expires_at": job.claim_expires_at,
    }


@method("aicf.workerSubmitResult", desc="Submit the result of a claimed AICF job", aliases=("aicf_workerSubmitResult",))
async def worker_submit_result(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    job_id = _coerce_str(p.get("job_id")).strip()
    text = _coerce_str(p.get("text"))
    if not address or not job_id:
        raise InvalidParams("workerSubmitResult: 'address' and 'job_id' are required")
    _STORE.touch_worker(address)
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"workerSubmitResult: unknown job_id {job_id}")
    if job.mode == "pipeline":
        # Pipeline jobs are settled via aicf.pipelineSubmitStageResult.
        # Accepting a race-style submit here would skip the stage
        # chaining and silently produce a one-worker answer instead of
        # the model-parallel composition.
        return {
            "accepted": False,
            "state": job.state,
            "reason": "use_pipeline_methods",
            "job_id": job_id,
            "hint": "call aicf.pipelineClaimStage + aicf.pipelineSubmitStageResult",
        }
    if job.state not in {"claimed", "pending"}:
        # Already completed (e.g. local stub raced ahead, or another
        # worker won the race) — accept idempotently. With K-way race
        # replication this is the dominant non-winner outcome; surface
        # it so the miner side can distinguish "I lost the race" from
        # "I won and was credited."
        winner = job.winner_address or job.provider_id or ""
        return {
            "accepted": False,
            "state": job.state,
            "reason": "lost_race" if winner and winner != address else "already_terminal",
            "winner_address": winner,
            "job_id": job_id,
        }
    # The legacy single-owner mismatch warning is now noisy under K-way
    # replication where every replica's address differs from claim_owner.
    # Keep the late-submit log only when the submitter isn't in the
    # active claims list at all (truly unsolicited submit).
    claim_addrs = {c.get("address") for c in (job.claims or [])}
    if claim_addrs and address not in claim_addrs:
        log.info(
            "aicf_jobs: accepting unsolicited submit for job %s from %s "
            "(active claimants: %s)",
            job_id, address, sorted(a for a in claim_addrs if a),
        )
    completed = _STORE.complete(job_id, text=text, provider_id=address)
    if completed is None:
        # Lost the race between read-state and complete: someone else
        # transitioned the job to terminal in between. Do NOT credit;
        # surface the same lost_race reply as the early branch above.
        latest = _STORE.get(job_id)
        winner = (latest.winner_address or latest.provider_id) if latest else ""
        return {
            "accepted": False,
            "state": latest.state if latest else "unknown",
            "reason": "lost_race" if winner and winner != address else "already_terminal",
            "winner_address": winner or "",
            "job_id": job_id,
        }
    # Credit ONLY the winner for the full estimated cost of the job. With
    # the SQLite store, in-memory dataclass mutation doesn't persist — use
    # the atomic credit helper when available so the IOU survives a node
    # restart. With the in-memory store, fall back to direct field
    # mutation. See aicf.workerEarnings for retrieval; once the
    # treasury→state-pool sweep activates (consensus rule), these IOUs
    # become claimable via aicf.claim.
    amount = float(completed.estimated_cost or 0.0)
    if hasattr(_STORE, "credit_worker_completion"):
        try:
            _STORE.credit_worker_completion(address, amount)
        except Exception as exc:
            log.warning("aicf_jobs: credit_worker_completion failed: %s", exc)
    else:
        w = _STORE.get_worker(address)
        if w is not None:
            w.jobs_completed += 1
            try:
                w.earnings_pending_animica += amount
            except (TypeError, ValueError):
                pass
    return {"accepted": True, "state": "completed", "job_id": job_id,
            "winner_address": address}


# ---------------------------------------------------------------------------
# Pipeline-parallel methods
# ---------------------------------------------------------------------------
#
# The pipeline path chains N worker stages to produce one chat answer.
# Each stage owns a transform (in the reference runner: identity + a
# stage marker; in a real layer-sharded miner: a contiguous layer range
# of the underlying model). Stage 0 reads the original prompt; stage k
# pulls the upstream activation from stage k-1. The final stage's
# `output_text` becomes the job's text and triggers settlement.
#
# Wire protocol summary:
#   1. Submit creates a pipeline job with N pending stages.
#   2. Workers (registered with tier=="pipeline") poll
#      aicf.pipelineClaimStage. The store hands out the lowest pending
#      stage and refuses to give the same worker a second stage of the
#      same job (would deadlock self-dependency).
#   3. Stage k worker calls aicf.pipelineGetUpstreamActivation with
#      stage_index=k. The node long-polls (~1.5s) until stage k-1 has
#      written its output, then returns it. Stage 0 gets no upstream
#      payload, just the prompt from claimStage.
#   4. Worker computes its transform on the upstream activation, then
#      calls aicf.pipelineSubmitStageResult with the new activation
#      (and `output_text` only on the final stage).
#   5. When the final stage submits, the wrapping _JobRecord transitions
#      to "completed" and aicf.streamJob/settleJob deliver the text to
#      the chat client unchanged.
#
# What's intentionally NOT in scope here (and noted for the follow-up
# work that turns this into "actually faster than running on one
# worker"):
#   - Sharded model bundles. Today's bundles are monolithic; layer-range
#     loading is a separate bundle-format effort.
#   - Direct worker-to-worker activation transport. Activations are
#     currently proxied through the node, which adds a round-trip per
#     stage. Fine for kbyte hidden states; bottleneck for MB ones.
#   - Multi-party payment split. The current settlement credits the
#     final-stage worker; cross-stage revenue share is a future TODO.
#   - Mid-job stage worker replacement / partial re-execution.


def _pipeline_capable_workers() -> int:
    """Count of registered workers advertising the 'pipeline' tier."""
    try:
        workers = _STORE.all_workers()
    except Exception:
        return 0
    return sum(1 for w in workers if _PIPELINE_TIER_LABEL in (w.tiers or []))


def _build_initial_stages(stage_count: int) -> List[Dict[str, Any]]:
    return [
        {
            "index": i,
            "state": "pending",
            "worker_address": None,
            "claimed_at": None,
            "expires_at": None,
            "completed_at": None,
            "output_b64": None,
            "output_text": None,
            "meta": {},
        }
        for i in range(int(stage_count))
    ]


def _credit_pipeline_stages(job: _JobRecord) -> None:
    """Distribute the job's cost across every completed stage worker.

    Default split rule: equal share. Each completed stage's worker
    receives ``cost / num_completed_stages``. Duplicate stages (the same
    worker held two stages of the same job — disallowed in the claim
    path but defensive here too) get a single share per distinct
    address. Any credit error is logged and skipped — the chat user
    has already received the answer at this point, so we never want a
    ledger hiccup to mark the job failed.
    """
    completed_stages = [
        s for s in (job.stages or [])
        if s.get("state") == "completed" and s.get("worker_address")
    ]
    if not completed_stages:
        return
    # Deduplicate by address while preserving order so any future split
    # rule that weights by stage_index still sees a stable iteration.
    distinct_workers: List[str] = []
    seen: set[str] = set()
    for s in completed_stages:
        addr = str(s.get("worker_address") or "")
        if addr and addr not in seen:
            distinct_workers.append(addr)
            seen.add(addr)
    if not distinct_workers:
        return
    total = float(job.estimated_cost or 0.0)
    if total <= 0.0:
        return
    share = round(total / len(distinct_workers), 9)
    for addr in distinct_workers:
        if hasattr(_STORE, "credit_worker_completion"):
            try:
                _STORE.credit_worker_completion(addr, share)
                continue
            except Exception as exc:
                log.warning(
                    "aicf_jobs: pipeline credit_worker_completion(%s, %.9f) "
                    "failed: %s",
                    addr, share, exc,
                )
        # Fallback for in-memory store / non-atomic path.
        w = _STORE.get_worker(addr)
        if w is not None:
            w.jobs_completed += 1
            try:
                w.earnings_pending_animica += share
            except (TypeError, ValueError):
                pass


def _resolve_pipeline_mode(
    spec: Mapping[str, Any],
    requested_stages: int,
) -> tuple[str, int]:
    """Decide whether a newly-submitted job runs as race or pipeline.

    Returns ``(mode, stages)``. Mode is one of "race" | "pipeline".
    Callers honour spec.mode == "race"/"pipeline" verbatim; with
    spec.mode == "auto" (the default) we pick pipeline only when there
    are enough pipeline-tier workers to fill every stage. Anything else
    falls back to race so chat never hangs waiting for missing stages.
    """
    requested_mode = str(spec.get("mode") or "auto").lower().strip()
    if not _PIPELINE_ENABLED:
        return "race", 0
    if requested_mode == "race":
        return "race", 0
    target_stages = max(2, int(requested_stages or _PIPELINE_STAGES_DEFAULT))
    if requested_mode == "pipeline":
        return "pipeline", target_stages
    # "auto" — default. Promote to pipeline only when feasible.
    if _pipeline_capable_workers() >= target_stages:
        return "pipeline", target_stages
    return "race", 0


@method(
    "aicf.pipelineClaimStage",
    desc="Claim the next available pipeline stage for a worker",
    aliases=("aicf_pipelineClaimStage",),
)
async def pipeline_claim_stage(
    ctx: Any = None,
    **params: Any,
) -> Optional[Dict[str, Any]]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("pipelineClaimStage: 'address' is required")
    job_id_filter = _coerce_str(p.get("job_id")).strip() or None
    res = _STORE.claim_pipeline_stage(address, job_id_filter=job_id_filter)
    if res is None:
        return None
    job, stage_index = res
    prompt = str(job.spec.get("prompt", ""))
    return {
        "job_id": job.job_id,
        "stage_index": stage_index,
        "total_stages": len(job.stages),
        "prompt": prompt,
        "upstream_stage": (stage_index - 1) if stage_index > 0 else None,
        "is_final_stage": stage_index == (len(job.stages) - 1),
        "tier": job.tier,
        "lease_expires_at": next(
            (s.get("expires_at") for s in job.stages
             if int(s.get("index", -1)) == stage_index),
            None,
        ),
        "spec": dict(job.spec),
    }


@method(
    "aicf.pipelineSubmitStageResult",
    desc="Submit a worker's pipeline-stage output",
    aliases=("aicf_pipelineSubmitStageResult",),
)
async def pipeline_submit_stage_result(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    job_id = _coerce_str(p.get("job_id")).strip()
    stage_index = _coerce_int(p.get("stage_index"), -1)
    if not address or not job_id or stage_index < 0:
        raise InvalidParams(
            "pipelineSubmitStageResult: 'address', 'job_id', 'stage_index' required"
        )
    output_b64 = p.get("output_b64")
    output_text = p.get("output_text")
    meta = p.get("meta") or {}
    if not isinstance(meta, Mapping):
        meta = {}
    job, status = _STORE.submit_pipeline_stage(
        job_id,
        stage_index,
        worker_addr=address,
        output_b64=(str(output_b64) if output_b64 is not None else None),
        output_text=(str(output_text) if output_text is not None else None),
        meta=dict(meta),
    )
    accepted = status in {"completed_stage", "completed_job"}
    # Multi-party revenue split on pipeline completion. When the final
    # stage closes the job we walk every completed stage and credit its
    # worker their share of the job cost. The default split is equal
    # (cost / num_completed_stages); operators can plug a different
    # split rule via ANIMICA_AICF_PIPELINE_SPLIT in a future iteration
    # (e.g. weighted by latency, layer count, or compute cycles). The
    # ledger entries here are still off-chain IOUs; the treasury sweep
    # that turns them into on-chain payouts is the same future consensus
    # upgrade that race-mode earnings depend on.
    if status == "completed_job" and job is not None:
        _credit_pipeline_stages(job)
    return {
        "accepted": accepted,
        "status": status,
        "job_id": job_id,
        "stage_index": stage_index,
        "job_state": job.state if job else None,
        "completed_stages": (
            [int(s.get("index", -1)) for s in (job.stages if job else [])
             if s.get("state") == "completed"]
        ),
    }


@method(
    "aicf.pipelineGetUpstreamActivation",
    desc="Long-poll for the upstream stage's activation",
    aliases=("aicf_pipelineGetUpstreamActivation",),
)
async def pipeline_get_upstream_activation(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    stage_index = _coerce_int(p.get("stage_index"), -1)
    if not job_id or stage_index < 0:
        raise InvalidParams(
            "pipelineGetUpstreamActivation: 'job_id' and 'stage_index' required"
        )
    if stage_index == 0:
        return {"ready": True, "output_b64": None, "output_text": None,
                "meta": {}, "stage_index": -1, "note": "stage_0_has_no_upstream"}
    upstream = stage_index - 1
    deadline = time.time() + 1.5
    while time.time() < deadline:
        job = _STORE.get(job_id)
        if job is None:
            raise InvalidParams(f"pipelineGetUpstreamActivation: unknown job_id {job_id}")
        if job.state in {"completed", "failed"} and not any(
            int(s.get("index", -1)) == upstream and s.get("state") == "completed"
            for s in job.stages
        ):
            # Job died with the upstream still pending — propagate.
            return {
                "ready": False,
                "stage_index": upstream,
                "job_state": job.state,
                "note": "job_terminal_before_upstream_ready",
            }
        stage = next(
            (s for s in job.stages
             if int(s.get("index", -1)) == upstream),
            None,
        )
        if stage and stage.get("state") == "completed":
            return {
                "ready": True,
                "stage_index": upstream,
                "output_b64": stage.get("output_b64"),
                "output_text": stage.get("output_text"),
                "meta": dict(stage.get("meta") or {}),
                "completed_at": stage.get("completed_at"),
            }
        await asyncio.sleep(0.05)
    return {"ready": False, "stage_index": upstream, "note": "timeout_keep_polling"}


@method(
    "aicf.pipelineJobStatus",
    desc="Get full per-stage status for a pipeline job",
    aliases=("aicf_pipelineJobStatus",),
)
async def pipeline_job_status(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    if not job_id:
        raise InvalidParams("pipelineJobStatus: 'job_id' is required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"pipelineJobStatus: unknown job_id {job_id}")
    return {
        "job_id": job.job_id,
        "mode": job.mode,
        "state": job.state,
        "total_stages": len(job.stages),
        "completed_stages": sum(
            1 for s in job.stages if s.get("state") == "completed"
        ),
        "stages": [
            {
                "index": int(s.get("index", -1)),
                "state": s.get("state"),
                "worker_address": s.get("worker_address"),
                "claimed_at": s.get("claimed_at"),
                "expires_at": s.get("expires_at"),
                "completed_at": s.get("completed_at"),
                "has_output": bool(s.get("output_b64") or s.get("output_text")),
            }
            for s in sorted(job.stages, key=lambda s: int(s.get("index", 0)))
        ],
        "text": job.text,
        "error": job.error,
    }


# --------------------------------------------------------------------------- #
# Streaming-decode protocol                                                   #
# --------------------------------------------------------------------------- #
#
# Once the prefill pass completes, generation continues as a chain of
# token-step rounds. For each new token T_k:
#   1. Stage N-1 samples T_k from its LM-head logits, appends T_k to
#      ``job.text`` (so streamJob delivers the character to the chat
#      client), then calls aicf.pipelineFeedToken(job_id, T_k).
#   2. The node enqueues T_k in ``job.decode_inbox``.
#   3. Stage 0 long-polls aicf.pipelineGetNextTokenStep, which dequeues
#      the oldest pending token. The stage embeds it, runs its layer
#      range with the locally-cached KV state, and submits the result
#      via aicf.pipelineSubmitDecodeStep with step_index=k+1.
#   4. Middle stages long-poll aicf.pipelineGetUpstreamStep for the
#      previous stage's step k+1 output, run their layers, submit
#      their own step k+1.
#   5. Stage N-1 picks up step k+1 from its upstream and the loop
#      repeats. When the final stage decides to stop (EOS or
#      max_new_tokens) it sets meta.is_terminal=True on its submit,
#      which closes the job.
#
# All four endpoints below long-poll for ~1.5s so the chat path runs
# at roughly 1 step per stage-RPC round-trip + the wall-clock cost of
# the layer compute, dominated by the direct-W2W transport when
# enabled.


@method(
    "aicf.pipelineGetStageInfo",
    desc="Resolve a pipeline stage's worker + direct endpoint for W2W transport",
    aliases=("aicf_pipelineGetStageInfo",),
)
async def pipeline_get_stage_info(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Resolve who owns a given pipeline stage and how to reach them
    directly. Downstream stages call this with the upstream stage's
    index to learn the upstream worker's HTTP endpoint, then fetch the
    activation bytes peer-to-peer. Falls back to the node-proxy path
    (aicf.pipelineGetUpstreamActivation) when the upstream worker
    didn't advertise a reachable endpoint at registration."""
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    stage_index = _coerce_int(p.get("stage_index"), -1)
    if not job_id or stage_index < 0:
        raise InvalidParams(
            "pipelineGetStageInfo: 'job_id' and 'stage_index' required"
        )
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"pipelineGetStageInfo: unknown job_id {job_id}")
    stage = next(
        (s for s in job.stages if int(s.get("index", -1)) == stage_index),
        None,
    )
    if stage is None:
        return {
            "found": False,
            "stage_index": stage_index,
            "reason": "unknown_stage",
        }
    worker = stage.get("worker_address") or ""
    direct = ""
    if worker:
        info = _STORE.get_worker(worker)
        if info is not None:
            direct = info.direct_endpoint or ""
    return {
        "found": True,
        "stage_index": stage_index,
        "worker_address": worker,
        "direct_endpoint": direct,
        "state": stage.get("state"),
        "completed_at": stage.get("completed_at"),
        "expires_at": stage.get("expires_at"),
        # Echo the chunk URL the upstream would expose for this job.
        # Workers MAY use the convention {direct_endpoint}/aicf/pipeline/
        # {job_id}/{stage_index} or whatever they advertise; we treat
        # `direct_endpoint` as the authoritative base URL.
        "chunk_path_hint": (
            f"/aicf/pipeline/{job_id}/{stage_index}" if direct else ""
        ),
    }


@method(
    "aicf.pipelineFeedToken",
    desc="Final stage pushes the next-token id back to stage 0 for the next decode step",
    aliases=("aicf_pipelineFeedToken",),
)
async def pipeline_feed_token(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    token_id = _coerce_int(p.get("token_id"), -1)
    if not job_id or token_id < 0:
        raise InvalidParams("pipelineFeedToken: 'job_id' and 'token_id' required")
    job = _STORE.push_decode_inbox(job_id, token_id)
    if job is None:
        raise InvalidParams(f"pipelineFeedToken: unknown job_id {job_id}")
    return {
        "accepted": job.state not in {"completed", "failed"},
        "job_state": job.state,
        "inbox_depth": len(job.decode_inbox),
    }


@method(
    "aicf.pipelineGetNextTokenStep",
    desc="Stage 0 polls for the next token to embed for the current decode round",
    aliases=("aicf_pipelineGetNextTokenStep",),
)
async def pipeline_get_next_token_step(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Long-poll for the next token to embed.

    Stage 0 calls this after submitting the prefill. The response
    carries a fresh monotonic step_index allocated atomically so two
    competing stage-0 workers couldn't accidentally emit the same
    step number even when the SQLite store is shared across pool
    processes.
    """
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    if not job_id:
        raise InvalidParams("pipelineGetNextTokenStep: 'job_id' required")
    deadline = time.time() + 1.5
    while time.time() < deadline:
        job, token = _STORE.pop_decode_inbox(job_id)
        if job is None:
            raise InvalidParams(f"pipelineGetNextTokenStep: unknown job_id {job_id}")
        if job.state in {"completed", "failed"}:
            return {
                "ready": False,
                "job_state": job.state,
                "note": "job_terminal",
            }
        if token is not None:
            step_index = _STORE.next_decode_step_index(job_id)
            return {
                "ready": True,
                "token_id": int(token),
                "step_index": int(step_index or 0),
                "job_state": job.state,
            }
        await asyncio.sleep(0.05)
    return {"ready": False, "note": "timeout_keep_polling"}


@method(
    "aicf.pipelineSubmitDecodeStep",
    desc="Append a decode-step output to a pipeline stage",
    aliases=("aicf_pipelineSubmitDecodeStep",),
)
async def pipeline_submit_decode_step(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Each pipeline stage uses this for every decode-loop iteration.

    Intermediate stages submit ``output_b64`` (the safetensors-encoded
    hidden state) and leave ``output_text`` empty. The final stage
    submits ``output_text`` (the decoded token text) plus
    ``meta.is_terminal=true`` on the last step to close the job. Any
    stage may include ``meta.next_token_id`` so the inbox push happens
    in the same round-trip; this saves the final stage one RPC per
    decoded token.
    """
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    job_id = _coerce_str(p.get("job_id")).strip()
    stage_index = _coerce_int(p.get("stage_index"), -1)
    step_index = _coerce_int(p.get("step_index"), -1)
    if not address or not job_id or stage_index < 0 or step_index < 0:
        raise InvalidParams(
            "pipelineSubmitDecodeStep: 'address', 'job_id', 'stage_index', 'step_index' required"
        )
    output_b64 = p.get("output_b64")
    output_text = p.get("output_text")
    meta = p.get("meta") or {}
    if not isinstance(meta, Mapping):
        meta = {}
    job, status = _STORE.append_decode_step(
        job_id, stage_index,
        worker_addr=address,
        step_index=step_index,
        output_b64=(str(output_b64) if output_b64 is not None else None),
        output_text=(str(output_text) if output_text is not None else None),
        meta=dict(meta),
    )
    # Token-text streaming is handled atomically inside
    # append_decode_step (final stage's output_text concatenates into
    # job.text under the same lock as the state transition), so no
    # extra append needed here.
    #
    # Convenience: piggy-back next-token feed in the same RPC so the
    # final-stage worker doesn't need two round-trips per token.
    next_token = (meta or {}).get("next_token_id")
    if (next_token is not None
            and status in {"appended", "completed_job"}
            and job is not None
            and job.state not in {"completed", "failed"}
            and not bool((meta or {}).get("is_terminal"))):
        try:
            _STORE.push_decode_inbox(job_id, int(next_token))
        except (TypeError, ValueError):
            pass
    # Credit the workers when the final stage closes out.
    if status == "completed_job" and job is not None:
        _credit_pipeline_stages(job)
    return {
        "accepted": status in {"appended", "completed_job"},
        "status": status,
        "job_id": job_id,
        "stage_index": stage_index,
        "step_index": step_index,
        "job_state": job.state if job else None,
    }


@method(
    "aicf.pipelineGetUpstreamStep",
    desc="Long-poll for the upstream stage's output at a specific step index",
    aliases=("aicf_pipelineGetUpstreamStep",),
)
async def pipeline_get_upstream_step(
    ctx: Any = None,
    **params: Any,
) -> Dict[str, Any]:
    """Used by middle + final stages during streaming decode. Returns
    the upstream's decode-step record once available. Stage 0's
    "upstream" is the user prompt, which is delivered via
    pipelineGetNextTokenStep instead."""
    p = dict(params or {})
    job_id = _coerce_str(p.get("job_id")).strip()
    stage_index = _coerce_int(p.get("stage_index"), -1)
    step_index = _coerce_int(p.get("step_index"), -1)
    if not job_id or stage_index < 0 or step_index < 0:
        raise InvalidParams(
            "pipelineGetUpstreamStep: 'job_id', 'stage_index', 'step_index' required"
        )
    if stage_index == 0:
        return {"ready": True, "note": "stage_0_has_no_upstream"}
    upstream = stage_index - 1
    deadline = time.time() + 1.5
    while time.time() < deadline:
        # Surface job-terminal early so consumers can stop polling.
        job = _STORE.get(job_id)
        if job is None:
            raise InvalidParams(f"pipelineGetUpstreamStep: unknown job_id {job_id}")
        if job.state in {"completed", "failed"}:
            # If the requested step landed already we still return it
            # even on terminal state (final stage may need to drain).
            entry = _STORE.get_decode_step(job_id, upstream, step_index)
            if entry is not None:
                return {
                    "ready": True,
                    "stage_index": upstream,
                    "step_index": step_index,
                    "output_b64": entry.get("output_b64"),
                    "output_text": entry.get("output_text"),
                    "meta": dict(entry.get("meta") or {}),
                    "completed_at": entry.get("completed_at"),
                    "job_state": job.state,
                }
            return {
                "ready": False,
                "job_state": job.state,
                "note": "job_terminal_before_step_ready",
            }
        entry = _STORE.get_decode_step(job_id, upstream, step_index)
        if entry is not None:
            return {
                "ready": True,
                "stage_index": upstream,
                "step_index": step_index,
                "output_b64": entry.get("output_b64"),
                "output_text": entry.get("output_text"),
                "meta": dict(entry.get("meta") or {}),
                "completed_at": entry.get("completed_at"),
            }
        await asyncio.sleep(0.05)
    return {"ready": False, "stage_index": upstream, "note": "timeout_keep_polling"}


__all__ = [
    "estimate_job_cost",
    "submit_inference_job",
    "stream_job",
    "job_status",
    "settle_job",
    "worker_register",
    "worker_status",
    "worker_claim_next_job",
    "worker_submit_result",
    "pipeline_claim_stage",
    "pipeline_submit_stage_result",
    "pipeline_get_upstream_activation",
    "pipeline_job_status",
    "pipeline_get_stage_info",
    "pipeline_feed_token",
    "pipeline_get_next_token_step",
    "pipeline_submit_decode_step",
    "pipeline_get_upstream_step",
]
