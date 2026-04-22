from __future__ import annotations

from stdlib import abi, events, storage

K_INIT = b"usdan:init"
K_NAME = b"usdan:name"
K_SYMBOL = b"usdan:symbol"
K_DECIMALS = b"usdan:decimals"
K_TOTAL = b"usdan:total"
K_MAX_SUPPLY = b"usdan:max_supply"
K_OWNER = b"usdan:owner"
K_METADATA_URI = b"usdan:metadata_uri"

K_PAUSED = b"usdan:paused"
K_MINT_CONTROLLER = b"usdan:mint_controller"
K_REDEMPTION_CONTROLLER = b"usdan:redemption_controller"
K_COMPLIANCE_CONTROLLER = b"usdan:compliance_controller"
K_ALLOWLIST_ENFORCED = b"usdan:allowlist_enforced"


def _k_bal(addr: bytes) -> bytes:
    return b"usdan:bal:" + bytes(addr)


def _k_allow(owner_addr: bytes, spender: bytes) -> bytes:
    return b"usdan:allow:" + bytes(owner_addr) + b":" + bytes(spender)


def _k_frozen(addr: bytes) -> bytes:
    return b"usdan:frozen:" + bytes(addr)


def _k_blocklisted(addr: bytes) -> bytes:
    return b"usdan:blocklisted:" + bytes(addr)


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


def _mint_controller() -> bytes:
    return _bget(K_MINT_CONTROLLER)


def _redemption_controller() -> bytes:
    return _bget(K_REDEMPTION_CONTROLLER)


def _compliance_controller() -> bytes:
    return _bget(K_COMPLIANCE_CONTROLLER)


def _is_owner(caller: bytes) -> bool:
    return bytes(caller) == _owner()


def _ensure_owner() -> None:
    abi.require(_is_owner(abi.caller()), b"not_owner")


def _ensure_compliance_or_owner() -> None:
    caller = bytes(abi.caller())
    compliance = _compliance_controller()
    abi.require(caller == _owner() or (compliance != b"" and caller == compliance), b"not_compliance")


def _ensure_mint_controller() -> None:
    c = _mint_controller()
    abi.require(c != b"", b"mint_controller_unset")
    abi.require(bytes(abi.caller()) == c, b"not_mint_controller")


def _is_frozen(addr: bytes) -> bool:
    return _uget(_k_frozen(addr)) == 1


def _is_blocklisted(addr: bytes) -> bool:
    return _uget(_k_blocklisted(addr)) == 1


def _is_paused() -> bool:
    return _uget(K_PAUSED) == 1


def _allowlist_enforced() -> bool:
    return _uget(K_ALLOWLIST_ENFORCED) == 1


def _controller_allows(src: bytes, dst: bytes) -> bool:
    compliance = _compliance_controller()
    if compliance == b"":
        # If no controller exists, transfers are only allowed when allowlist mode is disabled.
        return not _allowlist_enforced()
    out = abi.call_contract(compliance, "is_transfer_allowed", [bytes(src), bytes(dst)], read_only=True)
    return bool(out)


def _enforce_transfer_guards(src: bytes, dst: bytes) -> None:
    abi.require(not _is_paused(), b"paused")
    abi.require(not _is_frozen(src), b"src_frozen")
    abi.require(not _is_frozen(dst), b"dst_frozen")
    abi.require(not _is_blocklisted(src), b"src_blocklisted")
    abi.require(not _is_blocklisted(dst), b"dst_blocklisted")
    if _allowlist_enforced():
        abi.require(_controller_allows(src, dst), b"allowlist_block")


def _transfer(src: bytes, dst: bytes, amount: int) -> None:
    _ensure_nonzero_addr(src, b"bad_src")
    _ensure_nonzero_addr(dst, b"bad_dst")
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")
    _enforce_transfer_guards(src, dst)

    src_bal = _uget(_k_bal(src))
    abi.require(src_bal >= amt, b"insufficient_balance")
    _uset(_k_bal(src), src_bal - amt)
    _uset(_k_bal(dst), _uget(_k_bal(dst)) + amt)
    events.emit(b"Transfer", {"from": src, "to": dst, "value": amt})


