from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:agent:init"
K_OWNER = b"aicf:agent:owner"
K_PAUSED = b"aicf:agent:paused"

# state codes
# 1=created, 2=funded, 3=running, 4=final_submitted,
# 5=challenged, 6=finalized, 7=refunded


def _k_state(task_id: bytes) -> bytes:
    return b"aicf:agent:state:" + bytes(task_id)


def _k_requester(task_id: bytes) -> bytes:
    return b"aicf:agent:requester:" + bytes(task_id)


def _k_payer(task_id: bytes) -> bytes:
    return b"aicf:agent:payer:" + bytes(task_id)


def _k_model_id(task_id: bytes) -> bytes:
    return b"aicf:agent:model:" + bytes(task_id)


def _k_total_budget(task_id: bytes) -> bytes:
    return b"aicf:agent:budget_total:" + bytes(task_id)


def _k_spent_budget(task_id: bytes) -> bytes:
    return b"aicf:agent:budget_spent:" + bytes(task_id)


def _k_remaining_budget(task_id: bytes) -> bytes:
    return b"aicf:agent:budget_remaining:" + bytes(task_id)


def _k_step_timeout(task_id: bytes) -> bytes:
    return b"aicf:agent:step_timeout:" + bytes(task_id)


def _k_step_count(task_id: bytes) -> bytes:
    return b"aicf:agent:step_count:" + bytes(task_id)


def _k_last_step_height(task_id: bytes) -> bytes:
    return b"aicf:agent:last_step_height:" + bytes(task_id)


def _k_step_commitment(task_id: bytes, step: int) -> bytes:
    return b"aicf:agent:step_commit:" + bytes(task_id) + b":" + str(int(step)).encode()


def _k_step_trace(task_id: bytes, step: int) -> bytes:
    return b"aicf:agent:step_trace:" + bytes(task_id) + b":" + str(int(step)).encode()


def _k_final_hash(task_id: bytes) -> bytes:
    return b"aicf:agent:final_hash:" + bytes(task_id)


def _k_final_ref(task_id: bytes) -> bytes:
    return b"aicf:agent:final_ref:" + bytes(task_id)


def _k_challenge_reason(task_id: bytes) -> bytes:
    return b"aicf:agent:challenge_reason:" + bytes(task_id)


def _k_challenge_evidence(task_id: bytes) -> bytes:
    return b"aicf:agent:challenge_evidence:" + bytes(task_id)


def _k_nonce(tag: bytes, nonce: bytes) -> bytes:
    return b"aicf:agent:nonce:" + bytes(tag) + b":" + bytes(nonce)


def _uget(key: bytes) -> int:
    raw = storage.get(key)
    if raw in (None, b""):
        return 0
    return int.from_bytes(raw, "big")


def _uset(key: bytes, value: int) -> None:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.set(key, b"")
        return
    storage.set(key, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def _bget(key: bytes) -> bytes:
    raw = storage.get(key)
    if raw is None:
        return b""
    return raw


def _bset(key: bytes, value: bytes) -> None:
    storage.set(key, bytes(value))


def _ensure_init() -> None:
    abi.require(_uget(K_INIT) == 1, b"not_initialized")


def _ensure_owner(actor: bytes) -> None:
    abi.require(bytes(actor) == _bget(K_OWNER), b"not_owner")


def _ensure_not_paused() -> None:
    abi.require(_uget(K_PAUSED) == 0, b"paused")


def init(owner: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    abi.require(len(bytes(owner)) > 0, b"bad_owner")
    _bset(K_OWNER, bytes(owner))
    _uset(K_PAUSED, 0)
    _uset(K_INIT, 1)


def pause(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def create_agent_task(actor: bytes, nonce: bytes, task_id: bytes, requester: bytes, payer: bytes, model_id: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"create", nonce)) == 0, b"replay_create")
    abi.require(_uget(_k_state(task_id)) == 0, b"task_exists")

    _bset(_k_requester(task_id), bytes(requester))
    _bset(_k_payer(task_id), bytes(payer))
    _bset(_k_model_id(task_id), bytes(model_id))
    _uset(_k_state(task_id), 1)
    _uset(_k_nonce(b"create", nonce), 1)

    events.emit(
        b"AgentTaskCreated",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "requester": bytes(requester),
            "payer": bytes(payer),
            "model_id": bytes(model_id),
        },
    )


def fund_agent_task(actor: bytes, nonce: bytes, task_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"fund", nonce)) == 0, b"replay_fund")
    abi.require(_uget(_k_state(task_id)) in (1, 2), b"bad_state")

    amount = int(amount_anm_nanos)
    abi.require(amount > 0, b"bad_fund")

    total = _uget(_k_total_budget(task_id)) + amount
    _uset(_k_total_budget(task_id), total)
    _uset(_k_remaining_budget(task_id), _uget(_k_remaining_budget(task_id)) + amount)
    _uset(_k_state(task_id), 2)
    _uset(_k_nonce(b"fund", nonce), 1)

    events.emit(
        b"AgentTaskFunded",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "amount_anm_nanos": amount,
            "total_budget": total,
        },
    )


def start_agent_task(actor: bytes, task_id: bytes, step_timeout_blocks: int, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_state(task_id)) == 2, b"not_funded")
    timeout = int(step_timeout_blocks)
    abi.require(timeout > 0, b"bad_timeout")

    _uset(_k_step_timeout(task_id), timeout)
    _uset(_k_last_step_height(task_id), int(now_height))
    _uset(_k_state(task_id), 3)

    events.emit(
        b"AgentTaskStarted",
        {
            "actor": bytes(actor),
            "task_id": bytes(task_id),
            "step_timeout_blocks": timeout,
            "start_height": int(now_height),
        },
    )


