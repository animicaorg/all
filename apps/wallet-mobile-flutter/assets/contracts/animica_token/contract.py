from __future__ import annotations

from stdlib import abi, events, storage


K_INIT = b"anm20:init"
K_NAME = b"anm20:name"
K_SYMBOL = b"anm20:symbol"
K_DECIMALS = b"anm20:decimals"
K_TOTAL = b"anm20:total"
K_OWNER = b"anm20:owner"
K_MINTABLE = b"anm20:mintable"
K_MAX_SUPPLY = b"anm20:max_supply"
K_METADATA_URI = b"anm20:metadata_uri"
K_FREEZE_AUTH = b"anm20:freeze_auth"


def _k_bal(addr: bytes) -> bytes:
    return b"anm20:bal:" + addr


def _k_allow(owner: bytes, spender: bytes) -> bytes:
    return b"anm20:allow:" + owner + b":" + spender


def _k_frozen(addr: bytes) -> bytes:
    return b"anm20:frozen:" + addr


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


def _ensure_owner() -> None:
    abi.require(abi.caller() == _bget(K_OWNER), b"not_owner")


def _freeze_authority() -> bytes:
    auth = _bget(K_FREEZE_AUTH)
    if auth == b"":
        return _bget(K_OWNER)
    return auth


def _ensure_not_frozen(addr: bytes) -> None:
    abi.require(_uget(_k_frozen(addr)) == 0, b"account_frozen")


def _transfer(src: bytes, dst: bytes, amount: int) -> None:
    _ensure_nonzero_addr(src, b"bad_src")
    _ensure_nonzero_addr(dst, b"bad_dst")
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")
    _ensure_not_frozen(src)
    _ensure_not_frozen(dst)

    src_bal = _uget(_k_bal(src))
    abi.require(src_bal >= amt, b"insufficient_balance")
    _uset(_k_bal(src), src_bal - amt)
    _uset(_k_bal(dst), _uget(_k_bal(dst)) + amt)
    events.emit(b"Transfer", {"from": src, "to": dst, "value": amt})


def _mint_to(dst: bytes, amount: int) -> None:
    _ensure_nonzero_addr(dst, b"bad_dst")
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")

    total = _uget(K_TOTAL)
    max_supply = _uget(K_MAX_SUPPLY)
    new_total = total + amt
    abi.require(new_total <= max_supply, b"max_supply_exceeded")

    _uset(K_TOTAL, new_total)
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
    name: bytes,
    symbol: bytes,
    decimals: int,
    owner: bytes,
    initial_supply: int,
    max_supply: int,
    mintable: bool,
    metadata_uri: bytes,
    freeze_authority: bytes,
) -> None:
    abi.require(_uget(K_INIT) == 0, b"already_initialized")
    _ensure_nonzero_addr(owner, b"bad_owner")

    dec = int(decimals)
    abi.require(dec >= 0 and dec <= 255, b"bad_decimals")
    supply = int(initial_supply)
    cap = int(max_supply)
    abi.require(supply >= 0, b"bad_supply")
    abi.require(cap > 0 and cap >= supply, b"bad_max_supply")

    _bset(K_NAME, bytes(name))
    _bset(K_SYMBOL, bytes(symbol))
    _uset(K_DECIMALS, dec)
    _bset(K_OWNER, bytes(owner))
    _uset(K_MINTABLE, 1 if bool(mintable) else 0)
    _uset(K_MAX_SUPPLY, cap)
    _bset(K_METADATA_URI, bytes(metadata_uri))
    if freeze_authority != b"":
        _bset(K_FREEZE_AUTH, bytes(freeze_authority))

    _uset(K_INIT, 1)
    if supply > 0:
        _mint_to(owner, supply)


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
    return _bget(K_OWNER)


def mintable() -> bool:
    _ensure_init()
    return _uget(K_MINTABLE) == 1


def metadata_uri() -> bytes:
    _ensure_init()
    return _bget(K_METADATA_URI)


def metadataUri() -> bytes:
    return metadata_uri()


def balance_of(addr: bytes) -> int:
    _ensure_init()
    return _uget(_k_bal(addr))


def balanceOf(addr: bytes) -> int:
    return balance_of(addr)


def allowance(owner_addr: bytes, spender: bytes) -> int:
    _ensure_init()
    return _uget(_k_allow(owner_addr, spender))


def transfer(to: bytes, amount: int) -> bool:
    _ensure_init()
    _transfer(abi.caller(), to, int(amount))
    return True


def approve(spender: bytes, amount: int) -> bool:
    _ensure_init()
    _ensure_nonzero_addr(spender, b"bad_spender")
    _ensure_not_frozen(abi.caller())
    amt = int(amount)
    abi.require(amt >= 0, b"bad_amount")
    _uset(_k_allow(abi.caller(), spender), amt)
    events.emit(b"Approval", {"owner": abi.caller(), "spender": spender, "value": amt})
    return True


def transfer_from(owner_addr: bytes, to: bytes, amount: int) -> bool:
    _ensure_init()
    caller = abi.caller()
    amt = int(amount)
    abi.require(amt > 0, b"amount_zero")

    if caller != owner_addr:
        key = _k_allow(owner_addr, caller)
        allowed = _uget(key)
        abi.require(allowed >= amt, b"allowance_low")
        _uset(key, allowed - amt)
        events.emit(
            b"Approval",
            {"owner": owner_addr, "spender": caller, "value": allowed - amt},
        )

    _transfer(owner_addr, to, amt)
    return True


def transferFrom(owner_addr: bytes, to: bytes, amount: int) -> bool:
    return transfer_from(owner_addr, to, amount)


def mint(to: bytes, amount: int) -> bool:
    _ensure_init()
    _ensure_owner()
    abi.require(_uget(K_MINTABLE) == 1, b"mint_disabled")
    _mint_to(to, int(amount))
    return True


def burn(amount: int) -> bool:
    _ensure_init()
    _burn_from(abi.caller(), int(amount))
    return True


def burn_from(owner_addr: bytes, amount: int) -> bool:
    _ensure_init()
    caller = abi.caller()
    amt = int(amount)

    if caller != owner_addr:
        key = _k_allow(owner_addr, caller)
        allowed = _uget(key)
        abi.require(allowed >= amt, b"allowance_low")
        _uset(key, allowed - amt)
        events.emit(
            b"Approval",
            {"owner": owner_addr, "spender": caller, "value": allowed - amt},
        )

    _burn_from(owner_addr, amt)
    return True


def set_metadata_uri(uri: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    _bset(K_METADATA_URI, bytes(uri))
    events.emit(b"MetadataUpdated", {"uri": bytes(uri)})


def set_owner(new_owner: bytes) -> None:
    _ensure_init()
    _ensure_owner()
    _ensure_nonzero_addr(new_owner, b"bad_owner")
    prev = _bget(K_OWNER)
    _bset(K_OWNER, bytes(new_owner))
    events.emit(b"OwnershipTransferred", {"previousOwner": prev, "newOwner": bytes(new_owner)})


def freeze(addr: bytes, frozen: bool) -> None:
    _ensure_init()
    abi.require(abi.caller() == _freeze_authority(), b"not_freeze_authority")
    _ensure_nonzero_addr(addr, b"bad_addr")
    _uset(_k_frozen(addr), 1 if bool(frozen) else 0)
    events.emit(b"AccountFreeze", {"account": addr, "frozen": bool(frozen)})


def is_frozen(addr: bytes) -> bool:
    _ensure_init()
    return _uget(_k_frozen(addr)) == 1


def freeze_authority() -> bytes:
    _ensure_init()
    return _freeze_authority()
