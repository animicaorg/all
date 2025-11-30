# -*- coding: utf-8 -*-
"""
Animica-20 (ERC20-like) fungible token — workspace template
===========================================================

Deterministic, minimal reference implementation intended as a starting point for
projects created from the Animica "contract-python-workspace" template.

Design goals
------------
- Storage layout is explicit and prefix-namespaced.
- No floating point; all math is saturating / bounds-checked (u256).
- Event names and fields are canonical and deterministic.
- Owner-gated mint/burn provided for bootstrapping; remove if you need fixed supply.
- No external I/O (network/time), no randomness, and no recursion.

ABI notes
---------
Functions are documented with clear docstrings. If you use a docstring-based ABI
extractor, it will pick up names, params and returns. Otherwise, pair this file
with a hand-written `manifest.json`.

Imports come from the VM stdlib surface (`stdlib.*`), which is available inside
the Animica Python VM runtime (see vm_py/stdlib).

This file is self-contained and does not import the contracts stdlib helpers to
keep template portability high. For production-grade features (roles/permit/
pausable) consider composing with the stdlib contracts provided in this repo.
"""

from __future__ import annotations

from typing import Dict, Tuple

# VM stdlib shims (provided by the on-chain runtime)
from stdlib import abi, events, storage

# ---- constants & storage keys ------------------------------------------------

U256_MAX = (1 << 256) - 1

# Namespaced keys (all values encoded as 32-byte big-endian unless noted)
K_INIT = b"\x00init"  # 1 byte (0 or 1)
K_OWNER = b"\x00owner"  # 32 bytes (address)
K_NAME = b"\x00meta:name"  # raw bytes (short)
K_SYMBOL = b"\x00meta:symbol"  # raw bytes (short)
K_DECIMALS = b"\x00meta:decimals"  # 32-byte u256 (should be <= 18)
K_TOTAL = b"\x00supply:total"  # 32-byte u256

BAL_PREFIX = b"\x01bal:"  # + address (32) => value: u256
ALW_PREFIX = b"\x02alw:"  # + owner(32) + b":" + spender(32) => value: u256


# ---- low-level storage helpers ----------------------------------------------


def _get_u256(key: bytes) -> int:
    b = storage.get(key)
    if b is None or len(b) == 0:
        return 0
    # Support exact 32-byte encoding only for determinism.
    return int.from_bytes(b, "big", signed=False)


def _set_u256(key: bytes, value: int) -> None:
    if value < 0 or value > U256_MAX:
        abi.revert(b"u256 out of range")
    storage.set(key, value.to_bytes(32, "big", signed=False))


def _get_raw(key: bytes) -> bytes:
    return storage.get(key) or b""


def _set_raw(key: bytes, value: bytes) -> None:
    storage.set(key, value)


def _bal_key(addr: bytes) -> bytes:
    if len(addr) != 32:
        abi.revert(b"bad address length")
    return BAL_PREFIX + addr


def _alw_key(owner: bytes, spender: bytes) -> bytes:
    if len(owner) != 32 or len(spender) != 32:
        abi.revert(b"bad address length")
    return ALW_PREFIX + owner + b":" + spender


def _get_bal(addr: bytes) -> int:
    return _get_u256(_bal_key(addr))


def _set_bal(addr: bytes, value: int) -> None:
    _set_u256(_bal_key(addr), value)


def _get_alw(owner: bytes, spender: bytes) -> int:
    return _get_u256(_alw_key(owner, spender))


def _set_alw(owner: bytes, spender: bytes, value: int) -> None:
    _set_u256(_alw_key(owner, spender), value)


def _sender() -> bytes:
    """
    Returns the current call sender (32-byte address) via ABI surface.
    """
    s = abi.caller()
    if not isinstance(s, (bytes, bytearray)) or len(s) != 32:
        abi.revert(b"invalid caller")
    return bytes(s)


# ---- safe math ---------------------------------------------------------------


def _u256_add(a: int, b: int) -> int:
    c = a + b
    if c > U256_MAX:
        abi.revert(b"u256 overflow")
    return c


def _u256_sub(a: int, b: int) -> int:
    if b > a:
        abi.revert(b"u256 underflow")
    return a - b


# ---- metadata getters --------------------------------------------------------


def name() -> bytes:
    """Return the token name (bytes)."""
    return _get_raw(K_NAME)


def symbol() -> bytes:
    """Return the token symbol (bytes)."""
    return _get_raw(K_SYMBOL)


def decimals() -> int:
    """Return the number of decimals (u256, but should be small like 18)."""
    return _get_u256(K_DECIMALS)


def total_supply() -> int:
    """Return the total supply (u256)."""
    return _get_u256(K_TOTAL)


# ---- balance & allowance views ----------------------------------------------


def balance_of(owner: bytes) -> int:
    """
    Return the balance of `owner` (u256).

    :param owner: 32-byte address
    :return: u256 balance
    """
    return _get_bal(owner)


def allowance(owner: bytes, spender: bytes) -> int:
    """
    Return remaining allowance from `owner` to `spender` (u256).

    :param owner: 32-byte address
    :param spender: 32-byte address
    :return: u256 allowance
    """
    return _get_alw(owner, spender)


# ---- core transfers ----------------------------------------------------------


