from __future__ import annotations

"""
State DB (accounts, storage, code) on top of the KV interface
=============================================================

This module provides a typed view over the generic KV backends (SQLite/RocksDB).
It implements a compact binary key layout that supports efficient prefix scans,
plus helpers for account balances/nonces, contract code, and per-account storage.

Key layout (all little-endian length prefixes are single-byte for simplicity)
-----------------------------------------------------------------------------

    ACC  := 0x01 | addr_len:u8 | addr:bytes                  -> cbor(Account)
    CODE := 0x02 | addr_len:u8 | addr:bytes                  -> raw code bytes
    STO  := 0x03 | addr_len:u8 | addr:bytes | key_len:u8 | key:bytes -> raw value bytes

Notes
- Variable-length addresses (20–64 bytes) are supported. We record addr_len to allow
  reversible parsing during scans.
- Storage keys/values are arbitrary bytes. For contracts, use deterministic encoding
  (e.g., vm_py stdlib ABI helpers) above this layer.
- Values:
    * Account is encoded as canonical CBOR: {nonce:int, balance:int, code_hash:bytes?}
    * Code is stored raw; code_hash is the SHA3-256(code) cached in Account.
- This module is concurrency-safe at the KV layer granularity. For snapshot iteration,
  use `snapshot()` which materializes a consistent view (point-in-time copy).

Dependencies
------------
- core.db.kv: KV/ReadOnlyKV/Batch protocols
- core.encoding.cbor: canonical CBOR encoder/decoder
- core.utils.hash: sha3_256 for code hashes (optional)

"""

from dataclasses import dataclass
from typing import Iterator, Optional, Tuple, Dict, Any, Iterable, List, Union

import math

from .kv import KV, ReadOnlyKV, Batch
from ..encoding.cbor import cbor_dumps, cbor_loads
from ..utils.hash import sha3_256

# ---------------------------------------------------------------------------
# Internal key helpers
# ---------------------------------------------------------------------------

PFX_ACC  = b"\x01"
PFX_CODE = b"\x02"
PFX_STO  = b"\x03"

def _u8(n: int) -> bytes:
    if not (0 <= n <= 255):
        raise ValueError("length out of range for u8")
    return bytes((n,))

def _split_len_prefixed(buf: bytes, off: int = 0) -> Tuple[bytes, int]:
    """Parse len:u8 + payload; return (payload, new_offset)."""
    if off >= len(buf):
        raise ValueError("offset past end while parsing len-prefixed segment")
    ln = buf[off]
    start = off + 1
    end = start + ln
    if end > len(buf):
        raise ValueError("segment length exceeds buffer")
    return buf[start:end], end

def _k_acc(addr: bytes) -> bytes:
    return PFX_ACC + _u8(len(addr)) + addr

def _k_code(addr: bytes) -> bytes:
    return PFX_CODE + _u8(len(addr)) + addr

def _k_sto_prefix(addr: bytes) -> bytes:
    # Prefix for all storage keys under an address (no storage key appended yet)
    return PFX_STO + _u8(len(addr)) + addr

def _k_sto(addr: bytes, skey: bytes) -> bytes:
    return _k_sto_prefix(addr) + _u8(len(skey)) + skey

def _parse_acc_key(key: bytes) -> bytes:
    if not key.startswith(PFX_ACC):
        raise ValueError("not an ACC key")
    addr, _ = _split_len_prefixed(key, 1)
    return addr

def _parse_sto_key(key: bytes) -> Tuple[bytes, bytes]:
    if not key.startswith(PFX_STO):
        raise ValueError("not a STO key")
    addr, off = _split_len_prefixed(key, 1)
    skey, _ = _split_len_prefixed(key, off)
    return addr, skey

# ---------------------------------------------------------------------------
# Account model
# ---------------------------------------------------------------------------

@dataclass
class Account:
    nonce: int
    balance: int
    code_hash: Optional[bytes] = None  # 32 bytes if present

    def to_cbor(self) -> bytes:
        m: Dict[str, Any] = {"nonce": int(self.nonce), "balance": int(self.balance)}
        if self.code_hash is not None:
            m["code_hash"] = bytes(self.code_hash)
        return cbor_dumps(m)

    @staticmethod
    def from_cbor(data: bytes) -> "Account":
        m = cbor_loads(data)
        nonce = int(m.get("nonce", 0))
        balance = int(m.get("balance", 0))
        ch = m.get("code_hash", None)
        ch_b: Optional[bytes] = None if ch is None else bytes(ch)
        return Account(nonce=nonce, balance=balance, code_hash=ch_b)

