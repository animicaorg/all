from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:summarize:owner"
K_INIT = b"example:summarize:init"


def _k_budget(job_id: bytes) -> bytes:
    return b"example:summarize:budget:" + bytes(job_id)


def init(owner: bytes) -> None:
    abi.require(storage.get(K_INIT) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))
    storage.set(K_INIT, b"1")


def create_summary_job(actor: bytes, job_id: bytes, input_ref_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")
    storage.set(_k_budget(job_id), int(budget_anm_nanos).to_bytes(16, "big"))

    events.emit(
        b"AICFModelCallRequested",
        {
            "job_id": bytes(job_id),
            "model_id": b"aicf-chat-1",
            "job_type": b"model_call",
            "input_ref_hash": bytes(input_ref_hash),
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "verification_mode": b"CALLBACK_ACCEPT",
        },
    )
