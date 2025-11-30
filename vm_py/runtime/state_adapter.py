"""
vm_py.runtime.state_adapter
---------------------------

Optional bridge that lets the Python-VM use the *execution* module's real state
instead of the in-process demo dictionary used by `vm_py.runtime.storage_api`.

Design goals
============
- Purely optional: if `execution` is not installed, this module can still be
  imported; nothing explodes.
- Duck-typed: it adapts to a variety of execution-state shapes by probing for
  common method names (get_storage, read_storage, set_storage, write_storage,
  get_balance, credit/debit, etc.).
- Non-invasive: it monkey-patches the VM's storage/events/treasury facades only
  when `install_into_runtime(...)` is called.

Typical usage
=============
    from vm_py.runtime import state_adapter
    # `exec_state` is an object from execution/adapters/state_db.py (or similar)
    state_adapter.install_into_runtime(exec_state)

After installation, calls made by contracts via the stdlib (storage.get/set,
treasury.balance/transfer, events.emit) are forwarded to the execution backend.

What we look for on the provided `exec_state`
=============================================
Storage (addressed by (address: bytes, key: bytes) -> value: bytes):
  - getters:   get_storage, read_storage
  - setters:   set_storage, write_storage, put_storage

Accounts (balance & nonce):
  - balance:   get_balance, balance_of
  - set bal:   set_balance (if absent, we emulate via credit/debit when present)
  - credit:    credit, add_balance
  - debit:     debit, sub_balance
  - nonce:     get_nonce, nonce_of
  - set nonce: set_nonce

Events sink (optional):
  - emit(name: bytes, args: dict[bytes|str, bytes|int|bool|...])
    We probe: emit, log, append

Transactions (optional journaling):
  - begin/commit/rollback: begin_tx / commit_tx / rollback_tx

If a capability is missing, we gracefully degrade to a no-op for that feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Utilities                                                                   #
# --------------------------------------------------------------------------- #


def _first_call(obj: Any, candidates: List[str], *args, **kwargs):
    """Call the first present attribute name on obj; return (found, result)."""
    for name in candidates:
        fn = getattr(obj, name, None)
        if callable(fn):
            return True, fn(*args, **kwargs)
    return False, None


def _first_attr(obj: Any, candidates: List[str]):
    """Return the first callable attribute or None."""
    for name in candidates:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn
    return None


# --------------------------------------------------------------------------- #
# Backend adapter                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class ExecutionStateBackend:
    """
    Thin adapter that presents a unified API around a provided execution state.

    All methods return sensible defaults when the underlying capability does
    not exist, so the VM won't crash if, for example, balance operations are
    not wired for a local-simulation run.
    """

    state: Any

    # --- Storage ------------------------------------------------------------ #

    def get_storage(self, address: bytes, key: bytes) -> bytes:
        ok, out = _first_call(self.state, ["get_storage", "read_storage"], address, key)
        if ok:
            return bytes(out or b"")
        # Some implementations might expect hex strings; attempt fallback
        ok, out = _first_call(
            self.state, ["get_storage", "read_storage"], address.hex(), key.hex()
        )
        if ok:
            return (
                bytes.fromhex(out[2:])
                if isinstance(out, str) and out.startswith("0x")
                else bytes(out or b"")
            )
        return b""

    def set_storage(self, address: bytes, key: bytes, value: bytes) -> None:
        for name in ["set_storage", "write_storage", "put_storage"]:
            fn = getattr(self.state, name, None)
            if callable(fn):
                fn(address, key, value)
                return
        # last-chance: hex
        for name in ["set_storage", "write_storage", "put_storage"]:
            fn = getattr(self.state, name, None)
            if callable(fn):
                fn(address.hex(), key.hex(), "0x" + value.hex())
                return
        # no-op if nothing available

    # --- Accounts: balance & nonce ----------------------------------------- #

    def get_balance(self, address: bytes) -> int:
        ok, out = _first_call(self.state, ["get_balance", "balance_of"], address)
        if ok:
            return int(out or 0)
        ok, out = _first_call(self.state, ["get_balance", "balance_of"], address.hex())
        if ok:
            return int(out or 0)
        return 0

    def set_balance(self, address: bytes, value: int) -> None:
        # Prefer explicit setter
        fn = _first_attr(self.state, ["set_balance"])
        if fn:
            fn(address, int(value))
            return
        # Fall back to credit/debit from current balance
        cur = self.get_balance(address)
        delta = int(value) - int(cur)
        if delta == 0:
            return
        if delta > 0:
            fn = _first_attr(self.state, ["credit", "add_balance"])
            if fn:
                fn(address, delta)
        else:
            fn = _first_attr(self.state, ["debit", "sub_balance"])
            if fn:
                fn(address, -delta)

    def get_nonce(self, address: bytes) -> int:
        ok, out = _first_call(self.state, ["get_nonce", "nonce_of"], address)
        if ok:
            return int(out or 0)
        ok, out = _first_call(self.state, ["get_nonce", "nonce_of"], address.hex())
        if ok:
            return int(out or 0)
        return 0

    def set_nonce(self, address: bytes, value: int) -> None:
        fn = _first_attr(self.state, ["set_nonce"])
        if fn:
            fn(address, int(value))

    # --- Journaling (optional) --------------------------------------------- #

    def begin_tx(self) -> None:
        _first_call(self.state, ["begin_tx", "begin", "tx_begin"])

    def commit_tx(self) -> None:
        _first_call(self.state, ["commit_tx", "commit", "tx_commit"])

    def rollback_tx(self) -> None:
        _first_call(self.state, ["rollback_tx", "rollback", "tx_rollback"])

    # --- Events (optional sink) -------------------------------------------- #

    def emit_event(self, name: bytes, args: Dict[str, Any]) -> None:
        # Some state backends expose an events sink as a sub-object.
        sink = getattr(self.state, "events", None)
        if sink is not None:
            if callable(getattr(sink, "emit", None)):
                sink.emit(name, args)
                return
            if callable(getattr(sink, "append", None)):
                sink.append(name, args)  # type: ignore
                return
        # Or, the state itself might accept event calls:
        for nm in ["emit", "log", "append_event"]:
            fn = getattr(self.state, nm, None)
            if callable(fn):
                fn(name, args)
                return
        # else: no-op


# --------------------------------------------------------------------------- #
# Install / Monkey-patch helpers                                              #
# --------------------------------------------------------------------------- #


def _install_storage_backend(backend: ExecutionStateBackend) -> None:
    """
    Wire the VM's storage_api to use the provided backend.
    """
    try:
        from vm_py.runtime import storage_api  # type: ignore
    except Exception:  # pragma: no cover
        return

    # Use a simple shim that mirrors the public storage_api surface.
    def _get(address: bytes, key: bytes) -> bytes:
        return backend.get_storage(address, key)

    def _set(address: bytes, key: bytes, value: bytes) -> None:
        backend.set_storage(address, key, value)

    # Prefer official hook if present
    if callable(getattr(storage_api, "set_backend", None)):
        try:
            storage_api.set_backend(type("Backend", (), {"get": _get, "set": _set})())
            return
        except Exception:
            pass

    # Fallback: monkey-patch module-level functions
    storage_api.get = _get  # type: ignore[attr-defined]
    storage_api.set = _set  # type: ignore[attr-defined]
    # Some implementations keep a private slot; set if present
    if hasattr(storage_api, "_backend"):
        storage_api._backend = backend  # type: ignore[attr-defined]


def _install_events_sink(backend: ExecutionStateBackend) -> None:
    """Wire the VM's events_api to forward to execution state's sink if any."""
    try:
        from vm_py.runtime import events_api  # type: ignore
    except Exception:  # pragma: no cover
        return

    def _emit(name: bytes, args: Dict[str, Any]) -> None:
        backend.emit_event(name, args)

    if callable(getattr(events_api, "set_sink", None)):
        try:
            events_api.set_sink(type("Sink", (), {"emit": _emit})())
            return
        except Exception:
            pass

    # Fallback: replace module-level function
    events_api.emit = _emit  # type: ignore[attr-defined]


def _install_treasury_bridge(backend: ExecutionStateBackend) -> None:
    """Wire the VM's treasury_api to read/write balances via execution state."""
    try:
        from vm_py.runtime import treasury_api  # type: ignore
    except Exception:  # pragma: no cover
        return

    def _balance(address: bytes) -> int:
        return backend.get_balance(address)

    def _transfer(from_addr: bytes, to_addr: bytes, amount: int) -> None:
        # Minimal deterministic transfer: debit then credit, or rollback on failure.
        backend.begin_tx()
        try:
            src = backend.get_balance(from_addr)
            if amount < 0:
                raise ValueError("negative amount")
            if src < amount:
                raise ValueError("insufficient balance")
            backend.set_balance(from_addr, src - amount)
            dst = backend.get_balance(to_addr)
            backend.set_balance(to_addr, dst + amount)
            backend.commit_tx()
        except Exception:
            backend.rollback_tx()
            raise

    # Prefer hook if present
    if callable(getattr(treasury_api, "set_backend", None)):
        try:
            treasury_api.set_backend(
                type("Treasury", (), {"balance": _balance, "transfer": _transfer})()
            )
            return
        except Exception:
            pass

    # Fallback: patch functions
    treasury_api.balance = _balance  # type: ignore[attr-defined]
    treasury_api.transfer = _transfer  # type: ignore[attr-defined]


def install_into_runtime(exec_state: Any) -> ExecutionStateBackend:
    """
    Public entrypoint: bind the VM runtime (storage, events, treasury) to a
    provided execution-state object.

    Returns the constructed `ExecutionStateBackend` for convenience/tests.
    """
    backend = ExecutionStateBackend(exec_state)
    _install_storage_backend(backend)
    _install_events_sink(backend)
    _install_treasury_bridge(backend)
    return backend


__all__ = [
    "ExecutionStateBackend",
    "install_into_runtime",
]
