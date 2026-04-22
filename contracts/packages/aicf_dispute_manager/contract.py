from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:dispute:init"
K_OWNER = b"aicf:dispute:owner"
K_PAUSED = b"aicf:dispute:paused"
K_CHALLENGE_WINDOW = b"aicf:dispute:window"


def _k_dispute_exists(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:exists:" + bytes(dispute_id)


def _k_dispute_job(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:job:" + bytes(dispute_id)


def _k_dispute_open_height(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:opened:" + bytes(dispute_id)


def _k_dispute_state(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:state:" + bytes(dispute_id)


def _k_dispute_reason(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:reason:" + bytes(dispute_id)


def _k_dispute_evidence(dispute_id: bytes) -> bytes:
    return b"aicf:dispute:evidence:" + bytes(dispute_id)


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


def init(owner: bytes, challenge_window_blocks: int) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    abi.require(len(bytes(owner)) > 0, b"bad_owner")
    abi.require(int(challenge_window_blocks) > 0, b"bad_window")

    _bset(K_OWNER, bytes(owner))
    _uset(K_CHALLENGE_WINDOW, int(challenge_window_blocks))
    _uset(K_PAUSED, 0)
    _uset(K_INIT, 1)


def set_paused(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def pause(actor: bytes, paused_state: bool) -> None:
    set_paused(actor, paused_state)


def set_challenge_window(actor: bytes, challenge_window_blocks: int) -> None:
    _ensure_init()
    _ensure_owner(actor)
    abi.require(int(challenge_window_blocks) > 0, b"bad_window")
    _uset(K_CHALLENGE_WINDOW, int(challenge_window_blocks))


def open_dispute(actor: bytes, dispute_id: bytes, job_id: bytes, reason: bytes, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_dispute_exists(dispute_id)) == 0, b"dispute_exists")

    _uset(_k_dispute_exists(dispute_id), 1)
    _bset(_k_dispute_job(dispute_id), bytes(job_id))
    _bset(_k_dispute_reason(dispute_id), bytes(reason))
    _uset(_k_dispute_open_height(dispute_id), int(now_height))
    _uset(_k_dispute_state(dispute_id), 1)  # open

    events.emit(
        b"DisputeOpened",
        {
            "dispute_id": bytes(dispute_id),
            "job_id": bytes(job_id),
            "reason": bytes(reason),
            "opened_height": int(now_height),
        },
    )


def attach_evidence_reference(actor: bytes, dispute_id: bytes, evidence_ref: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_dispute_exists(dispute_id)) == 1, b"dispute_missing")
    abi.require(_uget(_k_dispute_state(dispute_id)) == 1, b"dispute_not_open")
    _bset(_k_dispute_evidence(dispute_id), bytes(evidence_ref))
    events.emit(
        b"DisputeEvidenceAttached",
        {
            "dispute_id": bytes(dispute_id),
            "evidence_ref": bytes(evidence_ref),
        },
    )


def resolve_dispute(actor: bytes, dispute_id: bytes, outcome: int, slash_amount_anm_nanos: int, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    abi.require(_uget(_k_dispute_exists(dispute_id)) == 1, b"dispute_missing")
    abi.require(_uget(_k_dispute_state(dispute_id)) == 1, b"dispute_not_open")
    abi.require(outcome in (1, 2, 3), b"bad_outcome")  # 1=uphold provider,2=slash,3=invalid

    opened = _uget(_k_dispute_open_height(dispute_id))
    abi.require(int(now_height) >= opened, b"bad_height")

    _uset(_k_dispute_state(dispute_id), outcome + 1)  # 2/3/4 terminal states

    events.emit(
        b"DisputeResolved",
        {
            "dispute_id": bytes(dispute_id),
            "job_id": _bget(_k_dispute_job(dispute_id)),
            "outcome": int(outcome),
            "slash_amount_anm_nanos": int(slash_amount_anm_nanos),
            "resolve_height": int(now_height),
        },
    )


def slash_provider(actor: bytes, dispute_id: bytes, slash_amount_anm_nanos: int, now_height: int) -> None:
    resolve_dispute(actor, dispute_id, 2, slash_amount_anm_nanos, now_height)


def clear_provider(actor: bytes, dispute_id: bytes, now_height: int) -> None:
    resolve_dispute(actor, dispute_id, 1, 0, now_height)


def refund_requester_if_needed(actor: bytes, dispute_id: bytes, now_height: int) -> None:
    resolve_dispute(actor, dispute_id, 3, 0, now_height)


def dispute_info(dispute_id: bytes) -> dict:
    _ensure_init()
    return {
        "exists": _uget(_k_dispute_exists(dispute_id)) == 1,
        "job_id": _bget(_k_dispute_job(dispute_id)),
        "state": _uget(_k_dispute_state(dispute_id)),
        "opened_height": _uget(_k_dispute_open_height(dispute_id)),
        "challenge_window_blocks": _uget(K_CHALLENGE_WINDOW),
        "reason": _bget(_k_dispute_reason(dispute_id)),
        "evidence_ref": _bget(_k_dispute_evidence(dispute_id)),
    }
