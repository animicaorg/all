from __future__ import annotations

from stdlib import abi, events, storage

K_OWNER = b"example:workflow:owner"


def init(owner: bytes) -> None:
    abi.require(storage.get(K_OWNER) in (None, b""), b"already_initialized")
    storage.set(K_OWNER, bytes(owner))


def create_workflow(actor: bytes, task_id: bytes, objective_hash: bytes, budget_anm_nanos: int) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    abi.require(int(budget_anm_nanos) > 0, b"bad_budget")

    events.emit(
        b"AICFAgentTaskCreated",
        {
            "task_id": bytes(task_id),
            "objective_hash": bytes(objective_hash),
            "max_budget_anm_nanos": int(budget_anm_nanos),
            "step_timeout_blocks": 40,
            "verification_mode": b"CALLBACK_ACCEPT",
        },
    )


def accept_workflow_result(actor: bytes, task_id: bytes, final_hash: bytes) -> None:
    abi.require(storage.get(K_OWNER) == bytes(actor), b"not_owner")
    events.emit(
        b"AICFAgentTaskAccepted",
        {
            "task_id": bytes(task_id),
            "final_hash": bytes(final_hash),
        },
    )
