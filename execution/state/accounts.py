"""
execution.state.accounts — Account records and basic lifecycle helpers.

An Account holds three consensus fields:

- nonce:      u256 transaction counter (monotonically increasing)
- balance:    u256 currency amount
- code_hash:  32-byte hash of the associated code (all-zero for EOAs)

This module intentionally avoids any database concerns; callers pass in a
mutable mapping (e.g., a dict keyed by bytes addresses) when creating or
destroying accounts. Higher layers (state_db adapter, journals) can wrap these
helpers to persist/rollback.

All arithmetic is u256-bounded and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import MutableMapping, Optional

import hashlib

from execution.errors import StateConflict, ExecError
from execution.types.gas import (
    U256_MAX,
    is_u256,
    saturating_add,
)

# --------------------------------------------------------------------------- #
# Constants & helpers
# --------------------------------------------------------------------------- #

EMPTY_CODE_HASH: bytes = b"\x00" * 32


def _ensure_u256(name: str, value: int) -> int:
    if not isinstance(value, int):
        raise TypeError(f"{name} must be int")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    if not is_u256(value):
        raise OverflowError(f"{name} exceeds u256")
    return value


def compute_code_hash(code: bytes | bytearray | memoryview) -> bytes:
    """
    Compute the canonical code hash (SHA3-256) for contract code bytes.

    For EOAs or empty code, prefer `EMPTY_CODE_HASH`.
    """
    if not code:
        return EMPTY_CODE_HASH
    if not isinstance(code, (bytes, bytearray, memoryview)):
        raise TypeError("code must be bytes-like")
    return hashlib.sha3_256(bytes(code)).digest()


# --------------------------------------------------------------------------- #
# Account
# --------------------------------------------------------------------------- #

@dataclass(slots=True)
class Account:
    """
    A minimal, deterministic account record.

    Invariants:
    - nonce and balance are u256
    - code_hash is exactly 32 bytes
    """
    nonce: int = 0
    balance: int = 0
    code_hash: bytes = EMPTY_CODE_HASH

    def __post_init__(self) -> None:
        self.nonce = _ensure_u256("nonce", int(self.nonce))
        self.balance = _ensure_u256("balance", int(self.balance))
        if not isinstance(self.code_hash, (bytes, bytearray, memoryview)):
            raise TypeError("code_hash must be bytes-like")
        ch = bytes(self.code_hash)
        if len(ch) != 32:
            raise ValueError("code_hash must be 32 bytes")
        self.code_hash = ch

    # ----------------------- field operations ------------------------------ #

    def increment_nonce(self) -> None:
        """
        Increase the nonce by 1; raises StateConflict on overflow.
        """
        if self.nonce == U256_MAX:
            raise StateConflict("nonce overflow (u256 max)")
        self.nonce += 1  # safe as we checked max

    def set_nonce(self, value: int) -> None:
        """
        Set nonce explicitly (used by tests/genesis tooling).
        """
        self.nonce = _ensure_u256("nonce", int(value))

    def credit(self, amount: int) -> None:
        """
        Increase balance by `amount` (non-negative). Saturates at u256 max.
        """
        amt = _ensure_u256("amount", int(amount))
        self.balance = saturating_add(self.balance, amt, cap=U256_MAX)

    def can_debit(self, amount: int) -> bool:
        amt = _ensure_u256("amount", int(amount))
        return self.balance >= amt

    def debit(self, amount: int) -> None:
        """
        Decrease balance by `amount`; raises ExecError if insufficient.
        """
        amt = _ensure_u256("amount", int(amount))
        if self.balance < amt:
            raise ExecError("insufficient balance")
        self.balance -= amt  # safe (non-negative)

    def set_code_hash(self, code_hash: bytes) -> None:
        if not isinstance(code_hash, (bytes, bytearray, memoryview)):
            raise TypeError("code_hash must be bytes-like")
        ch = bytes(code_hash)
        if len(ch) != 32:
            raise ValueError("code_hash must be 32 bytes")
        self.code_hash = ch

    # ----------------------- (de)serialization ----------------------------- #

    def to_dict(self) -> dict:
        return {
            "nonce": self.nonce,
            "balance": self.balance,
            "code_hash": self.code_hash.hex(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        try:
            nonce = int(data["nonce"])
            balance = int(data["balance"])
            ch_hex = data["code_hash"]
            code_hash = bytes.fromhex(ch_hex) if isinstance(ch_hex, str) else bytes(ch_hex)
        except Exception as e:  # pragma: no cover - defensive
            raise ValueError(f"bad account dict: {e}") from e
        return cls(nonce=nonce, balance=balance, code_hash=code_hash)


# --------------------------------------------------------------------------- #
# Lifecycle helpers (pure, mapping-based)
# --------------------------------------------------------------------------- #

def create_account(store: MutableMapping[bytes, Account],
                   address: bytes,
                   *,
                   initial_balance: int = 0,
                   code_hash: Optional[bytes] = None) -> Account:
    """
    Create a new account in `store` at `address`.

    Raises:
        StateConflict if an account already exists at the address.
    """
    if not isinstance(address, (bytes, bytearray, memoryview)):
        raise TypeError("address must be bytes-like")
    addr = bytes(address)
    if addr in store:
        raise StateConflict("account already exists at address")
    acc = Account(
        nonce=0,
        balance=_ensure_u256("initial_balance", int(initial_balance)),
        code_hash=(EMPTY_CODE_HASH if code_hash is None else bytes(code_hash)),
    )
    store[addr] = acc
    return acc


def destroy_account(store: MutableMapping[bytes, Account],
                    address: bytes) -> Optional[Account]:
    """
    Remove and return the account at `address` if present; otherwise None.

    NOTE: Higher layers must decide what to do with any remaining balance
    (e.g., burn, transfer to beneficiary). This function does not move funds.
    """
    if not isinstance(address, (bytes, bytearray, memoryview)):
        raise TypeError("address must be bytes-like")
    return store.pop(bytes(address), None)


__all__ = [
    "Account",
    "EMPTY_CODE_HASH",
    "compute_code_hash",
    "create_account",
    "destroy_account",
]