def _mint_to(dst: bytes, amount: int) -> None:
    _ensure_nonzero_addr(dst, b"bad_dst")
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")
    abi.require(not _is_blocklisted(dst), b"dst_blocklisted")

    total = _uget(K_TOTAL)
    cap = _uget(K_MAX_SUPPLY)
    if cap > 0:
        abi.require(total + amt <= cap, b"max_supply_exceeded")

    _uset(K_TOTAL, total + amt)
    _uset(_k_bal(dst), _uget(_k_bal(dst)) + amt)

    events.emit(b"Mint", {"to": dst, "value": amt})
    events.emit(b"Transfer", {"from": b"", "to": dst, "value": amt})


def _burn_from(src: bytes, amount: int) -> None:
    _ensure_nonzero_addr(src, b"bad_src")
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")

    src_bal = _uget(_k_bal(src))
    abi.require(src_bal >= amt, b"insufficient_balance")
    _uset(_k_bal(src), src_bal - amt)

    total = _uget(K_TOTAL)
    abi.require(total >= amt, b"supply_underflow")
    _uset(K_TOTAL, total - amt)

    events.emit(b"Burn", {"from": src, "value": amt})
    events.emit(b"Transfer", {"from": src, "to": b"", "value": amt})


def init(
    owner: bytes,
    mint_controller: bytes,
    redemption_controller: bytes,
    compliance_controller: bytes,
    metadata_uri: bytes,
    max_supply: int,
    decimals: int,
    enforce_allowlist: bool,
) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")

    dec = int(decimals)
    abi.require(dec >= 0 and dec <= 255, b"bad_decimals")

    cap = int(max_supply)
    abi.require(cap >= 0, b"bad_max_supply")

    _bset(K_NAME, b"Animica Dollar")
    _bset(K_SYMBOL, b"USDAN")
    _uset(K_DECIMALS, dec)
    _bset(K_OWNER, bytes(owner))
    _bset(K_METADATA_URI, bytes(metadata_uri))
    _uset(K_MAX_SUPPLY, cap)

    if bytes(mint_controller) != b"":
        _bset(K_MINT_CONTROLLER, bytes(mint_controller))
    if bytes(redemption_controller) != b"":
        _bset(K_REDEMPTION_CONTROLLER, bytes(redemption_controller))
    if bytes(compliance_controller) != b"":
        _bset(K_COMPLIANCE_CONTROLLER, bytes(compliance_controller))
    _uset(K_ALLOWLIST_ENFORCED, 1 if bool(enforce_allowlist) else 0)

    _uset(K_INIT, 1)


def name() -> bytes:
    _ensure_init()
    return _bget(K_NAME)


def symbol() -> bytes:
    _ensure_init()
    return _bget(K_SYMBOL)


def decimals() -> int:
    _ensure_init()
    return _uget(K_DECIMALS)


def total_supply() -> int:
    _ensure_init()
    return _uget(K_TOTAL)


def totalSupply() -> int:
    return total_supply()


def max_supply() -> int:
    _ensure_init()
    return _uget(K_MAX_SUPPLY)


def owner() -> bytes:
    _ensure_init()
    return _owner()


def metadata_uri() -> bytes:
    _ensure_init()
    return _bget(K_METADATA_URI)


def mint_controller() -> bytes:
    _ensure_init()
    return _mint_controller()


def redemption_controller() -> bytes:
    _ensure_init()
    return _redemption_controller()


def compliance_controller() -> bytes:
    _ensure_init()
    return _compliance_controller()


def allowlist_enforced() -> bool:
    _ensure_init()
    return _allowlist_enforced()


def paused() -> bool:
    _ensure_init()
    return _is_paused()


def balance_of(addr: bytes) -> int:
    _ensure_init()
    return _uget(_k_bal(addr))


def balanceOf(addr: bytes) -> int:
    return balance_of(addr)


def allowance(owner_addr: bytes, spender: bytes) -> int:
    _ensure_init()
    return _uget(_k_allow(owner_addr, spender))


def is_frozen(addr: bytes) -> bool:
    _ensure_init()
    return _is_frozen(addr)


def is_blocklisted(addr: bytes) -> bool:
    _ensure_init()
    return _is_blocklisted(addr)


def transfer(to: bytes, amount: int) -> bool:
    _ensure_init()
    _transfer(bytes(abi.caller()), bytes(to), int(amount))
    return True


def approve(spender: bytes, amount: int) -> bool:
    _ensure_init()
    caller = bytes(abi.caller())
    sp = bytes(spender)
    _ensure_nonzero_addr(sp, b"bad_spender")
    _enforce_transfer_guards(caller, sp)

    amt = int(amount)
    abi.require(amt >= 0, b"bad_amount")
    _uset(_k_allow(caller, sp), amt)
    events.emit(b"Approval", {"owner": caller, "spender": sp, "value": amt})
    return True


