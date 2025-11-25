"""
execution.adapters.state_db — bridge to core.db.state_db

This adapter provides a small, typed, and test-friendly facade over the
state database used by the execution engine. It avoids a hard dependency on a
specific core.db.state_db implementation by relying on a minimal protocol
(duck-typed interface). It also exposes a WriteBatch context manager so the
executor can apply many mutations atomically and deterministically.

Expected underlying DB capabilities
-----------------------------------
The wrapped object (``core_state``) should provide (duck-typed):

    get_balance(addr: bytes) -> int
    get_nonce(addr: bytes) -> int
    get_code(addr: bytes) -> bytes
    get_storage(addr: bytes, key: bytes) -> bytes

    set_balance(addr: bytes, value: int) -> None
    set_nonce(addr: bytes, value: int) -> None
    set_code(addr: bytes, code: bytes) -> None
    set_storage(addr: bytes, key: bytes, value: bytes) -> None

Optionally (if available), the adapter will use snapshot/commit/revert:

    snapshot() -> object
    revert(snap_id: object) -> None
    commit() -> None

If those are not available, WriteBatch will still apply operations in-order.

Design notes
------------
• All addresses and storage keys are bytes; balances and nonces are non-negative ints.
• This module performs only shallow validation (types and non-negativity).
• Arithmetic checks for balance underflow are handled by higher layers, but
  the adapter provides a safe `add_balance` helper used by those layers.

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple, runtime_checkable, Literal, Any
import contextlib


# ----------------------------- public exceptions ------------------------------

class StateAdapterError(Exception):
    """Base error for the state-db adapter."""


class NegativeValue(StateAdapterError):
    """Raised when a negative balance/nonce is set or computed."""


class InsufficientBalance(StateAdapterError):
    """Raised when an operation would underflow an account balance."""


# ---------------------------- core-db minimal protocol ------------------------

@runtime_checkable
class _CoreStateDB(Protocol):
    # reads
    def get_balance(self, address: bytes) -> int: ...
    def get_nonce(self, address: bytes) -> int: ...
    def get_code(self, address: bytes) -> bytes: ...
    def get_storage(self, address: bytes, key: bytes) -> bytes: ...
    # writes
    def set_balance(self, address: bytes, value: int) -> None: ...
    def set_nonce(self, address: bytes, value: int) -> None: ...
    def set_code(self, address: bytes, code: bytes) -> None: ...
    def set_storage(self, address: bytes, key: bytes, value: bytes) -> None: ...
    # optional txn-ish APIs
    def snapshot(self) -> object: ...
    def revert(self, snap_id: object) -> None: ...
    def commit(self) -> None: ...


# ------------------------------- read facade ----------------------------------

class StateReader:
    """Read-only facade over the state database."""

    def __init__(self, core_state: _CoreStateDB):
        if not isinstance(core_state, _CoreStateDB):  # runtime duck check (best-effort)
            # Protocol check can be overly strict at runtime; tolerate and trust.
            pass
        self._db = core_state

    # Single reads -------------------------------------------------------------

    def balance(self, address: bytes) -> int:
        v = self._db.get_balance(address)
        if v < 0:
            raise NegativeValue("balance must be non-negative")
        return v

    def nonce(self, address: bytes) -> int:
        v = self._db.get_nonce(address)
        if v < 0:
            raise NegativeValue("nonce must be non-negative")
        return v

    def code(self, address: bytes) -> bytes:
        return self._db.get_code(address)

    def storage(self, address: bytes, key: bytes) -> bytes:
        return self._db.get_storage(address, key)

    # Batch reads --------------------------------------------------------------

    def balances(self, addresses: Iterable[bytes]) -> Dict[bytes, int]:
        return {addr: self.balance(addr) for addr in addresses}

    def nonces(self, addresses: Iterable[bytes]) -> Dict[bytes, int]:
        return {addr: self.nonce(addr) for addr in addresses}

    def storages(self, address: bytes, keys: Iterable[bytes]) -> Dict[bytes, bytes]:
        return {k: self.storage(address, k) for k in keys}


# ------------------------------ write batching --------------------------------

@dataclass(frozen=True)
class _Op:
    kind: Literal["set_balance", "add_balance", "set_nonce", "inc_nonce", "set_code", "set_storage"]
    address: bytes
    # fields reused depending on op
    value_int: Optional[int] = None
    key: Optional[bytes] = None
    value_bytes: Optional[bytes] = None


class WriteBatch:
    """
    Collect state mutations and apply them in-order.

    Usage:
        with StateWriter(db).begin() as wb:
            wb.set_balance(addr, 123)
            wb.set_storage(addr, key, b"value")
            wb.apply()  # optional; also called on __exit__ if no error
    """

    __slots__ = ("_db", "_ops", "_snap_id", "_closed")

    def __init__(self, core_state: _CoreStateDB):
        self._db = core_state
        self._ops: List[_Op] = []
        self._snap_id: Optional[object] = None
        self._closed = False

    # mutation builders --------------------------------------------------------

    def set_balance(self, address: bytes, value: int) -> None:
        if value < 0:
            raise NegativeValue("balance cannot be negative")
        self._ops.append(_Op("set_balance", address, value_int=value))

    def add_balance(self, address: bytes, delta: int) -> None:
        # delta may be negative, but final balance must be >= 0
        self._ops.append(_Op("add_balance", address, value_int=delta))

    def set_nonce(self, address: bytes, value: int) -> None:
        if value < 0:
            raise NegativeValue("nonce cannot be negative")
        self._ops.append(_Op("set_nonce", address, value_int=value))

    def inc_nonce(self, address: bytes, by: int = 1) -> None:
        if by < 0:
            raise NegativeValue("nonce increment cannot be negative")
        self._ops.append(_Op("inc_nonce", address, value_int=by))

    def set_code(self, address: bytes, code: bytes) -> None:
        self._ops.append(_Op("set_code", address, value_bytes=code))

    def set_storage(self, address: bytes, key: bytes, value: bytes) -> None:
        self._ops.append(_Op("set_storage", address, key=key, value_bytes=value))

    # apply / lifecycle --------------------------------------------------------

    def apply(self) -> None:
        """Apply operations in-order, using snapshot/revert if available."""
        if self._closed:
            return
        # Take snapshot if db supports it
        snap = getattr(self._db, "snapshot", None)
        revert = getattr(self._db, "revert", None)
        commit = getattr(self._db, "commit", None)

        if callable(snap) and callable(revert):
            self._snap_id = snap()  # type: ignore[misc]

        try:
            for op in self._ops:
                if op.kind == "set_balance":
                    self._db.set_balance(op.address, int(op.value_int))  # type: ignore[arg-type]
                elif op.kind == "add_balance":
                    new_bal = self._db.get_balance(op.address) + int(op.value_int)  # type: ignore[arg-type]
                    if new_bal < 0:
                        raise InsufficientBalance("balance underflow in add_balance")
                    self._db.set_balance(op.address, new_bal)
                elif op.kind == "set_nonce":
                    self._db.set_nonce(op.address, int(op.value_int))  # type: ignore[arg-type]
                elif op.kind == "inc_nonce":
                    new_nonce = self._db.get_nonce(op.address) + int(op.value_int)  # type: ignore[arg-type]
                    if new_nonce < 0:
                        raise NegativeValue("nonce became negative")
                    self._db.set_nonce(op.address, new_nonce)
                elif op.kind == "set_code":
                    self._db.set_code(op.address, op.value_bytes or b"")
                elif op.kind == "set_storage":
                    self._db.set_storage(op.address, op.key or b"", op.value_bytes or b"")
                else:  # pragma: no cover - defensive
                    raise StateAdapterError(f"unknown op kind: {op.kind}")
        except Exception:
            # Roll back if we can
            if self._snap_id is not None and callable(revert):
                revert(self._snap_id)  # type: ignore[misc]
            raise
        else:
            if callable(commit):
                # Let DB finalize any transactional state. If commit() is not
                # supported, the operations were already applied above.
                commit()  # type: ignore[misc]
            self._ops.clear()

    # context manager ----------------------------------------------------------

    def __enter__(self) -> "WriteBatch":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc is None:
                self.apply()
        finally:
            self._closed = True
            self._ops.clear()
            self._snap_id = None


# ------------------------------- write facade ---------------------------------

class StateWriter(StateReader):
    """Read+write facade. Use `begin()` to collect and apply mutations."""

    def begin(self) -> WriteBatch:
        return WriteBatch(self._db)

    # Convenience single-op helpers (apply immediately) -----------------------

    def set_balance(self, address: bytes, value: int) -> None:
        if value < 0:
            raise NegativeValue("balance cannot be negative")
        self._db.set_balance(address, value)

    def add_balance(self, address: bytes, delta: int) -> None:
        new_bal = self.balance(address) + delta
        if new_bal < 0:
            raise InsufficientBalance("balance underflow")
        self._db.set_balance(address, new_bal)

    def set_nonce(self, address: bytes, value: int) -> None:
        if value < 0:
            raise NegativeValue("nonce cannot be negative")
        self._db.set_nonce(address, value)

    def inc_nonce(self, address: bytes, by: int = 1) -> None:
        if by < 0:
            raise NegativeValue("nonce increment cannot be negative")
        self._db.set_nonce(address, self.nonce(address) + by)

    def set_code(self, address: bytes, code: bytes) -> None:
        self._db.set_code(address, code)

    def set_storage(self, address: bytes, key: bytes, value: bytes) -> None:
        self._db.set_storage(address, key, value)


__all__ = [
    "StateReader",
    "StateWriter",
    "WriteBatch",
    "StateAdapterError",
    "NegativeValue",
    "InsufficientBalance",
]
