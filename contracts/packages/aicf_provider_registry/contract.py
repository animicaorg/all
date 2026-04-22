from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"aicf:pr:init"
K_OWNER = b"aicf:pr:owner"
K_PAUSED = b"aicf:pr:paused"


def _k_provider_exists(provider_id: bytes) -> bytes:
    return b"aicf:pr:exists:" + bytes(provider_id)


def _k_provider_wallet(provider_id: bytes) -> bytes:
    return b"aicf:pr:wallet:" + bytes(provider_id)


def _k_provider_state(provider_id: bytes) -> bytes:
    return b"aicf:pr:state:" + bytes(provider_id)


def _k_provider_capabilities(provider_id: bytes) -> bytes:
    return b"aicf:pr:caps:" + bytes(provider_id)


def _k_node_exists(node_id: bytes) -> bytes:
    return b"aicf:pr:node_exists:" + bytes(node_id)


def _k_node_provider(node_id: bytes) -> bytes:
    return b"aicf:pr:node_provider:" + bytes(node_id)


def _k_node_metadata(node_id: bytes) -> bytes:
    return b"aicf:pr:node_metadata:" + bytes(node_id)


def _k_node_state(node_id: bytes) -> bytes:
    return b"aicf:pr:node_state:" + bytes(node_id)


def _k_node_last_heartbeat(node_id: bytes) -> bytes:
    return b"aicf:pr:node_hb:" + bytes(node_id)


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


def pause(actor: bytes, paused_state: bool) -> None:
    set_paused(actor, paused_state)


def register_provider(actor: bytes, provider_id: bytes, wallet: bytes, capabilities_ref: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_provider_exists(provider_id)) == 0, b"provider_exists")

    _uset(_k_provider_exists(provider_id), 1)
    _bset(_k_provider_wallet(provider_id), bytes(wallet))
    _uset(_k_provider_state(provider_id), 1)  # 1=active
    _bset(_k_provider_capabilities(provider_id), bytes(capabilities_ref))

    events.emit(
        b"ProviderRegistered",
        {
            "provider_id": bytes(provider_id),
            "wallet": bytes(wallet),
            "capabilities_ref": bytes(capabilities_ref),
        },
    )


def set_provider_state(actor: bytes, provider_id: bytes, state: int) -> None:
    _ensure_init()
    _ensure_owner(actor)
    abi.require(_uget(_k_provider_exists(provider_id)) == 1, b"provider_missing")
    abi.require(state in (1, 2, 3), b"bad_state")  # active/inactive/quarantined

    _uset(_k_provider_state(provider_id), int(state))
    events.emit(
        b"ProviderStateUpdated",
        {
            "provider_id": bytes(provider_id),
            "state": int(state),
        },
    )


def update_capabilities(actor: bytes, provider_id: bytes, capabilities_ref: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_provider_exists(provider_id)) == 1, b"provider_missing")
    _bset(_k_provider_capabilities(provider_id), bytes(capabilities_ref))
    events.emit(
        b"ProviderCapabilitiesUpdated",
        {
            "provider_id": bytes(provider_id),
            "capabilities_ref": bytes(capabilities_ref),
        },
    )


def set_active(actor: bytes, provider_id: bytes, active: bool) -> None:
    set_provider_state(actor, provider_id, 1 if active else 2)


def quarantine_provider(actor: bytes, provider_id: bytes) -> None:
    set_provider_state(actor, provider_id, 3)


def unregister_provider(actor: bytes, provider_id: bytes) -> None:
    _ensure_init()
    _ensure_owner(actor)
    abi.require(_uget(_k_provider_exists(provider_id)) == 1, b"provider_missing")
    _uset(_k_provider_state(provider_id), 2)  # inactive
    _uset(_k_provider_exists(provider_id), 0)
    events.emit(
        b"ProviderUnregistered",
        {
            "provider_id": bytes(provider_id),
        },
    )


def register_node(actor: bytes, provider_id: bytes, node_id: bytes, metadata_ref: bytes) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_provider_exists(provider_id)) == 1, b"provider_missing")
    abi.require(_uget(_k_node_exists(node_id)) == 0, b"node_exists")

    _uset(_k_node_exists(node_id), 1)
    _bset(_k_node_provider(node_id), bytes(provider_id))
    _bset(_k_node_metadata(node_id), bytes(metadata_ref))
    _uset(_k_node_state(node_id), 1)

    events.emit(
        b"NodeRegistered",
        {
            "provider_id": bytes(provider_id),
            "node_id": bytes(node_id),
            "metadata_ref": bytes(metadata_ref),
        },
    )


def heartbeat_node(actor: bytes, node_id: bytes, at_height: int) -> None:
    _ensure_init()
    _ensure_not_paused()
    _ensure_owner(actor)
    abi.require(_uget(_k_node_exists(node_id)) == 1, b"node_missing")

    _uset(_k_node_last_heartbeat(node_id), int(at_height))

    events.emit(
        b"NodeHeartbeat",
        {
            "node_id": bytes(node_id),
            "height": int(at_height),
        },
    )


def provider_info(provider_id: bytes) -> dict:
    _ensure_init()
    return {
        "exists": _uget(_k_provider_exists(provider_id)) == 1,
        "wallet": _bget(_k_provider_wallet(provider_id)),
        "state": _uget(_k_provider_state(provider_id)),
        "capabilities_ref": _bget(_k_provider_capabilities(provider_id)),
    }


def node_info(node_id: bytes) -> dict:
    _ensure_init()
    return {
        "exists": _uget(_k_node_exists(node_id)) == 1,
        "provider_id": _bget(_k_node_provider(node_id)),
        "metadata_ref": _bget(_k_node_metadata(node_id)),
        "state": _uget(_k_node_state(node_id)),
        "last_heartbeat": _uget(_k_node_last_heartbeat(node_id)),
    }
