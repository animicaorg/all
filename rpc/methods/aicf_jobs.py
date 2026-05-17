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
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from rpc.errors import InternalError, InvalidParams
from rpc.methods import method

log = logging.getLogger("animica.rpc.aicf_jobs")


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
# fallback kicks in. Keeps chat usable on a fresh node with no providers.
_WORKER_CLAIM_GRACE_S = 2.0
# How long a worker lease is valid; if a worker claims and goes silent,
# the job becomes reclaimable after this many seconds.
_WORKER_LEASE_S = 60.0


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
    claim_owner: Optional[str] = None
    claim_expires_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class _WorkerInfo:
    address: str
    tiers: List[str]
    hardware: Dict[str, Any]
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    jobs_completed: int = 0


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
        now = time.time()
        with self._lock:
            for job in self._jobs.values():
                if job.state == "pending" and (not tiers or job.tier in tiers):
                    job.state = "claimed"
                    job.claim_owner = worker_addr
                    job.claimed_at = now
                    job.claim_expires_at = now + _WORKER_LEASE_S
                    return job
                # Reclaim expired leases
                if (
                    job.state == "claimed"
                    and job.claim_expires_at is not None
                    and job.claim_expires_at < now
                ):
                    job.state = "claimed"
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
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.text = text
            job.provider_id = provider_id
            job.state = "completed"
            job.completed_at = time.time()
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

    # ---------- workers ----------

    def register_worker(self, info: _WorkerInfo) -> None:
        with self._lock:
            self._workers[info.address] = info

    def get_worker(self, address: str) -> Optional[_WorkerInfo]:
        with self._lock:
            w = self._workers.get(address)
            if w is not None:
                w.last_seen = time.time()
            return w

    def all_workers(self) -> List[_WorkerInfo]:
        with self._lock:
            return list(self._workers.values())


_STORE = _AicfJobStore()


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


def _stub_response(prompt: str, tier: str) -> str:
    return (
        f"[distributed-aicf stub @ tier={tier}] No external workers have "
        f"claimed this job; the node returned this placeholder so the "
        f"protocol round-trip completes. The original prompt was: "
        f"{prompt[:240]}{'…' if len(prompt) > 240 else ''}"
    )


async def _local_fallback_after_grace(job_id: str) -> None:
    """If no external worker claims the job within the grace window,
    complete it locally with a stub response so chat doesn't hang."""
    await asyncio.sleep(_WORKER_CLAIM_GRACE_S)
    job = _STORE.get(job_id)
    if job is None or job.state != "pending":
        return
    prompt = str(job.spec.get("prompt", ""))
    text = _stub_response(prompt, job.tier)
    _STORE.complete(job_id, text=text, provider_id="local-stub")
    log.info("aicf_jobs: local-stub completed job_id=%s tier=%s", job_id, job.tier)


def _schedule_fallback(job_id: str) -> None:
    """Schedule the local fallback in whatever event loop is running.
    Falls back to a thread if no loop is reachable from this thread."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_local_fallback_after_grace(job_id))
        return
    except RuntimeError:
        pass

    def _run() -> None:
        time.sleep(_WORKER_CLAIM_GRACE_S)
        job = _STORE.get(job_id)
        if job is None or job.state != "pending":
            return
        prompt = str(job.spec.get("prompt", ""))
        text = _stub_response(prompt, job.tier)
        _STORE.complete(job_id, text=text, provider_id="local-stub")
        log.info(
            "aicf_jobs: local-stub completed (thread) job_id=%s tier=%s",
            job_id,
            job.tier,
        )

    threading.Thread(target=_run, daemon=True, name=f"aicf-stub-{job_id[:8]}").start()


# ---------------------------------------------------------------------------
# Client-facing methods (estimate / submit / stream / status / settle)
# ---------------------------------------------------------------------------


@method("aicf.estimateJobCost", desc="Estimate cost of a distributed-AICF job")
async def estimate_job_cost(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    rec = _JobRecord(
        job_id=job_id,
        spec=dict(spec),
        payment=dict(payment),
        estimated_cost=cost,
        tier=tier,
    )
    _STORE.submit(rec)
    _schedule_fallback(job_id)
    log.info("aicf_jobs: submitted job_id=%s tier=%s est_cost=%.9f", job_id, tier, cost)
    return {
        "job_id": job_id,
        "accepted_tier": tier,
        "provider_id": "",   # filled in on completion
        "estimated_cost_animica": cost,
    }


@method("aicf.streamJob", desc="Stream chunks for an in-flight AICF job")
async def stream_job(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    }


@method("aicf.settleJob", desc="Settle and return final result for a completed AICF job")
async def settle_job(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    }


# ---------------------------------------------------------------------------
# Worker-facing methods
# ---------------------------------------------------------------------------


@method("aicf.workerRegister", desc="Register a worker to claim AICF jobs", aliases=("aicf_workerRegister",))
async def worker_register(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerRegister: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerRegister: 'tiers' must be a list")
    tiers = [_resolve_tier(t) for t in tiers_raw]
    hardware = p.get("hardware") or {}
    if not isinstance(hardware, Mapping):
        raise InvalidParams("workerRegister: 'hardware' must be an object")
    info = _WorkerInfo(address=address, tiers=tiers, hardware=dict(hardware))
    _STORE.register_worker(info)
    log.info("aicf_jobs: worker registered address=%s tiers=%s", address, tiers)
    return {"registered": True, "address": address, "tiers": tiers}


@method("aicf.workerStatus", desc="Get status of a registered AICF worker", aliases=("aicf_workerStatus",))
async def worker_status(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
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
    }


@method("aicf.workerClaimNextJob", desc="Claim the next pending AICF job", aliases=("aicf_workerClaimNextJob",))
async def worker_claim_next_job(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
) -> Optional[Dict[str, Any]]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    if not address:
        raise InvalidParams("workerClaimNextJob: 'address' is required")
    tiers_raw = p.get("tiers") or []
    if not isinstance(tiers_raw, list):
        raise InvalidParams("workerClaimNextJob: 'tiers' must be a list")
    tiers = [_resolve_tier(t) for t in tiers_raw]
    job = _STORE.claim_next(address, tiers)
    if job is None:
        return None
    return {
        "job_id": job.job_id,
        "spec": dict(job.spec),
        "tier": job.tier,
        "estimated_cost_animica": job.estimated_cost,
        "claim_expires_at": job.claim_expires_at,
    }


@method("aicf.workerSubmitResult", desc="Submit the result of a claimed AICF job", aliases=("aicf_workerSubmitResult",))
async def worker_submit_result(
    ctx: Any,
    params: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    p = dict(params or {})
    address = _coerce_str(p.get("address")).strip()
    job_id = _coerce_str(p.get("job_id")).strip()
    text = _coerce_str(p.get("text"))
    if not address or not job_id:
        raise InvalidParams("workerSubmitResult: 'address' and 'job_id' are required")
    job = _STORE.get(job_id)
    if job is None:
        raise InvalidParams(f"workerSubmitResult: unknown job_id {job_id}")
    if job.claim_owner != address:
        raise InvalidParams(
            f"workerSubmitResult: job {job_id} not claimed by {address}"
        )
    if job.state not in {"claimed", "pending"}:
        # Already completed (e.g. local stub raced ahead) — accept idempotently
        return {"accepted": False, "state": job.state}
    _STORE.complete(job_id, text=text, provider_id=address)
    w = _STORE.get_worker(address)
    if w is not None:
        w.jobs_completed += 1
    return {"accepted": True, "state": "completed", "job_id": job_id}


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
]
