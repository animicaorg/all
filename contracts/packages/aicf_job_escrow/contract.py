from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:esc:init"
K_OWNER = b"aicf:esc:owner"
K_PAUSED = b"aicf:esc:paused"
K_ADMIN = b"aicf:esc:admin:"


def _k_job_amount(job_id: bytes) -> bytes:
    return b"aicf:esc:amount:" + bytes(job_id)


def _k_job_project(job_id: bytes) -> bytes:
    return b"aicf:esc:project:" + bytes(job_id)


def _k_job_provider(job_id: bytes) -> bytes:
    return b"aicf:esc:provider:" + bytes(job_id)


def _k_job_state(job_id: bytes) -> bytes:
    return b"aicf:esc:state:" + bytes(job_id)


def _k_settlement_nonce(settlement_id: bytes) -> bytes:
    return b"aicf:esc:settlement_nonce:" + bytes(settlement_id)


def _k_treasury_subsidy(job_id: bytes) -> bytes:
    return b"aicf:esc:subsidy:" + bytes(job_id)


def _k_call_nonce(tag: bytes, nonce: bytes) -> bytes:
    return b"aicf:esc:nonce:" + bytes(tag) + b":" + bytes(nonce)


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
    _uset(K_ADMIN + bytes(owner), 1)


def set_paused(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def pause(actor: bytes, paused_state: bool) -> None:
    set_paused(actor, paused_state)


def open_job_escrow(
    actor: bytes,
    job_id: bytes,
    project_id: bytes,
    provider_id: bytes,
    reserved_anm_nanos: int,
    treasury_subsidy_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    reserved = int(reserved_anm_nanos)
    subsidy = int(treasury_subsidy_anm_nanos)
    abi.require(reserved > 0, b"reserved_zero")
    abi.require(_uget(_k_job_state(job_id)) == 0, b"escrow_exists")

    _bset(_k_job_project(job_id), bytes(project_id))
    _bset(_k_job_provider(job_id), bytes(provider_id))
    _uset(_k_job_amount(job_id), reserved)
    _uset(_k_treasury_subsidy(job_id), subsidy)
    _uset(_k_job_state(job_id), 1)  # 1=open

    events.emit(
        b"JobEscrowOpened",
        {
            "job_id": bytes(job_id),
            "project_id": bytes(project_id),
            "provider_id": bytes(provider_id),
            "reserved_anm_nanos": reserved,
            "subsidy_anm_nanos": subsidy,
        },
    )


def create_job(actor: bytes, nonce: bytes, job_id: bytes, project_id: bytes, provider_id: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"create", nonce)) == 0, b"replay_create")
    abi.require(_uget(_k_job_state(job_id)) == 0, b"job_exists")

    _bset(_k_job_project(job_id), bytes(project_id))
    _bset(_k_job_provider(job_id), bytes(provider_id))
    _uset(_k_job_amount(job_id), 0)
    _uset(_k_treasury_subsidy(job_id), 0)
    _uset(_k_job_state(job_id), 10)  # 10=created
    _uset(_k_call_nonce(b"create", nonce), 1)

    events.emit(
        b"JobCreated",
        {
            "nonce": bytes(nonce),
            "job_id": bytes(job_id),
            "project_id": bytes(project_id),
            "provider_id": bytes(provider_id),
        },
    )


def fund_job(actor: bytes, nonce: bytes, job_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"fund", nonce)) == 0, b"replay_fund")

    amount = int(amount_anm_nanos)
    abi.require(amount > 0, b"bad_fund")
    state = _uget(_k_job_state(job_id))
    abi.require(state in (10, 11), b"bad_state")

    _uset(_k_job_amount(job_id), _uget(_k_job_amount(job_id)) + amount)
    _uset(_k_job_state(job_id), 11)  # 11=funded
    _uset(_k_call_nonce(b"fund", nonce), 1)

    events.emit(
        b"JobFunded",
        {
            "nonce": bytes(nonce),
            "job_id": bytes(job_id),
            "amount_anm_nanos": amount,
            "funded_anm_nanos": _uget(_k_job_amount(job_id)),
        },
    )