def _transfer(src: bytes, dst: bytes, amount: int) -> None:
    if amount < 0:
        abi.revert(b"negative amount")
    if len(src) != 32 or len(dst) != 32:
        abi.revert(b"bad address length")

    if amount == 0:
        # No-op transfer still emits event for transparency (optional).
        events.emit(b"Transfer", {b"from": src, b"to": dst, b"value": amount})
        return

    sbal = _get_bal(src)
    dbal = _get_bal(dst)
    sbal_new = _u256_sub(sbal, amount)
    dbal_new = _u256_add(dbal, amount)
    _set_bal(src, sbal_new)
    _set_bal(dst, dbal_new)
    events.emit(b"Transfer", {b"from": src, b"to": dst, b"value": amount})


def transfer(to: bytes, amount: int) -> bool:
    """
    Transfer `amount` to `to` from the caller.

    :param to: 32-byte address
    :param amount: u256
    :return: True on success
    """
    _transfer(_sender(), to, amount)
    return True


def approve(spender: bytes, amount: int) -> bool:
    """
    Set allowance for `spender` from caller to `amount` (replace, not add).

    :param spender: 32-byte address
    :param amount: u256
    :return: True on success
    """
    if amount < 0 or amount > U256_MAX:
        abi.revert(b"bad allowance")
    owner = _sender()
    _set_alw(owner, spender, amount)
    events.emit(b"Approval", {b"owner": owner, b"spender": spender, b"value": amount})
    return True


def transfer_from(owner: bytes, to: bytes, amount: int) -> bool:
    """
    Transfer `amount` from `owner` to `to` using caller's allowance.

    :param owner: 32-byte address
    :param to: 32-byte address
    :param amount: u256
    :return: True on success
    """
    if amount < 0:
        abi.revert(b"negative amount")

    caller = _sender()
    allowed = _get_alw(owner, caller)
    if allowed < amount:
        abi.revert(b"insufficient allowance")

    # Decrease allowance, then transfer.
    _set_alw(owner, caller, _u256_sub(allowed, amount))
    _transfer(owner, to, amount)
    return True


# ---- owner-gated mint/burn ---------------------------------------------------


def _owner() -> bytes:
    o = _get_raw(K_OWNER)
    if len(o) != 32:
        abi.revert(b"owner not set")
    return o


def _only_owner() -> None:
    if _sender() != _owner():
        abi.revert(b"only owner")


def mint(to: bytes, amount: int) -> bool:
    """
    Mint `amount` tokens to `to`. Only owner.

    :param to: 32-byte address
    :param amount: u256
    """
    _only_owner()
    if amount < 0:
        abi.revert(b"negative mint")

    total = _get_u256(K_TOTAL)
    total_new = _u256_add(total, amount)
    _set_u256(K_TOTAL, total_new)

    bal = _get_bal(to)
    _set_bal(to, _u256_add(bal, amount))

    events.emit(b"Transfer", {b"from": b"\x00" * 32, b"to": to, b"value": amount})
    return True


def burn(from_addr: bytes, amount: int) -> bool:
    """
    Burn `amount` tokens from `from_addr`. Only owner.

    :param from_addr: 32-byte address
    :param amount: u256
    """
    _only_owner()
    if amount < 0:
        abi.revert(b"negative burn")

    total = _get_u256(K_TOTAL)
    _set_u256(K_TOTAL, _u256_sub(total, amount))

    bal = _get_bal(from_addr)
    _set_bal(from_addr, _u256_sub(bal, amount))

    events.emit(
        b"Transfer", {b"from": from_addr, b"to": b"\x00" * 32, b"value": amount}
    )
    return True


# ---- initialization -----------------------------------------------------------


def initialized() -> int:
    """
    Returns 1 if contract is initialized, else 0 (u256).
    """
    return _get_u256(K_INIT)


def init(
    name_bytes: bytes,
    symbol_bytes: bytes,
    decimals_u256: int,
    owner_addr: bytes,
    initial_supply: int,
    initial_recipient: bytes,
) -> None:
    """
    One-time initializer. Must be called exactly once, typically in the deploy tx.

    :param name_bytes: token name (bytes)
    :param symbol_bytes: token symbol (bytes)
    :param decimals_u256: u256 decimals (<= 18 recommended)
    :param owner_addr: 32-byte owner address
    :param initial_supply: u256 tokens to mint at init
    :param initial_recipient: 32-byte address to receive the initial supply
    """
    if initialized() != 0:
        abi.revert(b"already initialized")
    if len(owner_addr) != 32 or len(initial_recipient) != 32:
        abi.revert(b"bad address length")
    if decimals_u256 < 0 or decimals_u256 > 255:
        abi.revert(b"decimals out of range")
    if initial_supply < 0:
        abi.revert(b"negative supply")

    _set_raw(K_NAME, name_bytes)
    _set_raw(K_SYMBOL, symbol_bytes)
    _set_u256(K_DECIMALS, int(decimals_u256))
    storage.set(K_OWNER, owner_addr)
    _set_u256(K_INIT, 1)

    # Mint initial supply if requested
    if initial_supply > 0:
        _set_u256(K_TOTAL, initial_supply)
        _set_bal(initial_recipient, initial_supply)
        events.emit(
            b"Transfer",
            {b"from": b"\x00" * 32, b"to": initial_recipient, b"value": initial_supply},
        )
    else:
        _set_u256(K_TOTAL, 0)


# ---- optional convenience aliases (ERC-20 style names) -----------------------


def totalSupply() -> int:
    """Alias to total_supply()."""
    return total_supply()


def balanceOf(owner: bytes) -> int:
    """Alias to balance_of()."""
    return balance_of(owner)


# End of file.