def append_step_commitment(
    actor: bytes,
    nonce: bytes,
    task_id: bytes,
    step_cost_anm_nanos: int,
    step_commitment_hash: bytes,
    tool_trace_reference: bytes,
    now_height: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"step", nonce)) == 0, b"replay_step")
    abi.require(_uget(_k_state(task_id)) == 3, b"not_running")

    timeout = _uget(_k_step_timeout(task_id))
    last_height = _uget(_k_last_step_height(task_id))
    abi.require(int(now_height) <= last_height + timeout, b"step_timeout")

    cost = int(step_cost_anm_nanos)
    abi.require(cost >= 0, b"bad_cost")
    remaining = _uget(_k_remaining_budget(task_id))
    abi.require(cost <= remaining, b"insufficient_budget")

    next_step = _uget(_k_step_count(task_id)) + 1
    _bset(_k_step_commitment(task_id, next_step), bytes(step_commitment_hash))
    _bset(_k_step_trace(task_id, next_step), bytes(tool_trace_reference))
    _uset(_k_step_count(task_id), next_step)
    _uset(_k_spent_budget(task_id), _uget(_k_spent_budget(task_id)) + cost)
    _uset(_k_remaining_budget(task_id), remaining - cost)
    _uset(_k_last_step_height(task_id), int(now_height))
    _uset(_k_nonce(b"step", nonce), 1)

    events.emit(
        b"AgentTaskStepCommitted",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "step": next_step,
            "step_cost_anm_nanos": cost,
            "remaining_budget": remaining - cost,
        },
    )


def submit_final_result(actor: bytes, nonce: bytes, task_id: bytes, final_result_hash: bytes, final_result_ref: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"final", nonce)) == 0, b"replay_final")
    abi.require(_uget(_k_state(task_id)) == 3, b"not_running")

    _bset(_k_final_hash(task_id), bytes(final_result_hash))
    _bset(_k_final_ref(task_id), bytes(final_result_ref))
    _uset(_k_state(task_id), 4)
    _uset(_k_nonce(b"final", nonce), 1)

    events.emit(
        b"AgentTaskFinalSubmitted",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "final_result_hash": bytes(final_result_hash),
            "final_result_ref": bytes(final_result_ref),
        },
    )


def challenge_final_result(actor: bytes, task_id: bytes, reason_code: bytes, evidence_reference: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_state(task_id)) in (4, 5), b"bad_state")

    _bset(_k_challenge_reason(task_id), bytes(reason_code))
    _bset(_k_challenge_evidence(task_id), bytes(evidence_reference))
    _uset(_k_state(task_id), 5)

    events.emit(
        b"AgentTaskChallenged",
        {
            "actor": bytes(actor),
            "task_id": bytes(task_id),
            "reason_code": bytes(reason_code),
            "evidence_ref": bytes(evidence_reference),
        },
    )


def finalize_agent_task(actor: bytes, nonce: bytes, task_id: bytes, provider_payout_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_nonce(b"finalize", nonce)) == 0, b"replay_finalize")
    abi.require(_uget(_k_state(task_id)) == 4, b"bad_state")

    payout = int(provider_payout_anm_nanos)
    abi.require(payout >= 0, b"bad_payout")
    spent = _uget(_k_spent_budget(task_id))
    remaining = _uget(_k_remaining_budget(task_id))
    total = _uget(_k_total_budget(task_id))
    abi.require(payout <= total, b"payout_too_large")
    abi.require(spent + remaining == total, b"budget_invariant")

    _uset(_k_state(task_id), 6)
    _uset(_k_nonce(b"finalize", nonce), 1)

    events.emit(
        b"AgentTaskFinalized",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "provider_payout_anm_nanos": payout,
            "remaining_refund_anm_nanos": remaining,
        },
    )


def refund_remaining_budget(actor: bytes, nonce: bytes, task_id: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    abi.require(_uget(_k_nonce(b"refund", nonce)) == 0, b"replay_refund")
    state = _uget(_k_state(task_id))
    abi.require(state in (2, 3, 4, 5), b"bad_state")

    remaining = _uget(_k_remaining_budget(task_id))
    _uset(_k_remaining_budget(task_id), 0)
    _uset(_k_state(task_id), 7)
    _uset(_k_nonce(b"refund", nonce), 1)

    events.emit(
        b"AgentTaskRefunded",
        {
            "actor": bytes(actor),
            "nonce": bytes(nonce),
            "task_id": bytes(task_id),
            "refunded_anm_nanos": remaining,
        },
    )


def task_info(task_id: bytes) -> dict:
    _ensure_init()
    return {
        "state": _uget(_k_state(task_id)),
        "requester": _bget(_k_requester(task_id)),
        "payer": _bget(_k_payer(task_id)),
        "model_id": _bget(_k_model_id(task_id)),
        "total_budget": _uget(_k_total_budget(task_id)),
        "spent_budget": _uget(_k_spent_budget(task_id)),
        "remaining_budget": _uget(_k_remaining_budget(task_id)),
        "step_count": _uget(_k_step_count(task_id)),
        "step_timeout": _uget(_k_step_timeout(task_id)),
        "final_result_hash": _bget(_k_final_hash(task_id)),
        "final_result_ref": _bget(_k_final_ref(task_id)),
        "challenge_reason": _bget(_k_challenge_reason(task_id)),
        "challenge_evidence": _bget(_k_challenge_evidence(task_id)),
    }
