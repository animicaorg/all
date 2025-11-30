# -*- coding: utf-8 -*-
"""
Animica-20 (ERC-20–like) fungible token
=======================================

Deterministic, float-free, storage-backed token implementation designed for the
Animica Python VM. This module exposes a minimal, explicit-caller API surface so
it can be used across environments without relying on implicit globals.

Highlights
----------
- Explicit `caller` parameters for mutating calls (no ambient msg.sender).
- Deterministic storage layout using prefixes from `contracts.stdlib.token`.
- Events emitted via `stdlib.events`:
    - b"Transfer" { b"from": bytes, b"to": bytes, b"value": int }
    - b"Approval" { b"owner": bytes, b"spender": bytes, b"value": int }
- U256-checked math via `contracts.stdlib.math.safe_uint` (no silent wrap).
- Simple owner model (set at init); optional `mint`, `burn`, `burn_from`.

Public interface (ABI sketch)
-----------------------------
# metadata (pure)
name() -> bytes
symbol() -> bytes
decimals() -> int
total_supply() -> int
balance_of(addr: bytes) -> int
allowance(owner: bytes, spender: bytes) -> int

# state-changing (explicit caller)
init(name: bytes, symbol: bytes, decimals: int,
     initial_owner: bytes, initial_supply: int) -> None
transfer(caller: bytes, to: bytes, amount: int) -> bool
approve(caller: bytes, spender: bytes, amount: int) -> bool
transfer_from(caller: bytes, owner: bytes, to: bytes, amount: int) -> bool
increase_allowance(caller: bytes, spender: bytes, added: int) -> bool
decrease_allowance(caller: bytes, spender: bytes, subtracted: int) -> bool

# optional owner-gated supply control
mint(caller: bytes, to: bytes, amount: int) -> bool
burn(caller: bytes, amount: int) -> bool
burn_from(caller: bytes, owner: bytes, amount: int) -> bool

Notes
-----
- Addresses are raw `bytes`. Any bech32m presentation is off-chain/UI.
- Integers are assumed to be in the closed interval [0, 2**256 - 1].
- This module does not manage treasury balances or fees; it only manipulates
  token balances in contract storage.

"""

from __future__ import annotations

from typing import Final, Optional

from stdlib import abi, events, storage  # type: ignore

# Local stdlib helpers
from ..math.safe_uint import (  # type: ignore  # relative import within package
    u256_add, u256_sub)
from . import (DEFAULT_DECIMALS, ERR_BAD_AMOUNT, EVT_APPROVAL,  # type: ignore
               EVT_TRANSFER, clamp_decimals, key_allow, key_balance,
               normalize_symbol, require_address, require_amount, require_name,
               require_symbol)

# ------------------------------------------------------------------------------
# Storage keys (metadata & owner). Values are raw bytes unless noted.
# ------------------------------------------------------------------------------

K_NAME: Final[bytes] = b"tok:meta:name"  # bytes (ASCII)
K_SYMBOL: Final[bytes] = b"tok:meta:symbol"  # bytes (ASCII, typically uppercase)
K_DECIMALS: Final[bytes] = b"tok:meta:dec"  # 1-byte or small int encoded as 32B
K_TOTAL: Final[bytes] = b"tok:meta:total"  # u256 (32B big-endian)
K_OWNER: Final[bytes] = b"tok:meta:owner"  # bytes (address)
K_INIT: Final[bytes] = b"tok:meta:inited"  # presence flag (b"1")

ZERO_ADDR: Final[bytes] = (
    b"\x00"  # non-empty sentinel used in mint/burn Transfer events
)

# ------------------------------------------------------------------------------
# Internal IO helpers (u256 <-> storage)
# ------------------------------------------------------------------------------


def _get_u256(k: bytes) -> int:
    v = storage.get(k)
    return int.from_bytes(v, "big") if v else 0


def _set_u256(k: bytes, n: int) -> None:
    require_amount(n)
    storage.set(k, int(n).to_bytes(32, "big"))


def _get_small_uint(k: bytes) -> int:
    """
    Read a "small" uint (for decimals). We store as 32B big-endian for simplicity.
    """
    v = storage.get(k)
    return int.from_bytes(v, "big") if v else 0


def _set_small_uint(k: bytes, n: int) -> None:
    if not isinstance(n, int) or n < 0 or n > (2**256 - 1):
        abi.revert(ERR_BAD_AMOUNT)
    storage.set(k, int(n).to_bytes(32, "big"))


def _get_bytes(k: bytes) -> bytes:
    v = storage.get(k)
    return v if v else b""


def _set_bytes(k: bytes, bts: bytes) -> None:
    if not isinstance(bts, (bytes, bytearray)):
        abi.revert(b"TOKEN:BAD_BYTES")
    storage.set(k, bytes(bts))


