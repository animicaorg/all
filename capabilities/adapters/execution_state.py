"""
capabilities.adapters.execution_state
====================================

Thin, dependency-light bridge to the node's execution state, wrapping the
adapter provided by ``execution.adapters.state_db`` (preferred), and
falling back to any object that quacks like a state DB (duck-typed).

This module is intentionally small: capabilities/ only needs a narrow set
of primitives to (optionally) reflect off-chain capability accounting into
on-chain state, or to read contract-related storage for diagnostics.

Supported backends (in order):

  1) An explicit backend object passed to `ExecutionState(backend=...)`
     exposing methods (any subset is fine; missing ones will raise):
        - get_balance(addr: bytes) -> int
        - set_balance(addr: bytes, value: int) -> None
        - get_nonce(addr: bytes) -> int
        - set_nonce(addr: bytes, value: int) -> None
        - get_storage(addr: bytes, key: bytes) -> bytes
        - set_storage(addr: bytes, key: bytes, value: bytes) -> None
        - begin() -> tx; commit(tx) -> None; rollback(tx) -> None
          (alternatively: batch() -> context manager)

  2) A constructed adapter from `execution.adapters.state_db` via
     `from_uri(db_uri: str)` or `connect(**kwargs)` if available.

Env convenience:
  - If you call `connect_from_env()` we will read EXEC_DB_URI to try and
    create an execution state connection via `execution.adapters.state_db`.

All addresses/keys are **bytes**. Helpers are provided to decode 0x-hex.

This module never mutates balances unless you call `credit`/`debit`
explicitly or set them yourself; read operations are pure.
"""

from __future__ import annotations

import binascii
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "ExecutionState",
    "Batch",
    "hex_to_bytes",
    "connect_from_env",
]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def hex_to_bytes(x: str | bytes | bytearray | memoryview) -> bytes:
    """Accept 0x-hex or raw bytes-like and return bytes."""
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    s = str(x).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        s = "0" + s
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex string: {x!r}") from e


# ---------------------------------------------------------------------------
# Backend batch wrapper
# ---------------------------------------------------------------------------


class Batch:
    """
    A context manager representing a state batch/transaction.

    If the backend offers `.batch()` we delegate to it. Otherwise we look
    for `.begin()`/`.commit(tx)`/`.rollback(tx)` trio. If neither is
    available, we provide a no-op batch (still acts as a context manager).
    """

    def __init__(self, backend: Any):
        self._backend = backend
        self._ctx = None
        self._tx = None

    def __enter__(self):
        # Preferred: a dedicated context manager
        batch_cm = getattr(self._backend, "batch", None)
        if callable(batch_cm):
            self._ctx = batch_cm()
            return self._ctx.__enter__()

        # Fallback: explicit begin/commit/rollback API
        begin = getattr(self._backend, "begin", None)
        if callable(begin):
            self._tx = begin()
            return self

        # No-op fallback
        return self

    def __exit__(self, exc_type, exc, tb):
        # Matched pair for dedicated CM
        if self._ctx is not None:
            return self._ctx.__exit__(exc_type, exc, tb)

        # Matched pair for explicit tx API
        if self._tx is not None:
            commit = getattr(self._backend, "commit", None)
            rollback = getattr(self._backend, "rollback", None)
            if exc is None and callable(commit):
                commit(self._tx)
            elif exc is not None and callable(rollback):
                rollback(self._tx)
            # swallow nothing; propagate exceptions
            return False

        # No-op: nothing to do
        return False


# ---------------------------------------------------------------------------
# ExecutionState facade
# ---------------------------------------------------------------------------