# ---------------------------------------------------------------------------
# State DB view
# ---------------------------------------------------------------------------

class StateDB:
    """
    High-level state view on top of KV.

    This object is lightweight. It holds a reference to the underlying KV; callers
    are responsible for its lifetime. Use `batch()` when mutating multiple keys.
    """

    def __init__(self, kv: KV):
        self.kv = kv

    # --- Accounts ---

    def get_account(self, addr: bytes) -> Optional[Account]:
        v = self.kv.get(_k_acc(addr))
        return None if v is None else Account.from_cbor(v)

    def put_account(self, addr: bytes, acc: Account, batch: Optional[Batch]=None) -> None:
        k = _k_acc(addr)
        v = acc.to_cbor()
        if batch is None:
            self.kv.put(k, v)
        else:
            batch.put(k, v)

    def ensure_account(self, addr: bytes) -> Account:
        acc = self.get_account(addr)
        if acc is None:
            acc = Account(nonce=0, balance=0, code_hash=None)
            self.put_account(addr, acc)
        return acc

    def set_balance(self, addr: bytes, amount: int, batch: Optional[Batch]=None) -> None:
        acc = self.ensure_account(addr)
        acc.balance = int(amount)
        self.put_account(addr, acc, batch=batch)

    def add_balance(self, addr: bytes, delta: int, batch: Optional[Batch]=None) -> int:
        acc = self.ensure_account(addr)
        new_balance = acc.balance + int(delta)
        if new_balance < 0:
            raise ValueError("negative balance")
        acc.balance = new_balance
        self.put_account(addr, acc, batch=batch)
        return new_balance

    def get_balance(self, addr: bytes) -> int:
        acc = self.get_account(addr)
        return 0 if acc is None else acc.balance

    def get_nonce(self, addr: bytes) -> int:
        acc = self.get_account(addr)
        return 0 if acc is None else acc.nonce

    def set_nonce(self, addr: bytes, nonce: int, batch: Optional[Batch]=None) -> None:
        if nonce < 0:
            raise ValueError("negative nonce")
        acc = self.ensure_account(addr)
        acc.nonce = int(nonce)
        self.put_account(addr, acc, batch=batch)

    def inc_nonce(self, addr: bytes, batch: Optional[Batch]=None) -> int:
        acc = self.ensure_account(addr)
        acc.nonce += 1
        self.put_account(addr, acc, batch=batch)
        return acc.nonce

    # --- Code (contract bytecode) ---

    def get_code(self, addr: bytes) -> Optional[bytes]:
        return self.kv.get(_k_code(addr))

    def set_code(self, addr: bytes, code: bytes, batch: Optional[Batch]=None) -> bytes:
        chash = sha3_256(code)
        kcode = _k_code(addr)
        if batch is None:
            self.kv.put(kcode, code)
        else:
            batch.put(kcode, code)
        acc = self.ensure_account(addr)
        acc.code_hash = chash
        self.put_account(addr, acc, batch=batch)
        return chash

    def delete_code(self, addr: bytes, batch: Optional[Batch]=None) -> None:
        if batch is None:
            self.kv.delete(_k_code(addr))
        else:
            batch.delete(_k_code(addr))
        acc = self.ensure_account(addr)
        acc.code_hash = None
        self.put_account(addr, acc, batch=batch)

    # --- Storage (per-account key/value) ---

    def get_storage(self, addr: bytes, key: bytes) -> Optional[bytes]:
        return self.kv.get(_k_sto(addr, key))

    def set_storage(self, addr: bytes, key: bytes, value: bytes, batch: Optional[Batch]=None) -> None:
        k = _k_sto(addr, key)
        if batch is None:
            self.kv.put(k, value)
        else:
            batch.put(k, value)

    def delete_storage(self, addr: bytes, key: bytes, batch: Optional[Batch]=None) -> None:
        k = _k_sto(addr, key)
        if batch is None:
            self.kv.delete(k)
        else:
            batch.delete(k)

    def iter_storage(self, addr: Optional[bytes]=None) -> Iterator[Tuple[bytes, bytes, bytes]]:
        """
        Iterate storage entries.

        If addr is provided → yields (addr, key, value) for that account only.
        If addr is None     → yields for all accounts.

        WARNING: This reflects live state; for a stable view use snapshot().iter_storage().
        """
        if addr is None:
            # global scan: prefix = PFX_STO
            for k, v in self.kv.iter_prefix(PFX_STO):
                a, skey = _parse_sto_key(k)
                yield a, skey, v
        else:
            p = _k_sto_prefix(addr)
            for k, v in self.kv.iter_prefix(p):
                # k = p | key_len | key
                _, skey = _parse_sto_key(k)
                yield addr, skey, v

    # --- Accounts iteration ---

    def iter_accounts(self) -> Iterator[Tuple[bytes, Account]]:
        """
        Live iterator over all accounts (addr, Account) pairs.

        WARNING: This reflects live state; for a stable view use snapshot().iter_accounts().
        """
        for k, v in self.kv.iter_prefix(PFX_ACC):
            addr = _parse_acc_key(k)
            yield addr, Account.from_cbor(v)

    # --- Batching & close ---

    def batch(self) -> Batch:
        return self.kv.batch()

    def close(self) -> None:
        self.kv.close()

    # --- Snapshot ---

    def snapshot(self) -> "StateSnapshot":
        """
        Create a point-in-time materialized snapshot (accounts + storage). This
        copies keys/values into memory for deterministic iteration independent
        of concurrent writes.
        """
        acc_entries: List[Tuple[bytes, Account]] = []
        for k, v in self.kv.iter_prefix(PFX_ACC):
            addr = _parse_acc_key(k)
            acc_entries.append((addr, Account.from_cbor(v)))

        sto_entries: List[Tuple[bytes, bytes, bytes]] = []
        for k, v in self.kv.iter_prefix(PFX_STO):
            addr, skey = _parse_sto_key(k)
            sto_entries.append((addr, skey, bytes(v)))

        code_entries: List[Tuple[bytes, bytes]] = []
        for k, v in self.kv.iter_prefix(PFX_CODE):
            # mirror _parse_acc_key logic for code (same prefix layout)
            if not k.startswith(PFX_CODE):
                continue
            addr, _ = _split_len_prefixed(k, 1)
            code_entries.append((addr, bytes(v)))

        return StateSnapshot(acc_entries, sto_entries, code_entries)


