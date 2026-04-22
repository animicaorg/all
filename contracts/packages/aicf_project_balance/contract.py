from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:pb:init"
K_OWNER = b"aicf:pb:owner"
K_PAUSED = b"aicf:pb:paused"


def _k_available(project_id: bytes) -> bytes:
    return b"aicf:pb:avail:" + bytes(project_id)


def _k_reserved(project_id: bytes) -> bytes:
    return b"aicf:pb:resv:" + bytes(project_id)


def _k_total_deposited(project_id: bytes) -> bytes:
    return b"aicf:pb:deposited:" + bytes(project_id)


def _k_total_spent(project_id: bytes) -> bytes:
    return b"aicf:pb:spent:" + bytes(project_id)


def _k_total_refunded(project_id: bytes) -> bytes:
    return b"aicf:pb:refunded:" + bytes(project_id)


def _k_job_reserved(job_id: bytes) -> bytes:
    return b"aicf:pb:job_reserved:" + bytes(job_id)


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


def owner() -> bytes:
    _ensure_init()
    return _bget(K_OWNER)


def paused() -> bool:
    _ensure_init()
    return _uget(K_PAUSED) == 1


def set_paused(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)
    events.emit(b"Paused", {"paused": bool(paused_state)})


def deposit_project(actor: bytes, project_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    _uset(_k_available(project_id), _uget(_k_available(project_id)) + amt)
    _uset(_k_total_deposited(project_id), _uget(_k_total_deposited(project_id)) + amt)

    events.emit(
        b"ProjectDeposited",
        {
            "project_id": bytes(project_id),
            "amount_anm_nanos": amt,
        },
    )


def reserve_for_job(actor: bytes, project_id: bytes, job_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")
    abi.require(_uget(_k_job_reserved(job_id)) == 0, b"job_already_reserved")

    available = _uget(_k_available(project_id))
    abi.require(available >= amt, b"insufficient_balance")

    _uset(_k_available(project_id), available - amt)
    _uset(_k_reserved(project_id), _uget(_k_reserved(project_id)) + amt)
    _uset(_k_job_reserved(job_id), amt)

    events.emit(
        b"ProjectReserved",
        {
            "project_id": bytes(project_id),
            "job_id": bytes(job_id),
            "amount_anm_nanos": amt,
        },
    )


def settle_job(
    actor: bytes,
    project_id: bytes,
    job_id: bytes,
    charged_anm_nanos: int,
    refunded_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    charged = int(charged_anm_nanos)
    refunded = int(refunded_anm_nanos)
    reserved = _uget(_k_job_reserved(job_id))
    abi.require(reserved > 0, b"no_reservation")
    abi.require(charged >= 0 and refunded >= 0, b"bad_amount")
    abi.require(charged + refunded <= reserved, b"over_settlement")

    _uset(_k_job_reserved(job_id), 0)
    _uset(_k_reserved(project_id), _uget(_k_reserved(project_id)) - reserved)
    _uset(_k_available(project_id), _uget(_k_available(project_id)) + refunded)
    _uset(_k_total_spent(project_id), _uget(_k_total_spent(project_id)) + charged)
    _uset(_k_total_refunded(project_id), _uget(_k_total_refunded(project_id)) + refunded)

    events.emit(
        b"JobSettled",
        {
            "project_id": bytes(project_id),
            "job_id": bytes(job_id),
            "charged_anm_nanos": charged,
            "refunded_anm_nanos": refunded,
        },
    )


def withdraw_project(actor: bytes, project_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    available = _uget(_k_available(project_id))
    abi.require(available >= amt, b"insufficient_balance")

    _uset(_k_available(project_id), available - amt)
    _uset(_k_total_refunded(project_id), _uget(_k_total_refunded(project_id)) + amt)

    events.emit(
        b"ProjectWithdrawn",
        {
            "project_id": bytes(project_id),
            "amount_anm_nanos": amt,
        },
    )


def project_balance(project_id: bytes) -> dict:
    _ensure_init()
    return {
        "available_anm_nanos": _uget(_k_available(project_id)),
        "reserved_anm_nanos": _uget(_k_reserved(project_id)),
        "deposited_anm_nanos": _uget(_k_total_deposited(project_id)),
        "spent_anm_nanos": _uget(_k_total_spent(project_id)),
        "refunded_anm_nanos": _uget(_k_total_refunded(project_id)),
    }
