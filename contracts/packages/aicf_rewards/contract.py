from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:rewards:init"
K_OWNER = b"aicf:rewards:owner"
K_PAUSED = b"aicf:rewards:paused"


def _k_balance(provider_id: bytes) -> bytes:
    return b"aicf:rewards:bal:" + bytes(provider_id)


def _k_claim_nonce(claim_id: bytes) -> bytes:
    return b"aicf:rewards:claim_nonce:" + bytes(claim_id)


def _k_total_distributed() -> bytes:
    return b"aicf:rewards:total_distributed"


def _k_total_claimed() -> bytes:
    return b"aicf:rewards:total_claimed"


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


def set_paused(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def credit_reward(
    actor: bytes,
    provider_id: bytes,
    amount_anm_nanos: int,
    settlement_id: bytes,
    subsidy_anm_nanos: int,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    _uset(_k_balance(provider_id), _uget(_k_balance(provider_id)) + amt)
    _uset(_k_total_distributed(), _uget(_k_total_distributed()) + amt)

    events.emit(
        b"RewardCredited",
        {
            "provider_id": bytes(provider_id),
            "settlement_id": bytes(settlement_id),
            "amount_anm_nanos": amt,
            "subsidy_anm_nanos": int(subsidy_anm_nanos),
        },
    )


def claim_rewards(actor: bytes, provider_id: bytes, claim_id: bytes) -> int:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    abi.require(_uget(_k_claim_nonce(claim_id)) == 0, b"claim_replay")

    balance = _uget(_k_balance(provider_id))
    abi.require(balance > 0, b"no_rewards")

    _uset(_k_balance(provider_id), 0)
    _uset(_k_claim_nonce(claim_id), 1)
    _uset(_k_total_claimed(), _uget(_k_total_claimed()) + balance)

    events.emit(
        b"RewardClaimed",
        {
            "provider_id": bytes(provider_id),
            "claim_id": bytes(claim_id),
            "amount_anm_nanos": balance,
        },
    )

    return balance


def reward_balance(provider_id: bytes) -> int:
    _ensure_init()
    return _uget(_k_balance(provider_id))


def reward_totals() -> dict:
    _ensure_init()
    return {
        "total_distributed_anm_nanos": _uget(_k_total_distributed()),
        "total_claimed_anm_nanos": _uget(_k_total_claimed()),
    }
