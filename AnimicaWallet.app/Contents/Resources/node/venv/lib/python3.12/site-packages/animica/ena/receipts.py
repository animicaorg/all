from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import JobReceipt, JobRecord, ReceiptScoreComponent
from .store import EnaStore
from .text import sha3_hex, sha256_hex, stable_id, utc_now_iso


def _canonical_hash(value: Any) -> str:
    if hasattr(value, "canonical_json"):
        return sha3_hex(value.canonical_json())
    return sha3_hex(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _artifact_ids_from_result(result: Dict[str, Any]) -> List[str]:
    artifact_ids: List[str] = []
    for key in ("artifact_id", "receipt_artifact_id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            artifact_ids.append(value)
    extra = result.get("artifact_ids") or result.get("artifacts") or []
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, str) and item:
                artifact_ids.append(item)
            elif isinstance(item, dict) and item.get("artifact_id"):
                artifact_ids.append(str(item["artifact_id"]))
    return sorted(dict.fromkeys(artifact_ids))


def _artifact_hashes(store: EnaStore, artifact_ids: List[str], result: Dict[str, Any]) -> Dict[str, str]:
    hashes: Dict[str, str] = {}
    for artifact_id in artifact_ids:
        record = store.get_artifact(artifact_id)
        if record is not None:
            hashes[artifact_id] = record.sha256
    output_path = result.get("output_path")
    if isinstance(output_path, str) and output_path and Path(output_path).exists():
        hashes.setdefault("output_path", sha256_hex(Path(output_path).read_bytes()))
    return hashes


def _input_refs(job: JobRecord) -> List[str]:
    refs: List[str] = []
    refs.extend(str(item) for item in job.spec.sources if item)
    for key in ("path", "dataset", "eval_dataset", "test_dataset", "seed_file", "index_name"):
        value = job.spec.input_payload.get(key)
        if value:
            refs.append(f"{key}:{value}")
    if job.spec.input_payload.get("texts"):
        refs.append(f"texts:{len(job.spec.input_payload['texts'])}")
    return sorted(dict.fromkeys(refs))


def _output_refs(result: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for key in ("output_path", "artifact_id", "index_name"):
        value = result.get(key)
        if value:
            refs.append(f"{key}:{value}")
    for artifact_id in _artifact_ids_from_result(result):
        refs.append(f"artifact:{artifact_id}")
    return sorted(dict.fromkeys(refs))


def _event_timestamps(store: EnaStore, job_id: str) -> Dict[str, str]:
    events = store.list_job_events(job_id)
    timestamps: Dict[str, str] = {}
    for event in events:
        timestamps.setdefault(event["event"], event["created_at"])
    return timestamps


def build_credit_event(receipt: JobReceipt) -> Dict[str, Any]:
    credits = int(receipt.reward.get("credits", 0) or 0)
    ledger_id = sha3_hex(
        json.dumps(
            {
                "receipt_hash": receipt.receipt_hash,
                "job_id": receipt.job_id,
                "credits": credits,
                "aicf_task_id": receipt.aicf_task_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return {
        "ledger_id": ledger_id,
        "event_type": "ena_useful_work_verified" if receipt.verification_passed else "ena_useful_work_rejected",
        "amount": str(max(credits, 0)),
        "source": "ena.useful_work",
        "job_id": receipt.job_id,
        "metadata": {
            "receipt_hash": receipt.receipt_hash,
            "job_type": receipt.job_type.value,
            "aicf_task_id": receipt.aicf_task_id,
            "aicf_job_kind": receipt.aicf_job_kind,
            "verification_passed": receipt.verification_passed,
        },
    }


def build_onchain_export(receipt: JobReceipt) -> Dict[str, Any]:
    credit_event = build_credit_event(receipt)
    return {
        "schema": "animica.ena.receipt.v1",
        "receipt_hash": receipt.receipt_hash,
        "receipt_id": receipt.receipt_id,
        "job_id": receipt.job_id,
        "job_hash": receipt.job_hash,
        "manifest_hash": receipt.manifest_hash,
        "job_type": receipt.job_type.value,
        "aicf_task_id": receipt.aicf_task_id,
        "aicf_job_kind": receipt.aicf_job_kind,
        "result_hash": receipt.result_hash,
        "verification_hash": receipt.verification_hash,
        "input_refs": receipt.input_refs,
        "output_refs": receipt.output_refs,
        "artifact_hashes": receipt.artifact_hashes,
        "reward": receipt.reward,
        "credit_event": credit_event,
        "created_at": receipt.created_at,
    }


def build_job_receipt(
    job: JobRecord,
    *,
    store: EnaStore,
    provider_id: Optional[str] = None,
    miner_address: Optional[str] = None,
) -> JobReceipt:
    result_hash = _canonical_hash(job.result)
    verification_hash = _canonical_hash(job.verification) if job.verification is not None else None
    artifact_ids = _artifact_ids_from_result(job.result)
    artifact_hashes = _artifact_hashes(store, artifact_ids, job.result)
    source_hashes = sorted(dict.fromkeys(artifact_hashes.values()))
    input_refs = _input_refs(job)
    output_refs = _output_refs(job.result)
    event_timestamps = _event_timestamps(store, job.job_id)
    components = [
        ReceiptScoreComponent(
            name="verification",
            score=float(job.verification.score if job.verification else 0.0),
            weight=1.0,
            detail="verification score",
        ),
        ReceiptScoreComponent(
            name="credits",
            score=float(job.reward.get("credits", 0) or 0),
            weight=1.0,
            detail="awarded credits",
        ),
    ]
    receipt = JobReceipt(
        receipt_id=stable_id("receipt", job.job_id, result_hash, verification_hash or ""),
        job_id=job.job_id,
        job_hash=job.job_hash or job.spec.job_hash,
        manifest_hash=job.job_hash or job.spec.job_hash,
        job_type=job.job_type,
        job_status=job.status,
        aicf_task_id=job.aicf_task_id,
        aicf_job_kind=job.reward.get("aicf_job_kind"),
        requester=job.spec.created_by,
        worker_id=job.claimed_by,
        provider_id=provider_id or job.claimed_by,
        miner_address=miner_address,
        verification_id=job.verification.verification_id if job.verification else None,
        verification_hash=verification_hash,
        verification_passed=bool(job.verification and job.verification.passed),
        result_hash=result_hash,
        input_refs=input_refs,
        output_refs=output_refs,
        event_timestamps=event_timestamps,
        source_hashes=source_hashes,
        artifact_ids=artifact_ids,
        artifact_hashes=artifact_hashes,
        score=float(job.verification.score if job.verification else 0.0),
        score_components=components,
        reward=job.reward,
        created_at=utc_now_iso(),
        metadata={
            "job_created_at": job.created_at,
            "job_updated_at": job.updated_at,
            "status": job.status.value,
        },
    )
    receipt.receipt_hash = _canonical_hash(
        receipt.model_copy(update={"receipt_hash": "", "onchain_payload": {}, "export_payload_hash": ""})
    )
    receipt.onchain_payload = build_onchain_export(receipt)
    receipt.export_payload_hash = _canonical_hash(receipt.onchain_payload)
    return receipt


def validate_receipt(receipt: JobReceipt) -> Dict[str, Any]:
    expected_hash = _canonical_hash(
        receipt.model_copy(update={"receipt_hash": "", "onchain_payload": {}, "export_payload_hash": ""})
    )
    ok = expected_hash == receipt.receipt_hash
    return {
        "ok": ok,
        "expected_hash": expected_hash,
        "receipt_hash": receipt.receipt_hash,
        "job_id": receipt.job_id,
    }
