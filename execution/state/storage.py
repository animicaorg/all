"""
execution.state.storage — per-account storage (key/value)

A minimal, deterministic key/value storage view keyed by account address
(`bytes`) and storage key (`bytes`) with `bytes` values. This module is
backend-agnostic: it wraps a plain in-memory mapping but can be adapted by
higher layers (e.g., a DB-backed state_db) and journal/snapshot subsystems.

Design goals
------------
- Pure Python, no I/O; deterministic semantics.
- Bytes-in / bytes-out API (addresses, keys, values are bytes-like).
- Canonicalization: all inputs are copied to immutable `bytes`.
- "Zero means absent": storing an empty value deletes the key (canonical form).
- Optional fixed key length enforcement (default 32 bytes, configurable).

Typical usage
-------------
    sv = StorageView()
    sv.set(addr, key, b"value")
    value = sv.get(addr, key)  # b"value" or default (b"" by default)
    sv.delete(addr, key)
    sv.clear_account(addr)

This module does not perform hashing of keys or addresses; callers should pass
already-canonical identifiers. Higher layers (access trackers, journals, DB
adapters) can be layered on top without changing this API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Dict, Iterable, Iterator, Mapping, MutableMapping,
                    Optional, Tuple)

# ------------------------------- helpers -------------------------------------


def _as_bytes(x: bytes | bytearray | memoryview, *, name: str) -> bytes:
    if not isinstance(x, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    return bytes(x)


def _check_len(x: bytes, *, expected: Optional[int], what: str) -> None:
    if expected is not None and len(x) != expected:
        raise ValueError(f"{what} must be exactly {expected} bytes (got {len(x)})")


# ------------------------------- StorageView ---------------------------------


@dataclass
class StorageView:
    """
    A per-account key/value store.

    Parameters
    ----------
    backend :
        Optional external mapping to store state. If not provided, an internal
        dict is used. The shape is {address: {key: value}} with all entries as
        `bytes`.
    key_len :
        If not None (default 32), enforce that all storage keys are exactly this
        length in bytes. Set to None to allow arbitrary key lengths.
    track_touches :
        Optional callback `(addr: bytes, key: bytes) -> None` invoked on set/get
        and delete. Useful for access-list tracking; ignored if None.
    """
    backend: Optional[MutableMapping[bytes, Dict[bytes, bytes]]] = None
    key_len: Optional[int] = 32
    track_touches: Optional[callable] = None

    # Internal storage (initialized on first use if backend is None)
    _store: MutableMapping[bytes, Dict[bytes, bytes]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._store = self.backend if self.backend is not None else {}  # type: ignore[assignment]

    # ------------------------------ core ops --------------------------------

    def get(self, address: bytes | bytearray | memoryview,
            key: bytes | bytearray | memoryview,
            default: bytes = b"") -> bytes:
        """
        Return the value for (address, key), or `default` if absent.
        """
        addr_b = _as_bytes(address, name="address")
        key_b = _as_bytes(key, name="key")
        _check_len(key_b, expected=self.key_len, what="storage key")
        if self.track_touches:
            self.track_touches(addr_b, key_b)
        return self._store.get(addr_b, {}).get(key_b, default)

    def has(self, address: bytes | bytearray | memoryview,
            key: bytes | bytearray | memoryview) -> bool:
        addr_b = _as_bytes(address, name="address")
        key_b = _as_bytes(key, name="key")
        _check_len(key_b, expected=self.key_len, what="storage key")
        return key_b in self._store.get(addr_b, {})

    def set(self, address: bytes | bytearray | memoryview,
            key: bytes | bytearray | memoryview,
            value: bytes | bytearray | memoryview) -> None:
        """
        Set value for (address, key). An empty value deletes the key (canonical).
        """
        addr_b = _as_bytes(address, name="address")
        key_b = _as_bytes(key, name="key")
        _check_len(key_b, expected=self.key_len, what="storage key")
        val_b = _as_bytes(value, name="value")

        if self.track_touches:
            self.track_touches(addr_b, key_b)

        if len(val_b) == 0:
            # Canonical deletion for "zero" values
            acc = self._store.get(addr_b)
            if acc is not None:
                acc.pop(key_b, None)
                if not acc:
                    # Drop empty account bucket to keep memory tidy
                    self._store.pop(addr_b, None)
            return

        acc = self._store.get(addr_b)
        if acc is None:
            acc = {}
            self._store[addr_b] = acc
        acc[key_b] = val_b

    def delete(self, address: bytes | bytearray | memoryview,
               key: bytes | bytearray | memoryview) -> bool:
        """
        Delete (address, key). Returns True if a key existed and was removed.
        """
        addr_b = _as_bytes(address, name="address")
        key_b = _as_bytes(key, name="key")
        _check_len(key_b, expected=self.key_len, what="storage key")
        if self.track_touches:
            self.track_touches(addr_b, key_b)
        acc = self._store.get(addr_b)
        if acc is None:
            return False
        removed = acc.pop(key_b, None) is not None
        if not acc:
            self._store.pop(addr_b, None)
        return removed

    # ------------------------------ account ops -----------------------------

    def items(self, address: bytes | bytearray | memoryview) -> Iterator[Tuple[bytes, bytes]]:
        """
        Iterate (key, value) pairs for an address. Stable order: lexicographic by key.
        """
        addr_b = _as_bytes(address, name="address")
        acc = self._store.get(addr_b, {})
        for k in sorted(acc.keys()):
            yield k, acc[k]

    def keys(self, address: bytes | bytearray | memoryview) -> Iterator[bytes]:
        addr_b = _as_bytes(address, name="address")
        yield from (k for k, _ in self.items(addr_b))

    def values(self, address: bytes | bytearray | memoryview) -> Iterator[bytes]:
        addr_b = _as_bytes(address, name="address")
        yield from (v for _, v in self.items(addr_b))

    def clear_account(self, address: bytes | bytearray | memoryview) -> int:
        """
        Remove all storage for an address. Returns number of keys removed.
        """
        addr_b = _as_bytes(address, name="address")
        acc = self._store.pop(addr_b, None)
        if acc is None:
            return 0
        n = len(acc)
        acc.clear()
        return n

    def account_len(self, address: bytes | bytearray | memoryview) -> int:
        """
        Number of keys present for the address.
        """
        addr_b = _as_bytes(address, name="address")
        return len(self._store.get(addr_b, {}))

    # ------------------------------ export/import ---------------------------

    def export_account_hex(self, address: bytes | bytearray | memoryview) -> Dict[str, str]:
        """
        Export an account's storage as a {key_hex: value_hex} dict (sorted by key).
        """
        addr_b = _as_bytes(address, name="address")
        acc = self._store.get(addr_b, {})
        return {k.hex(): acc[k].hex() for k in sorted(acc.keys())}

    def import_account_hex(self,
                           address: bytes | bytearray | memoryview,
                           data: Mapping[str, str]) -> None:
        """
        Import storage from a {key_hex: value_hex} mapping, replacing existing keys.
        """
        addr_b = _as_bytes(address, name="address")
        if addr_b not in self._store:
            self._store[addr_b] = {}
        acc = self._store[addr_b]
        acc.clear()
        for k_hex, v_hex in data.items():
            k = bytes.fromhex(k_hex)
            _check_len(k, expected=self.key_len, what="storage key")
            v = bytes.fromhex(v_hex)
            if len(v) == 0:
                continue  # canonical deletion
            acc[k] = v

    # ------------------------------ diagnostics -----------------------------

    def total_keys(self) -> int:
        """Total number of keys across all accounts."""
        return sum(len(acc) for acc in self._store.values())

    def __repr.me__(self) -> str:  # pragma: no cover (human-only)
        return f"StorageView(accounts={len(self._store)}, total_keys={self.total_keys()}, key_len={self.key_len})"


__all__ = [
    "StorageView",
]