def _owner() -> Optional[bytes]:
    v = storage.get(K_OWNER)
    return v if v else None


def _require_owner(caller: bytes) -> None:
    require_address(caller)
    v = _owner()
    if v is None or v != caller:
        abi.revert(b"TOKEN:NOT_OWNER")


# ------------------------------------------------------------------------------
# Metadata (pure)
# ------------------------------------------------------------------------------


def name() -> bytes:
    return _get_bytes(K_NAME)


def symbol() -> bytes:
    return _get_bytes(K_SYMBOL)


def decimals() -> int:
    d = _get_small_uint(K_DECIMALS)
    return int(d)


def total_supply() -> int:
    return _get_u256(K_TOTAL)


# ------------------------------------------------------------------------------
# Init (one-time)
# ------------------------------------------------------------------------------


def init(
    name: bytes, symbol: bytes, decimals: int, initial_owner: bytes, initial_supply: int
) -> None:
    """
    One-time initializer. Fails if already initialized.
    """
    if storage.get(K_INIT):
        abi.revert(b"TOKEN:ALREADY_INIT")

    # Validate metadata
    require_name(name)
    require_symbol(symbol)
    require_address(initial_owner)
    require_amount(initial_supply)

    sym_norm = normalize_symbol(symbol)
    dec = clamp_decimals(decimals)

    # Persist metadata & owner
    _set_bytes(K_NAME, name)
    _set_bytes(K_SYMBOL, sym_norm)
    _set_small_uint(K_DECIMALS, dec)
    _set_bytes(K_OWNER, initial_owner)
    storage.set(K_INIT, b"1")

    # Mint initial supply to owner (if non-zero)
    if initial_supply > 0:
        _mint_to(initial_owner, initial_supply)
        # Emit Transfer from ZERO_ADDR to owner
        events.emit(
            EVT_TRANSFER,
            {
                b"from": ZERO_ADDR,
                b"to": initial_owner,
                b"value": initial_supply,
            },
        )


# ------------------------------------------------------------------------------
# Views
# ------------------------------------------------------------------------------


def balance_of(addr: bytes) -> int:
    require_address(addr)
    return _get_u256(key_balance(addr))


def allowance(owner: bytes, spender: bytes) -> int:
    require_address(owner)
    require_address(spender)
    return _get_u256(key_allow(owner, spender))


# ------------------------------------------------------------------------------
# Mutations (explicit caller)
# ------------------------------------------------------------------------------


def transfer(caller: bytes, to: bytes, amount: int) -> bool:
    require_address(caller)
    require_address(to)
    require_amount(amount)

    if amount == 0:
        # no-op but still emit Transfer per ERC-20 practice
        events.emit(EVT_TRANSFER, {b"from": caller, b"to": to, b"value": 0})
        return True

    from_key = key_balance(caller)
    to_key = key_balance(to)

    from_bal = _get_u256(from_key)
    if from_bal < amount:
        abi.revert(b"TOKEN:INSUFFICIENT_BALANCE")

    new_from = u256_sub(from_bal, amount)
    to_bal = _get_u256(to_key)
    new_to = u256_add(to_bal, amount)

    _set_u256(from_key, new_from)
    _set_u256(to_key, new_to)

    events.emit(EVT_TRANSFER, {b"from": caller, b"to": to, b"value": amount})
    return True


def approve(caller: bytes, spender: bytes, amount: int) -> bool:
    require_address(caller)
    require_address(spender)
    require_amount(amount)

    allow_key = key_allow(caller, spender)
    _set_u256(allow_key, amount)

    events.emit(
        EVT_APPROVAL,
        {
            b"owner": caller,
            b"spender": spender,
            b"value": amount,
        },
    )
    return True


def transfer_from(caller: bytes, owner: bytes, to: bytes, amount: int) -> bool:
    """
    Spender (`caller`) transfers `amount` from `owner` to `to` using allowance.
    """
    require_address(caller)
    require_address(owner)
    require_address(to)
    require_amount(amount)

    if amount == 0:
        # Emit zero-value transfer for consistency
        events.emit(EVT_TRANSFER, {b"from": owner, b"to": to, b"value": 0})
        return True

    allow_key = key_allow(owner, caller)
    current_allow = _get_u256(allow_key)
    if current_allow < amount:
        abi.revert(b"TOKEN:ALLOWANCE_LOW")

    # Debit allowance first (checks then set)
    new_allow = u256_sub(current_allow, amount)
    _set_u256(allow_key, new_allow)

    # Move balances
    owner_key = key_balance(owner)
    to_key = key_balance(to)
    owner_bal = _get_u256(owner_key)
    if owner_bal < amount:
        abi.revert(b"TOKEN:INSUFFICIENT_BALANCE")

    _set_u256(owner_key, u256_sub(owner_bal, amount))
    _set_u256(to_key, u256_add(_get_u256(to_key), amount))

    events.emit(EVT_TRANSFER, {b"from": owner, b"to": to, b"value": amount})
    return True


