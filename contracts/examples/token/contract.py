# -*- coding: utf-8 -*-
"""
Animica Example Contract — Fungible Token (A20)
------------------------------------------------

A deterministic fungible token that follows an ERC-20–like surface:

Views:
  - name() -> bytes
  - symbol() -> bytes
  - decimals() -> int
  - totalSupply() -> int
  - balanceOf(owner: bytes) -> int
  - allowance(owner: bytes, spender: bytes) -> int
State-changing:
  - init(name: bytes, symbol: bytes, decimals: int, initial_owner: bytes, initial_supply: int) -> None
  - transfer(to: bytes, amount: int) -> bool
  - approve(spender: bytes, amount: int) -> bool
  - transferFrom(src: bytes, dst: bytes, amount: int) -> bool
  - mint(to: bytes, amount: int) -> bool           (owner-only)
  - burn(amount: int) -> bool                      (self-burn)
Ownership:
  - owner() -> bytes
  - transferOwnership(new_owner: bytes) -> None    (owner-only)
  - renounceOwnership() -> None                    (owner-only)

Notes
- Uses only the VM stdlib (storage/events/abi/hash) for determinism.
- Performs strict checked arithmetic in 256-bit unsigned space.
- Emits Transfer/Approval/OwnershipTransferred events with deterministic payloads.
- `init` is callable exactly once; subsequent calls revert.

Event names (bytes):
  b"Transfer", b"Approval", b"OwnershipTransferred", b"Mint", b"Burn"
"""
from __future__ import annotations

from stdlib import storage, events, abi, hash  # VM-provided deterministic modules

# ----------------------------
# Storage keys & helpers
# ----------------------------

K_INIT = b"tok/init"
K_NAME = b"tok/name"
K_SYMBOL = b"tok/symbol"
K_DECIMALS = b"tok/dec"
K_TOTAL = b"tok/total"
K_OWNER = b"tok/owner"

def _k_bal(addr: bytes) -> bytes:
    return b"tok/bal/" + addr

def _k_allow(owner: bytes, spender: bytes) -> bytes:
    return b"tok/allow/" + owner + b"/" + spender

# ----------------------------
# Math (checked uint256)
# ----------------------------

UINT256_MAX = (1 << 256) - 1

def _u256(x: int) -> int:
    if not isinstance(x, int):
        abi.revert(b"ERR_NOT_INT")
    if x < 0 or x > UINT256_MAX:
        abi.revert(b"ERR_U256_RANGE")
    return x

def _add(a: int, b: int) -> int:
    a = _u256(a); b = _u256(b)
    c = a + b
    if c > UINT256_MAX:
        abi.revert(b"ERR_ADD_OVERFLOW")
    return c

def _sub(a: int, b: int) -> int:
    a = _u256(a); b = _u256(b)
    if b > a:
        abi.revert(b"ERR_SUB_UNDERFLOW")
    return a - b

# ----------------------------
# Views (helpers)
# ----------------------------

def _get_uint(key: bytes) -> int:
    v = storage.get(key)
    if v is None:
        return 0
    if not isinstance(v, int):
        abi.revert(b"ERR_TYPE_UINT")
    return _u256(v)

def _set_uint(key: bytes, val: int) -> None:
    storage.set(key, _u256(val))

def _get_bytes(key: bytes) -> bytes:
    v = storage.get(key)
    if v is None:
        return b""
    if not isinstance(v, (bytes, bytearray)):
        abi.revert(b"ERR_TYPE_BYTES")
    return bytes(v)

def _set_bytes(key: bytes, val: bytes) -> None:
    if not isinstance(val, (bytes, bytearray)):
        abi.revert(b"ERR_TYPE_BYTES")
    storage.set(key, bytes(val))

# ----------------------------
# Ownership guard
# ----------------------------

def _only_owner() -> bytes:
    caller = abi.caller()
    owner = _get_bytes(K_OWNER)
    if caller != owner:
        abi.revert(b"ERR_NOT_OWNER")
    return owner

# ----------------------------
# Initialization
# ----------------------------

