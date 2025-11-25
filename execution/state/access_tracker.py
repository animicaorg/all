"""
execution.state.access_tracker — track touched addresses/keys (for access lists)

Purpose
-------
During execution we want to remember every account and storage slot that was
*accessed* (read or written). This is used for:

  • Building canonical access lists to persist with receipts or to drive
    optimistic scheduling.
  • Deriving read/write locksets (addresses + storage keys) for conflict
    detection or parallel execution prototypes.

This module is deliberately standalone (pure stdlib) so it can be used in
tests and tools without pulling the whole core package.

Model
-----
We maintain disjoint read/write sets for accounts and for storage keys:

  accounts_read   : set[bytes]
  accounts_write  : set[bytes]
  code_read       : set[bytes]                  # reading account code
  storage_read    : dict[bytes, set[bytes]]     # addr -> keys
  storage_write   : dict[bytes, set[bytes]]     # addr -> keys

A write implies a read of the same item; helpers ensure the read set is
populated when a write is recorded.

Checkpoint / rollback
---------------------
The tracker supports lightweight checkpoints (for nested calls or failed
subcalls). `checkpoint()` returns a token. `commit(token)` discards the
recorded delta; `rollback(token)` reverts all additions made since that
checkpoint.

Canonicalization
----------------
- Addresses and keys are expected to be `bytes`.
- `to_access_list()` returns a canonical structure suitable for encoding:
    [
      {"address": <bytes>, "storage_keys": [<bytes>, ...]}, ...
    ]
  with lexicographic ordering by address then key.

- `to_lockset()` returns two frozensets for read/write of accounts and
  (address, key) storage tuples.

Notes
-----
We do not enforce address/key lengths here; upstream validators can do so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple


StorageSet = Dict[bytes, Set[bytes]]


@dataclass(frozen=True)
class LockSet:
    accounts_r: frozenset[bytes]
    accounts_w: frozenset[bytes]
    storage_r: frozenset[Tuple[bytes, bytes]]  # (address, key)
    storage_w: frozenset[Tuple[bytes, bytes]]


class AccessTracker:
    # ---- construction -----------------------------------------------------

    def __init__(self) -> None:
        self.accounts_read: Set[bytes] = set()
        self.accounts_write: Set[bytes] = set()
        self.code_read: Set[bytes] = set()
        self.storage_read: StorageSet = {}
        self.storage_write: StorageSet = {}

        # journal of ops for checkpoint/rollback
        self._oplog: List[Tuple[str, Tuple[bytes, ...]]] = []
        self._checkpoints: List[int] = []  # stack of oplog indices

    # ---- checkpointing ----------------------------------------------------

    def checkpoint(self) -> int:
        """
        Start a checkpoint; returns a token (internal oplog index).
        Use with try/finally or context manager style:

            t = tracker.checkpoint()
            try:
                ...
            except:
                tracker.rollback(t)
                raise
            else:
                tracker.commit(t)
        """
        token = len(self._oplog)
        self._checkpoints.append(token)
        return token

    def commit(self, token: int) -> None:
        """Discard operations tracked since `token` (keep their effects)."""
        self._expect_token(token)
        # Drop the marker but keep oplog entries for possible outer checkpoints
        self._checkpoints.pop()

    def rollback(self, token: int) -> None:
        """Undo operations recorded since `token`."""
        self._expect_token(token)
        try:
            while len(self._oplog) > token:
                op, args = self._oplog.pop()
                if op == "ar":
                    self._set_remove(self.accounts_read, args[0])
                elif op == "aw":
                    self._set_remove(self.accounts_write, args[0])
                elif op == "cr":
                    self._set_remove(self.code_read, args[0])
                elif op == "sr":
                    addr, key = args
                    self._dictset_remove(self.storage_read, addr, key)
                elif op == "sw":
                    addr, key = args
                    self._dictset_remove(self.storage_write, addr, key)
                else:  # pragma: no cover
                    raise RuntimeError(f"unknown op {op}")
        finally:
            self._checkpoints.pop()

    def _expect_token(self, token: int) -> None:
        if not self._checkpoints or self._checkpoints[-1] != token:
            raise RuntimeError("checkpoint token mismatch or out-of-order commit/rollback")

    # ---- record helpers ---------------------------------------------------

    def touch_account_read(self, address: bytes) -> None:
        if self._set_add(self.accounts_read, address):
            self._oplog.append(("ar", (address,)))

    def touch_account_write(self, address: bytes) -> None:
        # a write implies a read
        self.touch_account_read(address)
        if self._set_add(self.accounts_write, address):
            self._oplog.append(("aw", (address,)))

    def touch_code_read(self, address: bytes) -> None:
        if self._set_add(self.code_read, address):
            self._oplog.append(("cr", (address,)))

    def touch_storage_read(self, address: bytes, key: bytes) -> None:
        if self._dictset_add(self.storage_read, address, key):
            self._oplog.append(("sr", (address, key)))

    def touch_storage_write(self, address: bytes, key: bytes) -> None:
        # a write implies a read of that slot
        self.touch_storage_read(address, key)
        if self._dictset_add(self.storage_write, address, key):
            self._oplog.append(("sw", (address, key)))

    # convenience aliases
    account_r = touch_account_read
    account_w = touch_account_write
    code_r = touch_code_read
    slot_r = touch_storage_read
    slot_w = touch_storage_write

    # ---- merge / clear ----------------------------------------------------

    def merge(self, other: "AccessTracker") -> None:
        """
        In-place union with another tracker (no oplog entries are recorded).
        Useful when combining per-tx trackers into a block-level view.
        """
        self.accounts_read |= other.accounts_read
        self.accounts_write |= other.accounts_write
        self.code_read |= other.code_read
        self._merge_storage(self.storage_read, other.storage_read)
        self._merge_storage(self.storage_write, other.storage_write)

    def clear(self) -> None:
        """Reset all tracked accesses and checkpoints."""
        self.accounts_read.clear()
        self.accounts_write.clear()
        self.code_read.clear()
        self.storage_read.clear()
        self.storage_write.clear()
        self._oplog.clear()
        self._checkpoints.clear()

    # ---- exports ----------------------------------------------------------

    def to_access_list(self) -> List[dict]:
        """
        Return a canonical access list (addresses + storage keys) with sorted
        addresses and storage keys. Storage keys include both read & write slots.
        Accounts show up if they were touched in any way (read/write/code/slot).
        """
        addresses: Set[bytes] = set(self.accounts_read) | set(self.accounts_write) | set(self.code_read)
        addresses |= set(self.storage_read.keys()) | set(self.storage_write.keys())

        def sorted_bytes(items: Iterable[bytes]) -> List[bytes]:
            return sorted(items)

        # union of storage keys per address
        entries: List[dict] = []
        for addr in sorted_bytes(addresses):
            skeys: Set[bytes] = set()
            if addr in self.storage_read:
                skeys |= self.storage_read[addr]
            if addr in self.storage_write:
                skeys |= self.storage_write[addr]
            entries.append({"address": addr, "storage_keys": sorted_bytes(skeys)})
        return entries

    def to_lockset(self) -> LockSet:
        """
        Export disjoint read/write locksets.
        - Account writes ⊆ accounts_w; account reads ⊆ accounts_r ∪ accounts_w.
        - Storage writes/reads are exported as (address, key) tuples.
        """
        # account read set includes write set (reads are implied)
        acc_r = frozenset(self.accounts_read | self.accounts_write)
        acc_w = frozenset(self.accounts_write)

        def pairs(d: StorageSet) -> Set[Tuple[bytes, bytes]]:
            return {(a, k) for a, keys in d.items() for k in keys}

        stor_w = pairs(self.storage_write)
        stor_r = pairs(self.storage_read) | stor_w  # writes imply reads
        return LockSet(acc_r, acc_w, frozenset(stor_r), frozenset(stor_w))

    # ---- internals: set/dict-of-set ops ----------------------------------

    @staticmethod
    def _set_add(s: Set[bytes], v: bytes) -> bool:
        if v in s:
            return False
        s.add(v)
        return True

    @staticmethod
    def _set_remove(s: Set[bytes], v: bytes) -> None:
        s.discard(v)

    @staticmethod
    def _dictset_add(d: StorageSet, k: bytes, v: bytes) -> bool:
        s = d.get(k)
        if s is None:
            d[k] = {v}
            return True
        if v in s:
            return False
        s.add(v)
        return True

    @staticmethod
    def _dictset_remove(d: StorageSet, k: bytes, v: bytes) -> None:
        s = d.get(k)
        if not s:
            return
        s.discard(v)
        if not s:
            d.pop(k, None)

    @staticmethod
    def _merge_storage(dst: StorageSet, src: StorageSet) -> None:
        for a, keys in src.items():
            if a not in dst:
                dst[a] = set(keys)
            else:
                dst[a] |= keys


__all__ = [
    "AccessTracker",
    "LockSet",
]
