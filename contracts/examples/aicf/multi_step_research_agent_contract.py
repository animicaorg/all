from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:research:owner"


def _k_task_budget(task_id: bytes) -> bytes:
    return b"example:research:budget:" + bytes(task_id)


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def start_research_task(actor: bytes, task_id: bytes, topic_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")
    storage.set(_k_task_budget(task_id), int(budget_anm_nanos).to_bytes(16, "big"))

    events.emit(
        b"AICFAgentTaskCreated",
        {
            "task_id": bytes(task_id),
            "model_id": b"aicf-chat-1",
            "input_ref_hash": bytes(topic_hash),
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "verification_mode": b"CALLBACK_ACCEPT",
            "step_timeout_blocks": 30,
        },
    )


def approve_final_result(actor: bytes, task_id: bytes, accepted_result_hash: bytes) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    events.emit(
        b"AICFAgentTaskAccepted",
        {
            "task_id": bytes(task_id),
            "accepted_result_hash": bytes(accepted_result_hash),
        },
    )