def init(name: bytes, symbol: bytes, decimals: int, initial_owner: bytes, initial_supply: int) -> None:
    """
    Initialize metadata, set owner, and mint an initial supply to initial_owner.

    Reverts if already initialized.
    """
    if storage.get(K_INIT) is not None:
        abi.revert(b"ERR_ALREADY_INIT")

    # Persist metadata
    _set_bytes(K_NAME, name)
    _set_bytes(K_SYMBOL, symbol)
    if not isinstance(decimals, int) or decimals < 0 or decimals > 255:
        abi.revert(b"ERR_DECIMALS")
    storage.set(K_DECIMALS, int(decimals))

    if not isinstance(initial_owner, (bytes, bytearray)) or len(initial_owner) == 0:
        abi.revert(b"ERR_OWNER_ADDR")
    storage.set(K_OWNER, bytes(initial_owner))

    # Mint initial supply (uint256-checked)
    supply = _u256(initial_supply)
    if supply > 0:
        _mint_to(initial_owner, supply)

    # Mark initialized
    storage.set(K_INIT, 1)

# ----------------------------
# ERC-20–like views
# ----------------------------

def name() -> bytes:
    return _get_bytes(K_NAME)

def symbol() -> bytes:
    return _get_bytes(K_SYMBOL)

def decimals() -> int:
    v = storage.get(K_DECIMALS)
    if v is None:
        return 18  # sane default if not set (should be set by init)
    if not isinstance(v, int):
        abi.revert(b"ERR_DEC_TYPE")
    if v < 0 or v > 255:
        abi.revert(b"ERR_DEC_RANGE")
    return v

def totalSupply() -> int:
    return _get_uint(K_TOTAL)

def owner() -> bytes:
    return _get_bytes(K_OWNER)

def balanceOf(owner_addr: bytes) -> int:
    return _get_uint(_k_bal(owner_addr))

def allowance(owner_addr: bytes, spender: bytes) -> int:
    return _get_uint(_k_allow(owner_addr, spender))

# ----------------------------
# Core token logic
# ----------------------------

def transfer(to: bytes, amount: int) -> bool:
    """
    Move `amount` from caller to `to`.
    Emits: Transfer(from=caller, to=to, value=amount)
    """
    caller = abi.caller()
    _transfer(caller, to, amount)
    return True

def approve(spender: bytes, amount: int) -> bool:
    """
    Set allowance from caller to `spender` to `amount`.
    Emits: Approval(owner=caller, spender=spender, value=amount)
    """
    caller = abi.caller()
    amt = _u256(amount)
    _set_uint(_k_allow(caller, spender), amt)
    events.emit(b"Approval", {b"owner": caller, b"spender": spender, b"value": amt})
    return True

def transferFrom(src: bytes, dst: bytes, amount: int) -> bool:
    """
    Move `amount` from `src` to `dst`, decrementing allowance(src, caller).
    Emits: Approval(owner=src, spender=caller, value=newRemaining), Transfer(src,dst,amount)
    """
    caller = abi.caller()
    amt = _u256(amount)
    if caller != src:
        # Spend allowance
        key = _k_allow(src, caller)
        cur = _get_uint(key)
        if cur < amt:
            abi.revert(b"ERR_ALLOWANCE")
        _set_uint(key, _sub(cur, amt))
        events.emit(b"Approval", {b"owner": src, b"spender": caller, b"value": _get_uint(key)})

    _transfer(src, dst, amt)
    return True

# ----------------------------
# Mint / Burn (owner & self)
# ----------------------------

def mint(to: bytes, amount: int) -> bool:
    """
    Owner-only mint to address `to`.
    Emits: Mint(to, amount) + Transfer(0x0, to, amount)
    """
    _only_owner()
    _mint_to(to, _u256(amount))
    return True

def burn(amount: int) -> bool:
    """
    Burn `amount` from caller's balance.
    Emits: Burn(from, amount) + Transfer(from, 0x0, amount)
    """
    caller = abi.caller()
    amt = _u256(amount)
    _burn_from(caller, amt)
    return True

