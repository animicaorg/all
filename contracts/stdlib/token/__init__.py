# -*- coding: utf-8 -*-
"""
contracts.stdlib.token
======================

Tiny, deterministic helpers and constants for fungible token contracts.
This package **does not** perform storage or event emission by itself —
it only provides conventions, prefixes, and validation to be shared by
token implementations (e.g. ERC-20–like).

Design goals
------------
- Pure-Python, float-free, deterministic.
- Minimal shared surface: bytes prefixes, event names, sanity checks.
- No implicit I/O: token contracts call `stdlib.storage`, `stdlib.events`
  and `stdlib.abi` explicitly in their own code.

Conventions
-----------
Storage keys (prefixed bytes):
  - balances:  BAL_PREFIX || <addr>
  - allowances:ALLOW_PREFIX || <owner> || b"|" || <spender>
Where addresses are raw `bytes` (bech32m is for UI; contracts work in bytes).

Events (names as bytes):
  - b"Transfer" with payload { "from": bytes, "to": bytes, "value": int }
  - b"Approval" with payload { "owner": bytes, "spender": bytes, "value": int }

Symbols/Names:
  - Symbols: 1..11 printable ASCII, typically uppercase (e.g., "ANM").
  - Names:   1..64 printable ASCII, mixed case allowed.

Numeric domain:
  - Amounts fit U256 (0 <= n <= 2**256-1). Use `contracts.stdlib.math.safe_uint`
    for arithmetic (checked or saturating) inside token implementations.

Example (sketch)
----------------
from stdlib import storage, events, abi
from contracts.stdlib.math.safe_uint import u256_add, u256_sub
from contracts.stdlib.token import (
    key_balance, key_allow, EVT_TRANSFER, EVT_APPROVAL,
    require_amount, require_address
)

def _balance_of(addr: bytes) -> int:
    require_address(addr)
    v = storage.get(key_balance(addr))
    return int.from_bytes(v, "big") if v else 0

def _set_balance(addr: bytes, amt: int) -> None:
    storage.set(key_balance(addr), amt.to_bytes(32, "big"))

# ... see dedicated token modules for full reference patterns.
"""

from __future__ import annotations

from typing import Final

# -----------------------------------------------------------------------------
# Optional ABI dependency for deterministic reverts (resolved lazily).
# -----------------------------------------------------------------------------

try:
    from stdlib import abi as _abi_mod  # type: ignore
except Exception:
    _abi_mod = None  # late bind when first needed


def _abi():
    global _abi_mod
    if _abi_mod is None:
        from stdlib import abi as _abi_mod  # type: ignore
    return _abi_mod


def _revert(msg: bytes) -> None:
    _abi().revert(msg)


# -----------------------------------------------------------------------------
# Public constants: storage prefixes, event names, limits, errors
# -----------------------------------------------------------------------------

BAL_PREFIX: Final[bytes] = b"tok:bal:"
ALLOW_PREFIX: Final[bytes] = b"tok:allow:"

EVT_TRANSFER: Final[bytes] = b"Transfer"
EVT_APPROVAL: Final[bytes] = b"Approval"

DEFAULT_DECIMALS: Final[int] = 18

# Stable error tags (short, comparable, log-friendly)
ERR_BAD_ADDR: Final[bytes] = b"TOKEN:BAD_ADDR"
ERR_BAD_AMOUNT: Final[bytes] = b"TOKEN:BAD_AMOUNT"
ERR_BAD_SYMBOL: Final[bytes] = b"TOKEN:BAD_SYMBOL"
ERR_BAD_NAME: Final[bytes] = b"TOKEN:BAD_NAME"


# -----------------------------------------------------------------------------
# Key derivation helpers (no storage I/O here)
# -----------------------------------------------------------------------------


def key_balance(addr: bytes) -> bytes:
    """
    Derive the canonical balance key for an address.
    """
    if not isinstance(addr, (bytes, bytearray)) or len(addr) == 0:
        _revert(ERR_BAD_ADDR)
    return BAL_PREFIX + bytes(addr)


def key_allow(owner: bytes, spender: bytes) -> bytes:
    """
    Derive the canonical allowance key for (owner, spender).
    """
    if not isinstance(owner, (bytes, bytearray)) or len(owner) == 0:
        _revert(ERR_BAD_ADDR)
    if not isinstance(spender, (bytes, bytearray)) or len(spender) == 0:
        _revert(ERR_BAD_ADDR)
    return ALLOW_PREFIX + bytes(owner) + b"|" + bytes(spender)


# -----------------------------------------------------------------------------
# Validation helpers (deterministic, float-free)
# -----------------------------------------------------------------------------


def require_address(addr: bytes) -> None:
    """
    Ensure `addr` is non-empty bytes. We intentionally do not hard-code a width
    here; different networks MAY evolve address byte lengths. Contracts that
    require a fixed size can add a local check.
    """
    if not isinstance(addr, (bytes, bytearray)) or len(addr) == 0:
        _revert(ERR_BAD_ADDR)


def require_amount(n: int) -> None:
    """
    Ensure `n` is an integer amount in [0, 2**256-1].
    """
    if not isinstance(n, int) or n < 0 or n > (2**256 - 1):
        _revert(ERR_BAD_AMOUNT)


def is_printable_ascii(s: bytes) -> bool:
    """
    True iff every byte is printable ASCII (32..126) and not DEL (127).
    """
    if not isinstance(s, (bytes, bytearray)) or len(s) == 0:
        return False
    for b in s:
        if b < 32 or b > 126:
            return False
    return True


def require_symbol(sym: bytes) -> None:
    """
    Symbol must be 1..11 printable ASCII (typ. uppercase, not enforced).
    """
    if not is_printable_ascii(sym) or not (1 <= len(sym) <= 11):
        _revert(ERR_BAD_SYMBOL)


def require_name(name: bytes) -> None:
    """
    Name must be 1..64 printable ASCII.
    """
    if not is_printable_ascii(name) or not (1 <= len(name) <= 64):
        _revert(ERR_BAD_NAME)


# -----------------------------------------------------------------------------
# Normalizers (pure helpers; do not revert)
# -----------------------------------------------------------------------------


def normalize_symbol(sym: bytes) -> bytes:
    """
    Return an uppercased version of `sym` if printable; otherwise return `sym`
    unchanged. Uppercasing is locale-free (ASCII only).
    """
    try:
        s = sym.decode("ascii")
        return s.upper().encode("ascii")
    except Exception:
        return sym


def clamp_decimals(n: int) -> int:
    """
    Clamp decimals to a sane range [0, 36]. (36 is common upper bound in practice.)
    """
    if n < 0:
        return 0
    if n > 36:
        return 36
    return n


# -----------------------------------------------------------------------------
# Public exports
# -----------------------------------------------------------------------------

__all__ = [
    # prefixes
    "BAL_PREFIX",
    "ALLOW_PREFIX",
    # events
    "EVT_TRANSFER",
    "EVT_APPROVAL",
    # defaults/limits
    "DEFAULT_DECIMALS",
    # errors
    "ERR_BAD_ADDR",
    "ERR_BAD_AMOUNT",
    "ERR_BAD_SYMBOL",
    "ERR_BAD_NAME",
    # key derivation
    "key_balance",
    "key_allow",
    # validators
    "require_address",
    "require_amount",
    "require_symbol",
    "require_name",
    "is_printable_ascii",
    # helpers
    "normalize_symbol",
    "clamp_decimals",
]
