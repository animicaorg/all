from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:stake:init"
K_OWNER = b"aicf:stake:owner"
K_PAUSED = b"aicf:stake:paused"
K_MIN_STAKE = b"aicf:stake:min"
K_UNLOCK_DELAY = b"aicf:stake:unlock_delay"


def _k_stake(provider_id: bytes) -> bytes:
    return b"aicf:stake:amt:" + bytes(provider_id)


def _k_pending_unstake(provider_id: bytes) -> bytes:
    return b"aicf:stake:pending:" + bytes(provider_id)


def _k_unlock_height(provider_id: bytes) -> bytes:
    return b"aicf:stake:unlock_height:" + bytes(provider_id)


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


def init(owner: bytes, min_stake_anm_nanos: int, unlock_delay_blocks: int) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    abi.require(len(bytes(owner)) > 0, b"bad_owner")
    abi.require(int(min_stake_anm_nanos) >= 0, b"bad_min_stake")
    abi.require(int(unlock_delay_blocks) >= 0, b"bad_unlock_delay")

    _bset(K_OWNER, bytes(owner))
    _uset(K_MIN_STAKE, int(min_stake_anm_nanos))
    _uset(K_UNLOCK_DELAY, int(unlock_delay_blocks))
    _uset(K_PAUSED, 0)
    _uset(K_INIT, 1)


def set_paused(actor: bytes, paused_state: bool) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)


def pause(actor: bytes, paused_state: bool) -> None:
    set_paused(actor, paused_state)


def set_min_stake(actor: bytes, min_stake_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_MIN_STAKE, int(min_stake_anm_nanos))


def stake_for_provider(actor: bytes, provider_id: bytes, amount_anm_nanos: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    _uset(_k_stake(provider_id), _uget(_k_stake(provider_id)) + amt)

    events.emit(
        b"StakeAdded",
        {
            "provider_id": bytes(provider_id),
            "amount_anm_nanos": amt,
            "total_stake_anm_nanos": _uget(_k_stake(provider_id)),
        },
    )


def stake(actor: bytes, provider_id: bytes, amount_anm_nanos: int) -> None:
    stake_for_provider(actor, provider_id, amount_anm_nanos)


def request_unstake(actor: bytes, provider_id: bytes, amount_anm_nanos: int, now_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    current = _uget(_k_stake(provider_id))
    abi.require(current >= amt, b"insufficient_stake")
    abi.require(current - amt >= _uget(K_MIN_STAKE), b"below_min_stake")

    _uset(_k_pending_unstake(provider_id), amt)
    _uset(_k_unlock_height(provider_id), int(now_height) + _uget(K_UNLOCK_DELAY))

    events.emit(
        b"UnstakeRequested",
        {
            "provider_id": bytes(provider_id),
            "amount_anm_nanos": amt,
            "unlock_height": _uget(_k_unlock_height(provider_id)),
        },
    )


def finalize_unstake(actor: bytes, provider_id: bytes, now_height: int) -> int:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    pending = _uget(_k_pending_unstake(provider_id))
    abi.require(pending > 0, b"no_pending_unstake")
    abi.require(int(now_height) >= _uget(_k_unlock_height(provider_id)), b"cooldown")

    _uset(_k_stake(provider_id), _uget(_k_stake(provider_id)) - pending)
    _uset(_k_pending_unstake(provider_id), 0)
    _uset(_k_unlock_height(provider_id), 0)

    events.emit(
        b"UnstakeFinalized",
        {
            "provider_id": bytes(provider_id),
            "amount_anm_nanos": pending,
            "remaining_stake_anm_nanos": _uget(_k_stake(provider_id)),
        },
    )

    return pending


def slash_provider(actor: bytes, provider_id: bytes, amount_anm_nanos: int, reason: bytes) -> int:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)

    amt = int(amount_anm_nanos)
    abi.require(amt > 0, b"amount_zero")

    current = _uget(_k_stake(provider_id))
    slashed = amt if current >= amt else current
    _uset(_k_stake(provider_id), current - slashed)

    events.emit(
        b"ProviderSlashed",
        {
            "provider_id": bytes(provider_id),
            "amount_anm_nanos": slashed,
            "reason": bytes(reason),
            "remaining_stake_anm_nanos": _uget(_k_stake(provider_id)),
        },
    )

    return slashed


def slash(actor: bytes, provider_id: bytes, amount_anm_nanos: int, reason: bytes) -> int:
    return slash_provider(actor, provider_id, amount_anm_nanos, reason)


def get_effective_stake(provider_id: bytes) -> int:
    _ensure_init()
    return _uget(_k_stake(provider_id))


def stake_info(provider_id: bytes) -> dict:
    _ensure_init()
    return {
        "stake_anm_nanos": _uget(_k_stake(provider_id)),
        "pending_unstake_anm_nanos": _uget(_k_pending_unstake(provider_id)),
        "unlock_height": _uget(_k_unlock_height(provider_id)),
        "min_stake_anm_nanos": _uget(K_MIN_STAKE),
        "unlock_delay_blocks": _uget(K_UNLOCK_DELAY),
    }
