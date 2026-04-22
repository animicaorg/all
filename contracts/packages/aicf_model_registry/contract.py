from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:model:init"
K_OWNER = b"aicf:model:owner"
K_PAUSED = b"aicf:model:paused"


def _k_exists(model_id: bytes) -> bytes:
    return b"aicf:model:exists:" + bytes(model_id)


def _k_name(model_id: bytes) -> bytes:
    return b"aicf:model:name:" + bytes(model_id)


def _k_version(model_id: bytes) -> bytes:
    return b"aicf:model:version:" + bytes(model_id)


def _k_pricing_ref(model_id: bytes) -> bytes:
    return b"aicf:model:pricing_ref:" + bytes(model_id)


def _k_runtime_ref(model_id: bytes) -> bytes:
    return b"aicf:model:runtime_ref:" + bytes(model_id)


def _k_status(model_id: bytes) -> bytes:
    return b"aicf:model:status:" + bytes(model_id)


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


def register_model(
    actor: bytes,
    model_id: bytes,
    name: bytes,
    version: bytes,
    pricing_ref: bytes,
    runtime_ref: bytes,
) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_exists(model_id)) == 0, b"model_exists")

    _uset(_k_exists(model_id), 1)
    _bset(_k_name(model_id), bytes(name))
    _bset(_k_version(model_id), bytes(version))
    _bset(_k_pricing_ref(model_id), bytes(pricing_ref))
    _bset(_k_runtime_ref(model_id), bytes(runtime_ref))
    _uset(_k_status(model_id), 1)  # 1=active

    events.emit(
        b"ModelRegistered",
        {
            "model_id": bytes(model_id),
            "name": bytes(name),
            "version": bytes(version),
        },
    )


def set_model_status(actor: bytes, model_id: bytes, status: int) -> None:
    _ensure_init()
    _ensure_owner(actor)
    abi.require(_uget(_k_exists(model_id)) == 1, b"model_missing")
    abi.require(status in (1, 2, 3), b"bad_status")  # active/paused/deprecated

    _uset(_k_status(model_id), int(status))
    events.emit(
        b"ModelStatusUpdated",
        {
            "model_id": bytes(model_id),
            "status": int(status),
        },
    )


def model_info(model_id: bytes) -> dict:
    _ensure_init()
    return {
        "exists": _uget(_k_exists(model_id)) == 1,
        "name": _bget(_k_name(model_id)),
        "version": _bget(_k_version(model_id)),
        "pricing_ref": _bget(_k_pricing_ref(model_id)),
        "runtime_ref": _bget(_k_runtime_ref(model_id)),
        "status": _uget(_k_status(model_id)),
    }
