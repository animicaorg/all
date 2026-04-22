from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:escrow:owner"


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def escrow_and_request(actor: bytes, job_id: bytes, input_ref_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")

    events.emit(
        b"AICFJobEscrowCreateAndFund",
        {
            "job_id": bytes(job_id),
            "budget_anm_nanos": int(budget_anm_nanos),
        },
    )

    events.emit(
        b"AICFModelCallRequested",
        {
            "job_id": bytes(job_id),
            "model_id": b"aicf-chat-1",
            "input_ref_hash": bytes(input_ref_hash),
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "verification_mode": b"SINGLE_PROVIDER",
        },
    )