class StateSnapshot:
    """
    Immutable, in-memory copy of a subset of state at a point in time.
    """

    __slots__ = ("_acc", "_sto", "_code")

    def __init__(
        self,
        acc_entries: List[Tuple[bytes, Account]],
        sto_entries: List[Tuple[bytes, bytes, bytes]],
        code_entries: List[Tuple[bytes, bytes]],
    ) -> None:
        self._acc  = acc_entries
        self._sto  = sto_entries
        self._code = code_entries

    # Iterators over frozen content
    def iter_accounts(self) -> Iterator[Tuple[bytes, Account]]:
        yield from self._acc

    def iter_storage(self, addr: Optional[bytes]=None) -> Iterator[Tuple[bytes, bytes, bytes]]:
        if addr is None:
            yield from self._sto
        else:
            for a, k, v in self._sto:
                if a == addr:
                    yield (a, k, v)

    def get_code(self, addr: bytes) -> Optional[bytes]:
        for a, code in self._code:
            if a == addr:
                return code
        return None

    # Convenience: compute a deterministic "state digest" over snapshot
    def digest(self) -> bytes:
        """
        Compute a simple digest over (accounts, storage, code) for sanity checks.
        Not a consensus state root; use core/chain/state_root for that.
        """
        h = sha3_256(b"")
        # Accounts ordered by address bytes
        for addr, acc in sorted(self._acc, key=lambda t: t[0]):
            h = sha3_256(h + b"A" + addr + acc.to_cbor())
        # Storage ordered by (addr, key)
        for addr, key, val in sorted(self._sto, key=lambda t: (t[0], t[1])):
            h = sha3_256(h + b"S" + addr + key + val)
        # Code ordered by address
        for addr, code in sorted(self._code, key=lambda t: t[0]):
            h = sha3_256(h + b"C" + addr + code)
        return h

# ---------------------------------------------------------------------------
# Convenience helpers for typed storage (optional sugar)
# ---------------------------------------------------------------------------

def storage_get_int(state: StateDB, addr: bytes, key: bytes, default: int = 0) -> int:
    v = state.get_storage(addr, key)
    if v is None:
        return default
    # big-endian unsigned int
    return int.from_bytes(v, "big", signed=False)

def storage_set_int(state: StateDB, addr: bytes, key: bytes, value: int, batch: Optional[Batch]=None) -> None:
    if value < 0:
        raise ValueError("negative value not supported for unsigned int storage")
    b = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    state.set_storage(addr, key, b, batch=batch)

__all__ = [
    "StateDB",
    "StateSnapshot",
    "Account",
    "storage_get_int",
    "storage_set_int",
]
