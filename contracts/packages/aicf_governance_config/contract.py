from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:gov:init"
K_OWNER = b"aicf:gov:owner"
K_PAUSED = b"aicf:gov:paused"


def _k_param(key: bytes) -> bytes:
    return b"aicf:gov:param:" + bytes(key)


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


def init(owner: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    abi.require(len(bytes(owner)) > 0, b"bad_owner")
    _bset(K_OWNER, bytes(owner))
    _uset(K_PAUSED, 0)
    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _bget(K_OWNER)


def set_paused(actor: bytes, paused_state: bool, reason: bytes) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(K_PAUSED, 1 if paused_state else 0)
    events.emit(
        b"GovernancePaused",
        {
            "paused": bool(paused_state),
            "reason": bytes(reason),
        },
    )


def paused() -> bool:
    _ensure_init()
    return _uget(K_PAUSED) == 1


def set_param_u64(actor: bytes, key: bytes, value: int) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _uset(_k_param(key), int(value))
    events.emit(
        b"GovernanceParamUpdated",
        {
            "key": bytes(key),
            "value": int(value),
            "kind": b"u64",
        },
    )


def set_param_bytes(actor: bytes, key: bytes, value: bytes) -> None:
    _ensure_init()
    _ensure_owner(actor)
    _bset(_k_param(key), bytes(value))
    events.emit(
        b"GovernanceParamUpdated",
        {
            "key": bytes(key),
            "value": bytes(value),
            "kind": b"bytes",
        },
    )


def get_param_u64(key: bytes) -> int:
    _ensure_init()
    return _uget(_k_param(key))


def get_param_bytes(key: bytes) -> bytes:
    _ensure_init()
    return _bget(_k_param(key))
