"""
Test harness for the Animica NFT marketplace + ANM-721 + Founders Pass
contracts. Builds on top of contracts/tests/conftest.py's `stdlib` shim
package and adds the few primitives those contracts need that the
existing harness doesn't ship:

  - abi.caller()           — who invoked the current function
  - abi.self()             — the address of this contract instance
  - abi.value()            — ANM attached as transaction value
  - abi.block_timestamp()  — current "block" wall-clock for events
  - abi.call(target, method, args)
                           — synchronous cross-contract call dispatched
                             through the harness's registry

It also provides:

  - deploy(path, address)  — compile a contract source and bind it to
                             a per-instance storage / event / balance
                             context. Multiple deploys live side by
                             side in the same registry so a marketplace
                             contract can call into an NFT collection.
  - with_call(caller=…, value=…, ts=…)
                           — context manager that pushes per-call state
                             onto the per-contract context.

Stays intentionally pure-Python and dependency-light; mirrors the
production VM surface enough for the contract tests to exercise their
state machines.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional


# ---------------------------------------------------------------------------
# Per-contract context
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    address: bytes
    storage: Dict[bytes, bytes] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    # Native ANM balances of arbitrary addresses; the contract's own
    # balance is stored under `address`.
    balances: Dict[bytes, int] = field(default_factory=dict)
    # Per-call mutable state (stacked so nested calls don't trample).
    call_stack: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def caller(self) -> bytes:
        return self.call_stack[-1]["caller"] if self.call_stack else b""

    @property
    def value(self) -> int:
        return int(self.call_stack[-1]["value"]) if self.call_stack else 0

    @property
    def block_timestamp(self) -> int:
        if self.call_stack and "ts" in self.call_stack[-1]:
            return int(self.call_stack[-1]["ts"])
        return int(time.time())


# ---------------------------------------------------------------------------
# Active-context plumbing
# ---------------------------------------------------------------------------


_CURRENT: Optional[_Ctx] = None
_REGISTRY: Dict[bytes, "Contract"] = {}


def _cur() -> _Ctx:
    if _CURRENT is None:
        raise RuntimeError("no active contract context")
    return _CURRENT


# ---------------------------------------------------------------------------
# stdlib shims
# ---------------------------------------------------------------------------


class Revert(Exception):
    pass


def _to_bytes(b: Any) -> bytes:
    if isinstance(b, bytes):
        return b
    if isinstance(b, bytearray):
        return bytes(b)
    if isinstance(b, str):
        return b.encode("utf-8")
    raise TypeError(f"expected bytes/bytearray, got {type(b).__name__}")


# storage
def _st_get(key: bytes, default: bytes = b"") -> bytes:
    ctx = _cur()
    return ctx.storage.get(_to_bytes(key), default)


def _st_set(key: bytes, value: bytes) -> None:
    ctx = _cur()
    ctx.storage[_to_bytes(key)] = _to_bytes(value)


def _st_delete(key: bytes) -> None:
    ctx = _cur()
    ctx.storage.pop(_to_bytes(key), None)


# events
def _ev_emit(name: bytes, args: Mapping[str, Any] | None = None) -> None:
    ctx = _cur()
    ctx.events.append({"name": _to_bytes(name), "args": dict(args or {})})


# abi
def _abi_require(cond: bool, message: bytes | str = b"require_failed") -> None:
    if not cond:
        if isinstance(message, bytes):
            try:
                msg = message.decode("utf-8")
            except UnicodeDecodeError:
                msg = repr(message)
        else:
            msg = str(message)
        raise Revert(msg)


def _abi_revert(message: bytes | str = b"revert") -> None:
    _abi_require(False, message)


def _abi_caller() -> bytes:
    return _cur().caller


def _abi_self() -> bytes:
    return _cur().address


def _abi_value() -> int:
    return _cur().value


def _abi_block_timestamp() -> int:
    return _cur().block_timestamp


def _abi_call(target: bytes, method: bytes, args: list) -> Any:
    """Synchronously invoke `method(args...)` on the contract registered
    at `target`. The callee sees `abi.caller() == this contract's
    address` and `abi.value() == 0` unless the caller forwards value
    (we don't currently — tests can use `Contract.send_value` to seed
    balances).
    """
    addr = _to_bytes(target)
    if addr not in _REGISTRY:
        raise Revert(f"call_to_unknown_contract:{addr.hex()}")
    callee = _REGISTRY[addr]
    caller_ctx = _cur()
    method_name = method.decode("utf-8") if isinstance(method, bytes) else str(method)
    return callee._invoke(
        method_name,
        list(args or []),
        caller=caller_ctx.address,
        value=0,
        ts=caller_ctx.block_timestamp,
    )


# treasury
def _tr_balance(addr: Optional[bytes] = None) -> int:
    ctx = _cur()
    a = _to_bytes(addr) if addr is not None else ctx.address
    return int(ctx.balances.get(a, 0))


def _tr_transfer(to: bytes, amount: int) -> None:
    amount = int(amount)
    if amount < 0:
        raise Revert("negative_transfer")
    ctx = _cur()
    src = ctx.address
    dst = _to_bytes(to)
    cur_bal = ctx.balances.get(src, 0)
    if cur_bal < amount:
        raise Revert("insufficient_treasury_balance")
    ctx.balances[src] = cur_bal - amount
    ctx.balances[dst] = ctx.balances.get(dst, 0) + amount


def _install_stdlib() -> None:
    """Install the synthetic `stdlib` package. Idempotent."""
    base = types.ModuleType("stdlib")

    storage_mod = types.ModuleType("stdlib.storage")
    storage_mod.get = _st_get
    storage_mod.set = _st_set
    storage_mod.delete = _st_delete

    events_mod = types.ModuleType("stdlib.events")
    events_mod.emit = _ev_emit

    abi_mod = types.ModuleType("stdlib.abi")
    abi_mod.require = _abi_require
    abi_mod.revert = _abi_revert
    abi_mod.caller = _abi_caller
    abi_mod.self = _abi_self
    abi_mod.value = _abi_value
    abi_mod.block_timestamp = _abi_block_timestamp
    abi_mod.call = _abi_call

    treasury_mod = types.ModuleType("stdlib.treasury")
    treasury_mod.balance = _tr_balance
    treasury_mod.transfer = _tr_transfer

    base.storage = storage_mod
    base.events = events_mod
    base.abi = abi_mod
    base.treasury = treasury_mod

    sys.modules["stdlib"] = base
    sys.modules["stdlib.storage"] = storage_mod
    sys.modules["stdlib.events"] = events_mod
    sys.modules["stdlib.abi"] = abi_mod
    sys.modules["stdlib.treasury"] = treasury_mod


# ---------------------------------------------------------------------------
# Contract wrapper
# ---------------------------------------------------------------------------


@dataclass
class Contract:
    module: types.ModuleType
    ctx: _Ctx

    @property
    def address(self) -> bytes:
        return self.ctx.address

    @property
    def storage(self) -> Dict[bytes, bytes]:
        return self.ctx.storage

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.ctx.events

    @property
    def balances(self) -> Dict[bytes, int]:
        return self.ctx.balances

    def send_value(self, amount: int) -> None:
        """Credit `amount` ANM to this contract's treasury (simulates
        the VM forwarding tx.value before the contract runs)."""
        amount = int(amount)
        if amount < 0:
            raise ValueError("negative")
        self.ctx.balances[self.address] = self.ctx.balances.get(self.address, 0) + amount

    def _invoke(
        self,
        method: str,
        args: list,
        *,
        caller: bytes,
        value: int,
        ts: int,
    ) -> Any:
        global _CURRENT
        if not hasattr(self.module, method):
            raise Revert(f"unknown_method:{method}")
        fn = getattr(self.module, method)
        prev = _CURRENT
        _CURRENT = self.ctx
        self.ctx.call_stack.append({"caller": caller, "value": value, "ts": ts})
        try:
            return fn(*args)
        finally:
            self.ctx.call_stack.pop()
            _CURRENT = prev

    def call(
        self,
        method: str,
        *args,
        caller: bytes = b"",
        value: int = 0,
        ts: Optional[int] = None,
    ) -> Any:
        """Top-level invocation (i.e. the test acting as the EOA caller)."""
        if value > 0:
            self.send_value(value)
        return self._invoke(
            method,
            list(args),
            caller=caller,
            value=int(value),
            ts=int(ts if ts is not None else time.time()),
        )


# ---------------------------------------------------------------------------
# Public deploy API
# ---------------------------------------------------------------------------


_LOADED_MODULES: Dict[str, types.ModuleType] = {}


def _load_module(path: str) -> types.ModuleType:
    """Load (and cache) a contract module from a source file."""
    sp = Path(path)
    if not sp.is_file():
        raise FileNotFoundError(path)
    cache_key = str(sp.resolve())
    if cache_key in _LOADED_MODULES:
        # Return a fresh deep-copy so per-deploy contract state doesn't
        # leak (the contracts use module-globals via the `stdlib` shims,
        # not direct module attributes — so re-import is safe to share).
        return _LOADED_MODULES[cache_key]
    spec = importlib.util.spec_from_file_location(
        "anm_test_" + hashlib.sha3_256(sp.read_bytes()).hexdigest()[:12],
        str(sp),
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    _LOADED_MODULES[cache_key] = module
    return module


def deploy(path: str, address: bytes) -> Contract:
    """Compile a contract source and bind it at `address`."""
    _install_stdlib()
    module = _load_module(path)
    ctx = _Ctx(address=address)
    contract = Contract(module=module, ctx=ctx)
    _REGISTRY[address] = contract
    return contract


def reset_registry() -> None:
    """Wipe the cross-contract registry. Call this between tests so a
    new `deploy()` starts fresh."""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# Small helpers used by tests
# ---------------------------------------------------------------------------


def addr(tag: str) -> bytes:
    """Stable deterministic address for a tag (used for owner/alice/bob/
    contract identities)."""
    return hashlib.sha3_256(b"anm-test:" + tag.encode("utf-8")).digest()[:20]


@contextlib.contextmanager
def assert_reverts(needle: str = ""):
    """Context manager that asserts the wrapped block raises a Revert
    whose message contains `needle`."""
    try:
        yield
    except Revert as e:
        if needle and needle not in str(e):
            raise AssertionError(
                f"revert message {str(e)!r} did not contain {needle!r}"
            )
        return
    raise AssertionError("expected revert but no exception was raised")


def events_named(contract: Contract, name: bytes) -> List[Dict[str, Any]]:
    return [e for e in contract.events if e["name"] == name]


__all__ = [
    "Contract",
    "Revert",
    "addr",
    "assert_reverts",
    "deploy",
    "events_named",
    "reset_registry",
]
