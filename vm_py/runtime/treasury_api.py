"""
vm_py.runtime.treasury_api — minimal, deterministic balance ledger for local VM runs.

This module provides a tiny in-memory "treasury" for contracts when running the
VM locally (e.g., studio-wasm / tests). It exposes a contract-facing API that
the stdlib re-exports:

- balance() -> int                  # this contract's balance (default address = "self")
- balance_of(addr: bytes) -> int    # any address balance (read-only)
- transfer(to: bytes, amount: int)  # debit self, credit recipient

Notes
-----
* This is a simulation-only ledger. Real chain accounting happens inside the node's
  execution layer. Engines embedding this VM in a full node should override this
  module via host bindings.
* Deterministic: no wall-clock, no randomness, pure arithmetic with explicit caps.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

try:
    from vm_py.errors import VmError
except Exception:  # pragma: no cover - bootstrap path
    class VmError(Exception):  # type: ignore
        pass

# Config with safe defaults if vm_py.config isn't present yet.
try:
    import vm_py.config as _cfg  # type: ignore
except Exception:  # pragma: no cover
    class _cfg:  # type: ignore
        ADDRESS_LEN = 32
        MAX_BALANCE_BITS = 256


# ------------------------------ State & Locks ------------------------------ #

_L = threading.RLock()
_LEDGER: Dict[bytes, int] = {}  # address -> balance (non-negative ints)
_DEFAULT_SELF = b"\x00" * int(getattr(_cfg, "ADDRESS_LEN", 32))


# ------------------------------ Addr & Amount ------------------------------ #

def _check_addr(addr: bytes) -> None:
    if not isinstance(addr, (bytes, bytearray)):
        raise VmError("address must be bytes")
    alen = int(getattr(_cfg, "ADDRESS_LEN", 32))
    if len(addr) != alen:
        raise VmError(f"address must be exactly {alen} bytes")


def _to_bytes(addr: bytes | bytearray) -> bytes:
    return bytes(addr) if isinstance(addr, (bytes, bytearray)) else addr  # type: ignore[return-value]


def _check_amount(amount: int) -> None:
    if not isinstance(amount, int):
        raise VmError("amount must be int")
    if amount < 0:
        raise VmError("amount must be non-negative")
    max_bits = int(getattr(_cfg, "MAX_BALANCE_BITS", 256))
    if amount.bit_length() > max_bits:
        raise VmError(f"amount exceeds {max_bits}-bit limit")


def _add_checked(a: int, b: int) -> int:
    max_bits = int(getattr(_cfg, "MAX_BALANCE_BITS", 256))
    max_val = (1 << max_bits) - 1
    c = a + b
    if c > max_val:
        raise VmError("balance overflow")
    return c


# ---------------------------- Self Address Hook ---------------------------- #

def _resolve_self_address() -> bytes:
    """
    Try to obtain the current contract's address from runtime.context if available.
    Falls back to a zero address (all-zero bytes) for pure local simulations.
    """
    try:
        # Preferred: a helper the engine can set during call execution.
        from vm_py.runtime import context as _ctx  # type: ignore

        # Try common accessors in order of preference.
        if hasattr(_ctx, "current_contract_address"):
            addr = _ctx.current_contract_address()  # type: ignore[attr-defined]
            if isinstance(addr, (bytes, bytearray)):
                return _to_bytes(addr)
        if hasattr(_ctx, "tx_env") and hasattr(_ctx.tx_env, "to"):  # type: ignore[attr-defined]
            addr = _ctx.tx_env.to  # type: ignore[attr-defined]
            if isinstance(addr, (bytes, bytearray)):
                return _to_bytes(addr)
    except Exception:
        pass
    return _DEFAULT_SELF


# ------------------------------- Public API -------------------------------- #

def balance(addr: Optional[bytes] = None) -> int:
    """
    Return the balance for `addr`. If omitted, returns the balance of the "self" contract.
    """
    if addr is None:
        addr = _resolve_self_address()
    _check_addr(addr)
    with _L:
        return _LEDGER.get(addr, 0)


def balance_of(addr: bytes) -> int:
    """Alias for balance(addr) with explicit name for clarity."""
    return balance(addr)


def credit(addr: bytes, amount: int) -> None:
    """
    Host/testing helper: increase balance of `addr` by `amount`.
    """
    _check_addr(addr)
    _check_amount(amount)
    baddr = _to_bytes(addr)
    with _L:
        cur = _LEDGER.get(baddr, 0)
        _LEDGER[baddr] = _add_checked(cur, amount)


def debit(addr: bytes, amount: int) -> None:
    """
    Host/testing helper: decrease balance of `addr` by `amount` if sufficient.
    """
    _check_addr(addr)
    _check_amount(amount)
    baddr = _to_bytes(addr)
    with _L:
        cur = _LEDGER.get(baddr, 0)
        if amount > cur:
            raise VmError("insufficient balance")
        _LEDGER[baddr] = cur - amount


def transfer(to: bytes, amount: int) -> None:
    """
    Debit the caller (self) and credit `to` by `amount`.

    Deterministic, atomic w.r.t. this in-memory ledger.
    """
    frm = _resolve_self_address()
    _check_addr(frm)
    _check_addr(to)
    _check_amount(amount)

    bto = _to_bytes(to)
    bfrm = _to_bytes(frm)

    if amount == 0:
        return  # no-op

    with _L:
        cur_from = _LEDGER.get(bfrm, 0)
        if amount > cur_from:
            raise VmError("insufficient balance")
        # Perform debit then credit with overflow check.
        _LEDGER[bfrm] = cur_from - amount
        cur_to = _LEDGER.get(bto, 0)
        _LEDGER[bto] = _add_checked(cur_to, amount)


# ------------------------------- Test Hooks -------------------------------- #

def _reset_ledger() -> None:
    """Clear the in-memory ledger (tests only)."""
    with _L:
        _LEDGER.clear()


def _set_balance(addr: bytes, amount: int) -> None:
    """Set an exact balance for `addr` (tests only)."""
    _check_addr(addr)
    _check_amount(amount)
    with _L:
        _LEDGER[_to_bytes(addr)] = amount


__all__ = [
    "balance",
    "balance_of",
    "transfer",
    "credit",
    "debit",
    "_reset_ledger",
    "_set_balance",
]
