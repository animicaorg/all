"""
animica.ena.demand
==================

Demand side of ENA: a requester connects a wallet, pays ANM, and that funds a
useful-work/training job the contributor fleet then earns from.

Flow:

1. ``quote(job_type, params, reward_anm)`` creates a job in ``awaiting_payment``
   and returns the treasury address + required nano-ANM + the ``memo`` to bind
   (the job_hash).
2. The browser wallet sends ANM to the treasury with ``memo = job_hash``.
3. ``confirm(job_id, txid)`` verifies the payment on-chain (recipient, memo,
   amount, confirmation) and — once confirmed — funds the job (→ ``proposed``,
   reward set) so workers can claim it. txid is de-duplicated so a payment can
   fund exactly one job.
"""

from __future__ import annotations

from typing import Any, Optional

from . import jobs as jobmod
from . import payments as pay
from .errors import JobError
from .models import now_ts

# Job kinds a requester may fund from the web (executable + sensible to pay for).
DEMAND_JOB_TYPES = (
    "extract", "chunk", "embed", "index", "summarize", "label",
    "dataset_clean", "training_records", "eval", "train_prepare", "scrape",
)

STATUS_AWAITING_PAYMENT = "awaiting_payment"


class DemandService:
    def __init__(self, cfg, store, jobs: "jobmod.JobService") -> None:
        self.cfg = cfg
        self.store = store
        self.jobs = jobs

    # -- public config (for the website) ----------------------------------
    def config(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.demand_enabled(),
            "treasury_address": self.cfg.treasury_address,
            "chain_id": self.cfg.chain_id,
            "decimals": pay.ANM_DECIMALS,
            "min_anm": self.cfg.demand_min_anm,
            "job_types": list(DEMAND_JOB_TYPES),
            "confirmations": self.cfg.payment_confirmations,
        }

    def _rpc(self) -> pay.AnimicaRPC:
        return pay.AnimicaRPC(self.cfg.rpc_url)

    def _require_enabled(self) -> None:
        if not self.cfg.demand_enabled():
            raise pay.PaymentError(
                "demand is not configured",
                hint="set ENA_TREASURY_ADDRESS on the coordinator")

    # -- quote ------------------------------------------------------------
    def quote(self, job_type: str, params: dict[str, Any], reward_anm: float,
              requester: Optional[str] = None) -> dict[str, Any]:
        self._require_enabled()
        if job_type not in DEMAND_JOB_TYPES:
            raise JobError(f"job type {job_type!r} is not fundable via demand",
                          hint=f"one of: {', '.join(DEMAND_JOB_TYPES)}")
        reward_anm = float(reward_anm)
        if reward_anm < self.cfg.demand_min_anm:
            raise JobError(f"reward {reward_anm} below minimum {self.cfg.demand_min_anm} ANM")
        required_nano = pay.anm_to_nano(reward_anm)

        job = self.jobs.create(job_type, params, requester=requester)
        job["status"] = STATUS_AWAITING_PAYMENT
        job["funded"] = False
        job["reward"] = reward_anm
        job["required_nano"] = required_nano
        job["payer"] = requester
        job["updated_at"] = now_ts()
        self.store.upsert_job(job)
        return {
            "job_id": job["job_id"],
            "job_hash": job["job_hash"],
            "aicf_task_id": job["aicf_task_id"],
            "status": job["status"],
            "treasury_address": self.cfg.treasury_address,
            "chain_id": self.cfg.chain_id,
            "required_nano": required_nano,
            "required_anm": reward_anm,
            "memo": job["job_hash"],
        }

    # -- confirm ----------------------------------------------------------
    def confirm(self, job_id: str, txid: str) -> dict[str, Any]:
        self._require_enabled()
        job = self.jobs.get(job_id)
        if job.get("funded"):
            return {"job_id": job_id, "status": job["status"], "funded": True,
                    "reason": "already_funded",
                    "payment": self.store.payment_for_job(job_id)}
        if job["status"] != STATUS_AWAITING_PAYMENT:
            raise JobError(f"job {job_id} is not awaiting payment ({job['status']})")

        txid = pay._norm_hex(txid)
        if self.store.payment_seen(txid):
            existing = self.store.payment_for_job(job_id)
            if existing and pay._norm_hex(existing["txid"]) == txid:
                return {"job_id": job_id, "status": job["status"], "funded": bool(job.get("funded"))}
            return {"job_id": job_id, "status": job["status"], "funded": False,
                    "reason": "txid_already_used", "pending": False}

        result = pay.verify_payment(
            self._rpc(), txid, treasury_address=self.cfg.treasury_address,
            min_nano=int(job["required_nano"]), expect_memo=job["job_hash"],
            require_confirmed=self.cfg.payment_confirmations > 0,
            require_memo=self.cfg.payment_require_memo)

        if result["pending"]:
            return {"job_id": job_id, "status": job["status"], "funded": False,
                    "pending": True, "reason": result["reason"], "tx_status": result["status"]}
        if not result["ok"]:
            return {"job_id": job_id, "status": job["status"], "funded": False,
                    "pending": False, "reason": result["reason"], "tx_status": result["status"]}

        # Record payment first (atomic de-dup), then fund the job.
        recorded = self.store.record_payment({
            "txid": txid, "job_id": job_id, "value_nano": result["value_nano"],
            "from_addr": result["from_addr"], "status": "verified", "created_at": now_ts()})
        if not recorded:
            return {"job_id": job_id, "status": job["status"], "funded": False,
                    "reason": "txid_already_used"}

        job["funded"] = True
        job["status"] = jobmod.STATUS_PROPOSED
        job["reward"] = pay.nano_to_anm(result["value_nano"])
        job["payer"] = result["from_addr"]
        job["payment_txid"] = txid
        job["updated_at"] = now_ts()
        self.store.upsert_job(job)
        return {"job_id": job_id, "status": job["status"], "funded": True,
                "reward_anm": job["reward"], "reason": "funded",
                "payment": {"txid": txid, "value_nano": result["value_nano"],
                            "from_addr": result["from_addr"]}}

    # -- status -----------------------------------------------------------
    def status(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        return {
            "job_id": job_id, "job_type": job["job_type"], "status": job["status"],
            "funded": bool(job.get("funded")), "reward_anm": job.get("reward"),
            "result_hash": job.get("result_hash"),
            "worker_id": job.get("worker_id"),
            "payment": self.store.payment_for_job(job_id),
        }