# ----------------------------
# Ownership
# ----------------------------

def transferOwnership(new_owner: bytes) -> None:
    cur = _only_owner()
    if not isinstance(new_owner, (bytes, bytearray)) or len(new_owner) == 0:
        abi.revert(b"ERR_OWNER_ADDR")
    new_owner_b = bytes(new_owner)
    storage.set(K_OWNER, new_owner_b)
    events.emit(b"OwnershipTransferred", {b"previousOwner": cur, b"newOwner": new_owner_b})

def renounceOwnership() -> None:
    cur = _only_owner()
    storage.set(K_OWNER, b"")  # Explicitly empty; not recommended for production tokens
    events.emit(b"OwnershipTransferred", {b"previousOwner": cur, b"newOwner": b""})

# ----------------------------
# Internal primitives
# ----------------------------

def _transfer(src: bytes, dst: bytes, amount: int) -> None:
    if not isinstance(src, (bytes, bytearray)) or len(src) == 0:
        abi.revert(b"ERR_SRC_ADDR")
    if not isinstance(dst, (bytes, bytearray)) or len(dst) == 0:
        abi.revert(b"ERR_DST_ADDR")
    amt = _u256(amount)

    if src == dst:
        # No-op but still validate range and emit a Transfer of 0 if requested amount is 0
        if amt == 0:
            events.emit(b"Transfer", {b"from": bytes(src), b"to": bytes(dst), b"value": 0})
            return

    kb_src = _k_bal(src)
    kb_dst = _k_bal(dst)

    src_bal = _get_uint(kb_src)
    if src_bal < amt:
        abi.revert(b"ERR_BALANCE")
    _set_uint(kb_src, _sub(src_bal, amt))

    dst_bal = _get_uint(kb_dst)
    _set_uint(kb_dst, _add(dst_bal, amt))

    events.emit(b"Transfer", {b"from": bytes(src), b"to": bytes(dst), b"value": amt})

def _mint_to(dst: bytes, amount: int) -> None:
    if not isinstance(dst, (bytes, bytearray)) or len(dst) == 0:
        abi.revert(b"ERR_DST_ADDR")
    amt = _u256(amount)

    # total ← total + amt
    total = _get_uint(K_TOTAL)
    _set_uint(K_TOTAL, _add(total, amt))

    # balance[dst] ← balance[dst] + amt
    kb_dst = _k_bal(dst)
    _set_uint(kb_dst, _add(_get_uint(kb_dst), amt))

    # events
    events.emit(b"Mint", {b"to": bytes(dst), b"value": amt})
    events.emit(b"Transfer", {b"from": b"", b"to": bytes(dst), b"value": amt})

def _burn_from(src: bytes, amount: int) -> None:
    if not isinstance(src, (bytes, bytearray)) or len(src) == 0:
        abi.revert(b"ERR_SRC_ADDR")
    amt = _u256(amount)

    # balance[src] must be >= amt
    kb_src = _k_bal(src)
    src_bal = _get_uint(kb_src)
    if src_bal < amt:
        abi.revert(b"ERR_BALANCE")
    _set_uint(kb_src, _sub(src_bal, amt))

    # total ← total - amt
    total = _get_uint(K_TOTAL)
    if total < amt:
        abi.revert(b"ERR_TOTAL_UNDER")
    _set_uint(K_TOTAL, _sub(total, amt))

    # events
    events.emit(b"Burn", {b"from": bytes(src), b"value": amt})
    events.emit(b"Transfer", {b"from": bytes(src), b"to": b"", b"value": amt})

# ----------------------------
# Optional: deterministic metadata hashing helper (view)
# ----------------------------

def codeHashDomain() -> bytes:
    """
    Returns a deterministic domain tag that off-chain tooling may include in
    artifact manifests (purely informational helper).
    """
    # Domain = "A20|name|symbol|decimals"
    msg = b"A20|" + _get_bytes(K_NAME) + b"|" + _get_bytes(K_SYMBOL) + b"|" + str(decimals()).encode("ascii")
    return hash.sha3_256(msg)