@dataclass
class ExecutionState:
    """
    Facade that normalizes common state access patterns for capabilities/.

    Usage:

      es = connect_from_env()  # or ExecutionState(backend=my_state_db)
      addr = hex_to_bytes("0x0123...")
      key  = hex_to_bytes("0xdeadbeef")

      bal = es.get_balance(addr)
      with es.batch():
          es.debit(addr, 1000)
          es.credit(hex_to_bytes("0xabcd..."), 1000)
          es.set_storage(addr, key, b"value")
    """

    backend: Any

    # --- balances / nonce --------------------------------------------------

    def get_balance(self, addr: bytes) -> int:
        fn = getattr(self.backend, "get_balance", None)
        if not callable(fn):
            raise NotImplementedError("backend missing get_balance(addr)")
        val = fn(addr)
        return int(val)

    def set_balance(self, addr: bytes, value: int) -> None:
        fn = getattr(self.backend, "set_balance", None)
        if not callable(fn):
            raise NotImplementedError("backend missing set_balance(addr, value)")
        fn(addr, int(value))

    def get_nonce(self, addr: bytes) -> int:
        fn = getattr(self.backend, "get_nonce", None)
        if not callable(fn):
            raise NotImplementedError("backend missing get_nonce(addr)")
        return int(fn(addr))

    def set_nonce(self, addr: bytes, value: int) -> None:
        fn = getattr(self.backend, "set_nonce", None)
        if not callable(fn):
            raise NotImplementedError("backend missing set_nonce(addr, value)")
        fn(addr, int(value))

    # --- storage -----------------------------------------------------------

    def get_storage(self, addr: bytes, key: bytes) -> bytes:
        fn = getattr(self.backend, "get_storage", None)
        if not callable(fn):
            raise NotImplementedError("backend missing get_storage(addr, key)")
        val = fn(addr, key)
        return bytes(val) if val is not None else b""

    def set_storage(self, addr: bytes, key: bytes, value: bytes) -> None:
        fn = getattr(self.backend, "set_storage", None)
        if not callable(fn):
            raise NotImplementedError("backend missing set_storage(addr, key, value)")
        fn(addr, key, bytes(value))

    # --- higher-level helpers ---------------------------------------------

    def credit(self, addr: bytes, amount: int) -> int:
        """Add `amount` to balance; returns new balance."""
        if amount < 0:
            raise ValueError("amount must be >= 0 for credit()")
        bal = self.get_balance(addr)
        new_bal = bal + int(amount)
        self.set_balance(addr, new_bal)
        return new_bal

    def debit(self, addr: bytes, amount: int, *, allow_negative: bool = False) -> int:
        """Subtract `amount`; returns new balance. Raises on insufficient funds unless allow_negative."""
        if amount < 0:
            raise ValueError("amount must be >= 0 for debit()")
        bal = self.get_balance(addr)
        new_bal = bal - int(amount)
        if new_bal < 0 and not allow_negative:
            raise RuntimeError(f"insufficient balance: have {bal}, need {amount}")
        self.set_balance(addr, new_bal)
        return new_bal

    # --- batching / transactions ------------------------------------------

    @contextmanager
    def batch(self):
        """
        Context manager that groups state mutations as a unit of work.

        Example:
            with es.batch():
                es.debit(a, 10)
                es.credit(b, 10)
        """
        with Batch(self.backend):
            yield


# ---------------------------------------------------------------------------
# Discovery / construction helpers
# ---------------------------------------------------------------------------


def _construct_from_exec_adapter(
    db_uri: Optional[str] = None, **kwargs
) -> Optional[ExecutionState]:
    """
    Try to import/construct the adapter from execution.adapters.state_db.

    We support either:
      - StateDB.from_uri(db_uri)
      - from_uri(db_uri)
      - connect(**kwargs)
    """
    try:
        import importlib

        mod = importlib.import_module("execution.adapters.state_db")
    except Exception:
        return None

    # Try class method StateDB.from_uri
    state_cls = getattr(mod, "StateDB", None)
    if state_cls is not None:
        creator = getattr(state_cls, "from_uri", None)
        if callable(creator) and db_uri:
            return ExecutionState(backend=creator(db_uri))

    # Try top-level from_uri
    from_uri = getattr(mod, "from_uri", None)
    if callable(from_uri) and db_uri:
        return ExecutionState(backend=from_uri(db_uri))

    # Try generic connect(**kwargs)
    connect = getattr(mod, "connect", None)
    if callable(connect):
        return ExecutionState(backend=connect(**kwargs))

    return None


def connect_from_env() -> ExecutionState:
    """
    Construct an `ExecutionState` from environment variables or raise.

      - EXEC_DB_URI: preferred hint to execution.adapters.state_db
    """
    db_uri = os.getenv("EXEC_DB_URI")
    es = _construct_from_exec_adapter(db_uri=db_uri or None)
    if es is not None:
        return es
    raise RuntimeError(
        "Could not construct ExecutionState from environment; "
        "set EXEC_DB_URI or pass an explicit backend to ExecutionState(backend=...)."
    )
