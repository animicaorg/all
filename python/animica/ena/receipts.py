"""
animica.ena.receipts
====================

Deterministic useful-work receipts and their on-chain export envelopes.

A :class:`JobReceipt` is the machine-readable record emitted when a useful-work
job is verified/completed. Its ``receipt_hash`` is the SHA3-256 of the
canonical JSON receipt with ``receipt_hash = ""`` and ``onchain_payload = {}``
(per docs/ena/useful-work-jobs.md) so any party can recompute and verify it.

``export-onchain`` wraps the receipt in an envelope carrying a chain-consumable
``onchain`` payload plus a ``credit_event`` shaped for AICF ledger ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .models import RECEIPT_VERSION, Schema, canonical_json, hash_obj, new_uuid, now_ts, sha3_hex

# ENA useful-work job type → AICF JobKind value. Kept as plain strings so this
# module imports without the aicf package present (dev installs); validated
# against aicf.queue.JobKind when that package is available.
JOB_TYPE_TO_AICF_KIND: dict[str, str] = {
    "scrape": "DATA_CURATION",
    "extract": "DATA_CURATION",
    "chunk": "DATA_CURATION",
    "label": "REWARD_MODEL_LABELING",
    "embed": "RAG_INDEX_BUILD",
    "index": "RAG_INDEX_BUILD",
    "summarize": "AI",
    "eval": "EVAL_RUN",
    "dataset_clean": "DATA_CURATION",
    "training_records": "DATA_CURATION",
    "train_prepare": "DATA_CURATION",
    # training run kinds (emitted by the training orchestrator / worker)
    "sft_train": "SFT_TRAIN",
    "dpo_train": "DPO_TRAIN",
    "distill": "DISTILLATION_CPU",
}


def aicf_kind_for(job_type: str) -> str:
    kind = JOB_TYPE_TO_AICF_KIND.get(job_type, "AI")
    try:  # validate against the real enum when shipped alongside aicf
        from aicf.queue.jobkind import JobKind  # type: ignore
        return JobKind(kind).value
    except Exception:  # noqa: BLE001 - aicf optional in dev installs
        return kind


@dataclass
class JobReceipt(Schema):
    receipt_id: str
    job_id: str
    job_hash: str
    job_type: str
    job_status: str
    aicf_task_id: str
    aicf_job_kind: str
    result_hash: str
    receipt_version: str = RECEIPT_VERSION
    receipt_hash: str = ""
    requester: Optional[str] = None
    worker_id: Optional[str] = None
    provider_id: Optional[str] = None
    verification_id: Optional[str] = None
    verification_hash: Optional[str] = None
    verification_passed: bool = False
    source_hashes: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    artifact_hashes: list[str] = field(default_factory=list)
    score: float = 0.0
    score_components: dict[str, Any] = field(default_factory=dict)
    reward: str = "0"
    onchain_payload: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    """SHA3-256 hex over the canonical receipt with volatile fields cleared."""
    normalized = dict(receipt)
    normalized["receipt_hash"] = ""
    normalized["onchain_payload"] = {}
    return sha3_hex(canonical_json(normalized))


def finalize_receipt(receipt: JobReceipt) -> JobReceipt:
    """Compute and stamp the deterministic ``receipt_hash``."""
    receipt.receipt_hash = ""
    receipt.onchain_payload = {}
    receipt.receipt_hash = compute_receipt_hash(receipt.to_dict())
    return receipt


def validate_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Recompute the hash and compare to the stored value."""
    stored = receipt.get("receipt_hash", "")
    recomputed = compute_receipt_hash(receipt)
    ok = bool(stored) and stored == recomputed
    return {"valid": ok, "stored_hash": stored, "computed_hash": recomputed}


def build_onchain_payload(receipt: dict[str, Any]) -> dict[str, Any]:
    """The minimal chain-consumable projection of a receipt."""
    return {
        "receipt_hash": receipt.get("receipt_hash", ""),
        "job_id": receipt.get("job_id", ""),
        "job_hash": receipt.get("job_hash", ""),
        "aicf_task_id": receipt.get("aicf_task_id", ""),
        "aicf_job_kind": receipt.get("aicf_job_kind", ""),
        "result_hash": receipt.get("result_hash", ""),
        "verification_hash": receipt.get("verification_hash", ""),
        "artifact_hashes": receipt.get("artifact_hashes", []),
        "reward": receipt.get("reward", "0"),
    }


def build_credit_event(receipt: dict[str, Any]) -> dict[str, Any]:
    """A deterministic credit-event candidate for AICF ledger ingestion."""
    event = {
        "kind": "aicf.useful_work.credit",
        "aicf_task_id": receipt.get("aicf_task_id", ""),
        "aicf_job_kind": receipt.get("aicf_job_kind", ""),
        "job_hash": receipt.get("job_hash", ""),
        "receipt_hash": receipt.get("receipt_hash", ""),
        "worker_id": receipt.get("worker_id"),
        "provider_id": receipt.get("provider_id"),
        "reward": receipt.get("reward", "0"),
        "verification_passed": receipt.get("verification_passed", False),
    }
    event["event_hash"] = hash_obj(event)
    return event


def build_export_envelope(receipt: dict[str, Any]) -> dict[str, Any]:
    """Full ``export-onchain`` envelope: receipt + validation + onchain + credit."""
    onchain = build_onchain_payload(receipt)
    credit = build_credit_event(receipt)
    onchain["credit_event"] = credit
    return {
        "schema": "ena.export.v1",
        "receipt": receipt,
        "validation": validate_receipt(receipt),
        "onchain": onchain,
        "credit_event": credit,
        "exported_at": now_ts(),
    }
