from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"usdan:compliance:init"
K_OWNER = b"usdan:compliance:owner"
K_TOKEN = b"usdan:compliance:token"
K_ALLOWLIST_ENFORCED = b"usdan:compliance:allowlist_enforced"
K_SANCTIONS_REFERENCE = b"usdan:compliance:sanctions_ref"


def _k_admin(account: bytes) -> bytes:
    return b"usdan:compliance:admin:" + bytes(account)


def _k_allow(account: bytes) -> bytes:
    return b"usdan:compliance:allow:" + bytes(account)


def _k_deny(account: bytes) -> bytes:
    return b"usdan:compliance:deny:" + bytes(account)


def _uget(key: bytes) -> int:
    raw = storage.get(key, b"")
    if raw == b"":
        return 0
    return int.from_bytes(raw, "big")


def _uset(key: bytes, value: int) -> None:
    v = int(value)
    abi.require(v >= 0, b"negative")
    if v == 0:
        storage.delete(key)
        return
    storage.set(key, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def _bget(key: bytes) -> bytes:
    return storage.get(key, b"")


def _bset(key: bytes, value: bytes) -> None:
    storage.set(key, bytes(value))


def _ensure_init() -> None:
    abi.require(_uget(K_INIT) == 1, b"not_initialized")


def _ensure_nonzero_addr(addr: bytes, err: bytes) -> None:
    abi.require(isinstance(addr, (bytes, bytearray)), err)
    abi.require(len(addr) > 0, err)


def _owner() -> bytes:
    return _bget(K_OWNER)


def _token() -> bytes:
    return _bget(K_TOKEN)


def _is_admin(account: bytes) -> bool:
    if bytes(account) == _owner():
        return True
    return _uget(_k_admin(bytes(account))) == 1


def _ensure_owner() -> None:
    abi.require(bytes(abi.caller()) == _owner(), b"not_owner")


def _ensure_admin() -> None:
    abi.require(_is_admin(bytes(abi.caller())), b"not_admin")


def init(owner: bytes, token: bytes, sanctions_reference: bytes) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")
    _ensure_nonzero_addr(token, b"bad_token")

    _bset(K_OWNER, bytes(owner))
    _bset(K_TOKEN, bytes(token))
    _bset(K_SANCTIONS_REFERENCE, bytes(sanctions_reference))
    _uset(_k_admin(bytes(owner)), 1)

    _uset(K_INIT, 1)


def owner() -> bytes:
    _ensure_init()
    return _owner()


def token() -> bytes:
    _ensure_init()
    return _token()


def sanctions_reference() -> bytes:
    _ensure_init()
    return _bget(K_SANCTIONS_REFERENCE)


def allowlist_enforced() -> bool:
    _ensure_init()
    return _uget(K_ALLOWLIST_ENFORCED) == 1


def is_admin(account: bytes) -> bool:
    _ensure_init()
    return _is_admin(bytes(account))


def is_allowed(account: bytes) -> bool:
    _ensure_init()
    if not allowlist_enforced():
        return True
    return _uget(_k_allow(bytes(account))) == 1


def is_denied(account: bytes) -> bool:
    _ensure_init()
    return _uget(_k_deny(bytes(account))) == 1


def is_transfer_allowed(src: bytes, dst: bytes) -> bool:
    _ensure_init()
    s = bytes(src)
    d = bytes(dst)

    if is_denied(s) or is_denied(d):
        return False

    if not allowlist_enforced():
        return True

    return is_allowed(s) and is_allowed(d)


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    _ensure_nonzero_addr(n, b"bad_owner")
    prev = _owner()
    _bset(K_OWNER, n)
    _uset(_k_admin(n), 1)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_admin(account: bytes, enabled: bool) -> None:
    _ensure_init()
    _ensure_owner()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    _uset(_k_admin(a), 1 if bool(enabled) else 0)
    events.emit(b"ComplianceAdminUpdated", {"account": a, "enabled": bool(enabled)})


def set_allowlist_enforced(enabled: bool) -> None:
    _ensure_init()
    _ensure_admin()

    mode = 1 if bool(enabled) else 0
    _uset(K_ALLOWLIST_ENFORCED, mode)

    # Keep token state consistent with the off-chain controller policy.
    abi.call_contract(_token(), "set_allowlist_enforced", [bool(enabled)])
    events.emit(b"AllowlistModeUpdated", {"enabled": bool(enabled), "updatedBy": bytes(abi.caller())})


def set_allow(account: bytes, allowed: bool) -> None:
    _ensure_init()
    _ensure_admin()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")

    _uset(_k_allow(a), 1 if bool(allowed) else 0)
    events.emit(b"AllowlistUpdated", {"account": a, "allowed": bool(allowed), "updatedBy": bytes(abi.caller())})


def set_deny(account: bytes, denied: bool) -> None:
    _ensure_init()
    _ensure_admin()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")

    _uset(_k_deny(a), 1 if bool(denied) else 0)
    abi.call_contract(_token(), "set_blocklisted", [a, bool(denied)])
    events.emit(b"DenylistUpdated", {"account": a, "denied": bool(denied), "updatedBy": bytes(abi.caller())})


def set_sanctions_reference(reference: bytes) -> None:
    _ensure_init()
    _ensure_admin()
    _bset(K_SANCTIONS_REFERENCE, bytes(reference))
    events.emit(b"SanctionsReferenceUpdated", {"reference": bytes(reference), "updatedBy": bytes(abi.caller())})


def pause_token(paused_state: bool) -> None:
    _ensure_init()
    _ensure_admin()
    abi.call_contract(_token(), "set_paused", [bool(paused_state)])
    events.emit(b"PauseStatusPushed", {"paused": bool(paused_state), "updatedBy": bytes(abi.caller())})


def freeze_account(account: bytes, frozen: bool) -> None:
    _ensure_init()
    _ensure_admin()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    abi.call_contract(_token(), "set_frozen", [a, bool(frozen)])
    events.emit(b"FreezeStatusPushed", {"account": a, "frozen": bool(frozen), "updatedBy": bytes(abi.caller())})


def blocklist_account(account: bytes, blocked: bool) -> None:
    _ensure_init()
    _ensure_admin()
    set_deny(bytes(account), bool(blocked))