def transfer_from(owner_addr: bytes, to: bytes, amount: int) -> bool:
    _ensure_init()
    src = bytes(owner_addr)
    dst = bytes(to)
    caller = bytes(abi.caller())
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")

    if caller != src:
        key = _k_allow(src, caller)
        allowed = _uget(key)
        abi.require(allowed >= amt, b"allowance_low")
        _uset(key, allowed - amt)
        events.emit(b"Approval", {"owner": src, "spender": caller, "value": allowed - amt})

    _transfer(src, dst, amt)
    return True


def transferFrom(owner_addr: bytes, to: bytes, amount: int) -> bool:
    return transfer_from(owner_addr, to, amount)


def mint(to: bytes, amount: int) -> bool:
    _ensure_init()
    _ensure_mint_controller()
    _mint_to(bytes(to), int(amount))
    return True


def burn(amount: int) -> bool:
    _ensure_init()
    _burn_from(bytes(abi.caller()), int(amount))
    return True


def burn_from(owner_addr: bytes, amount: int) -> bool:
    _ensure_init()
    src = bytes(owner_addr)
    caller = bytes(abi.caller())
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")

    if caller != src and caller != _redemption_controller():
        key = _k_allow(src, caller)
        allowed = _uget(key)
        abi.require(allowed >= amt, b"allowance_low")
        _uset(key, allowed - amt)
        events.emit(b"Approval", {"owner": src, "spender": caller, "value": allowed - amt})

    _burn_from(src, amt)
    return True


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    n = bytes(new_owner)
    _ensure_nonzero_addr(n, b"bad_owner")
    prev = _owner()
    _bset(K_OWNER, n)
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": n})


def set_metadata_uri(uri: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    _bset(K_METADATA_URI, bytes(uri))
    events.emit(b"MetadataUpdated", {"uri": bytes(uri)})


def set_mint_controller(controller: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    c = bytes(controller)
    _ensure_nonzero_addr(c, b"bad_controller")
    prev = _mint_controller()
    _bset(K_MINT_CONTROLLER, c)
    events.emit(b"MintControllerUpdated", {"previous": prev, "current": c})


def set_redemption_controller(controller: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    c = bytes(controller)
    _ensure_nonzero_addr(c, b"bad_controller")
    prev = _redemption_controller()
    _bset(K_REDEMPTION_CONTROLLER, c)
    events.emit(b"RedemptionControllerUpdated", {"previous": prev, "current": c})


def set_compliance_controller(controller: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    c = bytes(controller)
    _ensure_nonzero_addr(c, b"bad_controller")
    prev = _compliance_controller()
    _bset(K_COMPLIANCE_CONTROLLER, c)
    events.emit(b"ComplianceControllerUpdated", {"previous": prev, "current": c})


def set_allowlist_enforced(enabled: bool) -> None:
    _ensure_init()
    _ensure_compliance_or_owner()
    _uset(K_ALLOWLIST_ENFORCED, 1 if bool(enabled) else 0)
    events.emit(b"AllowlistModeUpdated", {"enabled": bool(enabled)})


def set_paused(paused_state: bool) -> None:
    _ensure_init()
    _ensure_compliance_or_owner()
    next_state = 1 if bool(paused_state) else 0
    current = _uget(K_PAUSED)
    if current == next_state:
        return
    _uset(K_PAUSED, next_state)
    if next_state == 1:
        events.emit(b"Paused", {"account": bytes(abi.caller())})
    else:
        events.emit(b"Unpaused", {"account": bytes(abi.caller())})


def set_frozen(account: bytes, frozen: bool) -> None:
    _ensure_init()
    _ensure_compliance_or_owner()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    _uset(_k_frozen(a), 1 if bool(frozen) else 0)
    events.emit(b"AccountFrozen", {"account": a, "frozen": bool(frozen)})


def set_blocklisted(account: bytes, blocked: bool) -> None:
    _ensure_init()
    _ensure_compliance_or_owner()
    a = bytes(account)
    _ensure_nonzero_addr(a, b"bad_account")
    _uset(_k_blocklisted(a), 1 if bool(blocked) else 0)
    events.emit(b"AccountBlocklisted", {"account": a, "blocked": bool(blocked)})