def increase_allowance(caller: bytes, spender: bytes, added: int) -> Bool:
    require_address(caller)
    require_address(spender)
    require_amount(added)

    allow_key = key_allow(caller, spender)
    cur = _get_u256(allow_key)
    _set_u256(allow_key, u256_add(cur, added))

    events.emit(
        EVT_APPROVAL,
        {
            b"owner": caller,
            b"spender": spender,
            b"value": _get_u256(allow_key),
        },
    )
    return True


def decrease_allowance(caller: bytes, spender: bytes, subtracted: int) -> bool:
    require_address(caller)
    require_address(spender)
    require_amount(subtracted)

    allow_key = key_allow(caller, spender)
    cur = _get_u256(allow_key)
    if cur < subtracted:
        abi.revert(b"TOKEN:ALLOWANCE_LOW")
    _set_u256(allow_key, u256_sub(cur, subtracted))

    events.emit(
        EVT_APPROVAL,
        {
            b"owner": caller,
            b"spender": spender,
            b"value": _get_u256(allow_key),
        },
    )
    return True


# ------------------------------------------------------------------------------
# Owner-gated supply control (optional)
# ------------------------------------------------------------------------------


def mint(caller: bytes, to: bytes, amount: int) -> bool:
    _require_owner(caller)
    require_address(to)
    require_amount(amount)
    if amount == 0:
        return True

    _mint_to(to, amount)
    events.emit(EVT_TRANSFER, {b"from": ZERO_ADDR, b"to": to, b"value": amount})
    return True


def burn(caller: bytes, amount: int) -> bool:
    """
    Holder burns their own tokens.
    """
    require_address(caller)
    require_amount(amount)
    if amount == 0:
        return True

    bal_key = key_balance(caller)
    cur = _get_u256(bal_key)
    if cur < amount:
        abi.revert(b"TOKEN:INSUFFICIENT_BALANCE")

    _set_u256(bal_key, u256_sub(cur, amount))
    _set_u256(K_TOTAL, u256_sub(total_supply(), amount))
    events.emit(EVT_TRANSFER, {b"from": caller, b"to": ZERO_ADDR, b"value": amount})
    return True


def burn_from(caller: bytes, owner: bytes, amount: int) -> bool:
    """
    Spender burns tokens from `owner` using allowance.
    """
    require_address(caller)
    require_address(owner)
    require_amount(amount)
    if amount == 0:
        return True

    allow_key = key_allow(owner, caller)
    cur_allow = _get_u256(allow_key)
    if cur_allow < amount:
        abi.revert(b"TOKEN:ALLOWANCE_LOW")
    _set_u256(allow_key, u256_sub(cur_allow, amount))

    bal_key = key_balance(owner)
    cur_bal = _get_u256(bal_key)
    if cur_bal < amount:
        abi.revert(b"TOKEN:INSUFFICIENT_BALANCE")

    _set_u256(bal_key, u256_sub(cur_bal, amount))
    _set_u256(K_TOTAL, u256_sub(total_supply(), amount))
    events.emit(EVT_TRANSFER, {b"from": owner, b"to": ZERO_ADDR, b"value": amount})
    return True


# ------------------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------------------


def _mint_to(to: bytes, amount: int) -> None:
    """
    Unsafe mint (no owner check). Validates inputs & updates total+balance.
    Caller should enforce permissions.
    """
    require_address(to)
    require_amount(amount)
    if amount == 0:
        return

    # total supply
    _set_u256(K_TOTAL, u256_add(total_supply(), amount))
    # receiver balance
    to_key = key_balance(to)
    _set_u256(to_key, u256_add(_get_u256(to_key), amount))


# ------------------------------------------------------------------------------
# Convenience: metadata bootstrappers (pure reads)
# ------------------------------------------------------------------------------


def is_initialized() -> bool:
    return bool(storage.get(K_INIT))


def owner() -> bytes:
    v = _owner()
    return v if v else b""


# Explicit public symbols
__all__ = [
    # metadata
    "name",
    "symbol",
    "decimals",
    "total_supply",
    "is_initialized",
    "owner",
    # views
    "balance_of",
    "allowance",
    # mutations
    "init",
    "transfer",
    "approve",
    "transfer_from",
    "increase_allowance",
    "decrease_allowance",
    # supply control
    "mint",
    "burn",
    "burn_from",
]
