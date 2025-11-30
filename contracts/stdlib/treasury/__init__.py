# -*- coding: utf-8 -*-
"""
contracts.stdlib.treasury
=========================

Thin, deterministic helpers that forward to the VM's runtime treasury API
(`stdlib.treasury`). Use these from contracts to read the contract's balance
and to transfer funds in a single, well-audited place.

Why a wrapper?
--------------
- Centralizes validation (non-negative amounts, non-zero address).
- Provides stable error codes/messages for auditability.
- Keeps surface area tiny and deterministic: no I/O, no syscalls outside VM stdlib.

Runtime expectations
--------------------
The VM injects a `stdlib.treasury` module at execution time exposing:

    balance() -> int
    transfer(to: bytes, amount: int) -> None

Both operations are deterministic and meter gas appropriately. This module
simply validates inputs then calls through.

Error codes
-----------
- b"TREASURY:UNAVAILABLE"     : runtime treasury API not present
- b"TREASURY:NEG_AMOUNT"      : amount < 0
- b"TREASURY:ZERO_ADDRESS"    : empty recipient
- b"TREASURY:INSUFFICIENT"    : balance() < required (for require_min_balance)

Public API
----------
- balance() -> int
- transfer(to: bytes, amount: int) -> bool             # returns True on success
- pay(to: bytes, amount: int) -> bool                  # alias of transfer
- require_min_balance(min_amount: int) -> None         # revert if contract balance < min_amount
- safe_amount(amount: int) -> int                      # validates u256 range, returns amount
"""

from __future__ import annotations

from typing import Final

from stdlib import abi  # type: ignore


# Resolve VM-provided treasury primitives (with graceful fallback that reverts)
def _revert_unavailable(*_args, **_kwargs):  # pragma: no cover
    abi.revert(b"TREASURY:UNAVAILABLE")


try:
    from stdlib.treasury import balance as _vm_balance  # type: ignore
except Exception:  # pragma: no cover
    _vm_balance = _revert_unavailable  # type: ignore

try:
    from stdlib.treasury import transfer as _vm_transfer  # type: ignore
except Exception:  # pragma: no cover
    _vm_transfer = _revert_unavailable  # type: ignore


# -----------------------------
# Guards & small encode helpers
# -----------------------------

_U256_MAX: Final[int] = (1 << 256) - 1


def _require_non_negative(amount: int) -> None:
    if amount < 0:
        abi.revert(b"TREASURY:NEG_AMOUNT")


def _require_u256(amount: int) -> None:
    if amount < 0 or amount > _U256_MAX:
        abi.revert(b"TREASURY:NEG_AMOUNT")  # single code for simplicity


def _require_addr(addr: bytes) -> None:
    if not isinstance(addr, (bytes, bytearray)) or len(addr) == 0:
        abi.revert(b"TREASURY:ZERO_ADDRESS")


# -------------
# Public API
# -------------


def balance() -> int:
    """
    Return the current balance associated with this contract's treasury account.
    """
    val = int(_vm_balance())
    if val < 0:
        # Defensive: runtime should never return negative, but guard anyway.
        abi.revert(b"TREASURY:UNAVAILABLE")
    return val


def transfer(to: bytes, amount: int) -> bool:
    """
    Transfer `amount` units from this contract's treasury account to `to`.

    Deterministic semantics:
      - Validates address and amount (u256 bounds, non-negative).
      - Delegates to VM-provided `stdlib.treasury.transfer`.
      - Returns True on success; otherwise reverts with a stable code.
    """
    _require_addr(to)
    _require_u256(amount)
    # VM primitive throws on failure (e.g., insufficient balance); let it bubble or succeed.
    _vm_transfer(bytes(to), int(amount))
    return True


def pay(to: bytes, amount: int) -> bool:
    """
    Alias of `transfer` for readability at call sites.
    """
    return transfer(to, amount)


def require_min_balance(min_amount: int) -> None:
    """
    Revert unless the current balance is at least `min_amount`.
    """
    _require_non_negative(min_amount)
    if balance() < int(min_amount):
        abi.revert(b"TREASURY:INSUFFICIENT")


def safe_amount(amount: int) -> int:
    """
    Validate `amount` fits into unsigned 256-bit range and return it.
    Useful when computing values with intermediate Python ints.
    """
    _require_u256(amount)
    return int(amount)


__all__ = [
    "balance",
    "transfer",
    "pay",
    "require_min_balance",
    "safe_amount",
]