def reserve_budget(
    actor: bytes,
    nonce: bytes,
    job_id: bytes,
    reserved_anm_nanos: int,
    treasury_subsidy_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"reserve", nonce)) == 0, b"replay_reserve")
    abi.require(_uget(_k_job_state(job_id)) in (10, 11), b"bad_state")

    reserved = int(reserved_anm_nanos)
    subsidy = int(treasury_subsidy_anm_nanos)
    abi.require(reserved > 0, b"bad_reserved")
    abi.require(reserved <= _uget(_k_job_amount(job_id)), b"insufficient_fund")

    _uset(_k_job_amount(job_id), reserved)
    _uset(_k_treasury_subsidy(job_id), subsidy)
    _uset(_k_job_state(job_id), 1)
    _uset(_k_call_nonce(b"reserve", nonce), 1)

    events.emit(
        b"BudgetReserved",
        {
            "nonce": bytes(nonce),
            "job_id": bytes(job_id),
            "reserved_anm_nanos": reserved,
            "subsidy_anm_nanos": subsidy,
        },
    )


def settle_job_escrow(
    actor: bytes,
    settlement_id: bytes,
    job_id: bytes,
    provider_reward_anm_nanos: int,
    treasury_cut_anm_nanos: int,
    refunded_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    abi.require(_uget(_k_settlement_nonce(settlement_id)) == 0, b"replay_settlement")

    state = _uget(_k_job_state(job_id))
    abi.require(state == 1, b"escrow_not_open")

    provider_reward = int(provider_reward_anm_nanos)
    treasury_cut = int(treasury_cut_anm_nanos)
    refunded = int(refunded_anm_nanos)
    abi.require(provider_reward >= 0 and treasury_cut >= 0 and refunded >= 0, b"bad_amount")

    reserved = _uget(_k_job_amount(job_id))
    abi.require(provider_reward + treasury_cut + refunded <= reserved + _uget(_k_treasury_subsidy(job_id)), b"over_settlement")

    _uset(_k_job_state(job_id), 2)  # 2=settled
    _uset(_k_settlement_nonce(settlement_id), 1)

    events.emit(
        b"JobEscrowSettled",
        {
            "settlement_id": bytes(settlement_id),
            "job_id": bytes(job_id),
            "provider_reward_anm_nanos": provider_reward,
            "treasury_cut_anm_nanos": treasury_cut,
            "refunded_anm_nanos": refunded,
        },
    )


def finalize_payout(
    actor: bytes,
    nonce: bytes,
    settlement_id: bytes,
    job_id: bytes,
    provider_reward_anm_nanos: int,
    treasury_cut_anm_nanos: int,
    refunded_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"finalize", nonce)) == 0, b"replay_finalize")
    settle_job_escrow(
        actor,
        settlement_id,
        job_id,
        provider_reward_anm_nanos,
        treasury_cut_anm_nanos,
        refunded_anm_nanos,
    )
    _uset(_k_call_nonce(b"finalize", nonce), 1)


def cancel_job_escrow(actor: bytes, job_id: bytes, refunded_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    state = _uget(_k_job_state(job_id))
    abi.require(state == 1, b"escrow_not_open")

    refunded = int(refunded_anm_nanos)
    abi.require(refunded >= 0, b"bad_refund")
    abi.require(refunded <= _uget(_k_job_amount(job_id)), b"refund_too_large")

    _uset(_k_job_state(job_id), 3)  # 3=cancelled

    events.emit(
        b"JobEscrowCancelled",
        {
            "job_id": bytes(job_id),
            "refunded_anm_nanos": refunded,
        },
    )


def refund_unused(actor: bytes, nonce: bytes, job_id: bytes, refunded_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"refund", nonce)) == 0, b"replay_refund")
    cancel_job_escrow(actor, job_id, refunded_anm_nanos)
    _uset(_k_call_nonce(b"refund", nonce), 1)


def cancel_job(actor: bytes, nonce: bytes, job_id: bytes, refunded_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_call_nonce(b"cancel", nonce)) == 0, b"replay_cancel")
    cancel_job_escrow(actor, job_id, refunded_anm_nanos)
    _uset(_k_call_nonce(b"cancel", nonce), 1)


def escrow_info(job_id: bytes) -> dict:
    _ensure_init()
    return {
        "project_id": _bget(_k_job_project(job_id)),
        "provider_id": _bget(_k_job_provider(job_id)),
        "reserved_anm_nanos": _uget(_k_job_amount(job_id)),
        "subsidy_anm_nanos": _uget(_k_treasury_subsidy(job_id)),
        "state": _uget(_k_job_state(job_id)),
    }
