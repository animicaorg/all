from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:agentreview:owner"


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def request_agent_review(actor: bytes, task_id: bytes, dossier_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")

    events.emit(
        b"AICFAgentTaskCreated",
        {
            "task_id": bytes(task_id),
            "model_id": b"aicf-chat-1",
            "input_ref_hash": bytes(dossier_hash),
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "step_timeout_blocks": 40,
            "verification_mode": b"VERIFIER_REVIEW",
        },
    )
