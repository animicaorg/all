from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:classify:owner"


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def request_classification(actor: bytes, job_id: bytes, records_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")

    events.emit(
        b"AICFModelCallRequested",
        {
            "job_id": bytes(job_id),
            "model_id": b"aicf-chat-1",
            "job_type": b"classification",
            "input_ref_hash": bytes(records_hash),
            "output_schema": b"schema://classification_labels",
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "verification_mode": b"SINGLE_PROVIDER",
        },
    )
