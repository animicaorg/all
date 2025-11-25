"""
execution.state.view — read-only typed getters over accounts & storage.

This module provides a lightweight, **read-only** facade for querying the
current state. It wraps:
- an address→Account mapping (in-memory or adapter-provided)
- a `StorageView` instance (key/value per account)

The getters never mutate state. Callers that need to write should go through
journal/state_db layers instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Mapping, Optional, Tuple

from .accounts import Account, EMPTY_CODE_HASH
from .storage import StorageView


def _as_bytes(x: bytes | bytearray | memoryview, *, name: str) -> bytes:
    if not isinstance(x, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(x)


@dataclass(slots=True)
class StateView:
    """
    Read-only view over accounts & storage.

    Parameters
    ----------
    accounts :
        Mapping of address (bytes) → Account. May be any Mapping; no mutation
        is performed by this class.
    storage :
        StorageView providing per-account key/value access.
    """
    accounts: Mapping[bytes, Account]
    storage: StorageView

    # ------------------------------ accounts ---------------------------------

    def has_account(self, address: bytes | bytearray | memoryview) -> bool:
        """True if an account exists at `address`."""
        addr = _as_bytes(address, name="address")
        return addr in self.accounts

    def balance_of(self, address: bytes | bytearray | memoryview) -> int:
        """Return current balance for `address` (0 if absent)."""
        addr = _as_bytes(address, name="address")
        acc = self.accounts.get(addr)
        return acc.balance if acc is not None else 0

    def nonce_of(self, address: bytes | bytearray | memoryview) -> int:
        """Return current nonce for `address` (0 if absent)."""
        addr = _as_bytes(address, name="address")
        acc = self.accounts.get(addr)
        return acc.nonce if acc is not None else 0

    def code_hash_of(self, address: bytes | bytearray | memoryview) -> bytes:
        """Return 32-byte code hash for `address` (all-zero if absent/EOA)."""
        addr = _as_bytes(address, name="address")
        acc = self.accounts.get(addr)
        return acc.code_hash if acc is not None else EMPTY_CODE_HASH

    def has_code(self, address: bytes | bytearray | memoryview) -> bool:
        """True if `address` has non-empty code."""
        return self.code_hash_of(address) != EMPTY_CODE_HASH

    # ------------------------------ storage ----------------------------------

    def storage_at(self,
                   address: bytes | bytearray | memoryview,
                   key: bytes | bytearray | memoryview,
                   default: bytes = b"") -> bytes:
        """
        Return the value for (address, key) or `default` if absent.

        Note: key length enforcement is delegated to StorageView (defaults to 32).
        """
        return self.storage.get(address, key, default=default)

    def has_storage_key(self,
                        address: bytes | bytearray | memoryview,
                        key: bytes | bytearray | memoryview) -> bool:
        """True if a storage key exists for `address`."""
        return self.storage.has(address, key)

    def storage_items(self, address: bytes | bytearray | memoryview) -> Iterator[Tuple[bytes, bytes]]:
        """Iterate (key, value) pairs for `address` in lexicographic key order."""
        return self.storage.items(address)

    def storage_keys(self, address: bytes | bytearray | memoryview) -> Iterator[bytes]:
        """Iterate keys for `address` in lexicographic order."""
        return self.storage.keys(address)

    def storage_values(self, address: bytes | bytearray | memoryview) -> Iterator[bytes]:
        """Iterate values for `address` ordered by their keys."""
        return self.storage.values(address)

    def storage_len(self, address: bytes | bytearray | memoryview) -> int:
        """Number of storage keys present for `address`."""
        return self.storage.account_len(address)

    # ------------------------------ meta -------------------------------------

    def __contains__(self, address: object) -> bool:  # pragma: no cover (sugar)
        try:
            return self.has_account(address)  # type: ignore[arg-type]
        except TypeError:
            return False

    def __len__(self) -> int:  # pragma: no cover (sugar)
        """Number of accounts present in the underlying mapping."""
        return len(self.accounts)


__all__ = ["StateView"]
