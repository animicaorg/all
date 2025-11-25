# -*- coding: utf-8 -*-
"""
contracts.tests.conftest
========================

Pytest fixtures for contract examples and templates.

Goals:
- Provide a **minimal, deterministic local VM harness** so example contracts can
  be imported and exercised without a full node.
- Offer **compile helpers** that load a contract source file and attach a tiny,
  in-memory stdlib (storage/events/hash/abi/treasury/syscalls).
- Expose **funded accounts** with stable addresses for examples and smoke tests.

This harness is intentionally tiny and pure-Python. It is NOT the production VM.
It is only meant for unit/integration tests in this repository and mirrors the
surface that our example contracts actually use.

Usage (inside a test file):
    def test_counter_flow(compile_contract, funded_accounts):
        alice = funded_accounts["alice"]["address"]
        c = compile_contract("contracts/templates/counter/contract.py")
        # Optional init if the contract defines it:
        c.maybe_call("init")
        c.call("inc")
        c.call("inc")
        assert c.call("get") == 2
        # Inspect emitted events:
        assert [e["name"] for e in c.events] == [b"Inc", b"Inc"]

Notes:
- If vm_py is available in PYTHONPATH, you can still explicitly import and use
  it in your own tests. These fixtures don't prevent that.
- This file also seeds a deterministic RNG and common environment defaults.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Tuple

import pytest

# --- stable env for tests -----------------------------------------------------

# Keep dict/set hash-iteration stable. (CI may override but local runs benefit.)
os.environ.setdefault("PYTHONHASHSEED", "0")
# Prefer UTC everywhere.
os.environ.setdefault("TZ", "UTC")
# Conventional devnet defaults (used by some examples/tools).
os.environ.setdefault("ANIMICA_CHAIN_ID", "1337")
os.environ.setdefault("ANIMICA_RPC_URL", "http://127.0.0.1:8545")

# A project-wide deterministic seed. Local helpers derive pseudo-random bytes
# from this via SHA3 rather than importing 'random'.
PROJECT_TEST_SEED = 1337


# --- tiny deterministic helpers ----------------------------------------------

def _drbg(label: bytes, n: int) -> bytes:
    """
    A very small deterministic "DRBG": keccak(chain) stream. Good enough for tests.
    Not cryptographic; do not use outside of fixtures/dev tooling.
    """
    out = b""
    ctr = 0
    while len(out) < n:
        m = hashlib.sha3_256()
        m.update(b"tests-drbg-v1|")
        m.update(str(PROJECT_TEST_SEED).encode("ascii"))
        m.update(label)
        m.update(ctr.to_bytes(8, "big"))
        out += m.digest()
        ctr += 1
    return out[:n]


def _det_address(tag: str) -> str:
    """
    Produce a stable 20-byte hex address (0x...) from a tag.
    Contracts and tests can treat it as an "account address".
    """
    h = hashlib.sha3_256(tag.encode("utf-8")).hexdigest()[:40]
    return "0x" + h


# --- a minimal test stdlib ----------------------------------------------------

# The example contracts import symbols like:
#   from stdlib.storage import get, set
#   from stdlib.events import emit
#   from stdlib.abi import require, revert
#   from stdlib.hash import sha3_256, keccak256
#   from stdlib.treasury import balance, transfer
#
# Here we construct a synthetic 'stdlib' package with just enough behavior to
# run the examples. It is *per-call* bound to a current "context" holding the
# contract's storage, emitted events, and balances.


@dataclass
class _Context:
    address: str
    storage: Dict[bytes, bytes] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    balances: Dict[str, int] = field(default_factory=dict)


_CURRENT_CTX: Optional[_Context] = None


def _with_ctx() -> _Context:
    if _CURRENT_CTX is None:
        raise RuntimeError("No active contract context (bug in test harness).")
    return _CURRENT_CTX


# storage submodule
_storage_mod = types.ModuleType("stdlib.storage")


def _st_get(key: bytes) -> Optional[bytes]:
    ctx = _with_ctx()
    if isinstance(key, str):
        key = key.encode("utf-8")
    return ctx.storage.get(key, None)


def _st_set(key: bytes, value: bytes) -> None:
    ctx = _with_ctx()
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, (bytes, bytearray)):
        raise TypeError("storage.set expects bytes")
    ctx.storage[key] = bytes(value)


_storage_mod.get = _st_get  # type: ignore[attr-defined]
_storage_mod.set = _st_set  # type: ignore[attr-defined]


# events submodule
_events_mod = types.ModuleType("stdlib.events")


def _ev_emit(name: bytes, args: Mapping[str, Any] | None = None) -> None:
    ctx = _with_ctx()
    if isinstance(name, str):
        name = name.encode("utf-8")
    ctx.events.append({"name": bytes(name), "args": dict(args or {})})


_events_mod.emit = _ev_emit  # type: ignore[attr-defined]


# abi submodule
_abi_mod = types.ModuleType("stdlib.abi")


class Revert(Exception):
    """Raised when a contract calls stdlib.abi.revert()."""


def _abi_revert(message: str = "revert") -> None:
    raise Revert(message)


def _abi_require(cond: bool, message: str | None = None) -> None:
    if not cond:
        raise Revert(message or "require failed")


_abi_mod.revert = _abi_revert  # type: ignore[attr-defined]
_abi_mod.require = _abi_require  # type: ignore[attr-defined]


# hash submodule
_hash_mod = types.ModuleType("stdlib.hash")


def _sha3_256(data: bytes) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha3_256(data).digest()


def _sha3_512(data: bytes) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha3_512(data).digest()


def _keccak256(data: bytes) -> bytes:
    # For tests we alias to Python's SHA3-256. Acceptable for non-consensus examples.
    return _sha3_256(data)


_hash_mod.sha3_256 = _sha3_256  # type: ignore[attr-defined]
_hash_mod.sha3_512 = _sha3_512  # type: ignore[attr-defined]
_hash_mod.keccak256 = _keccak256  # type: ignore[attr-defined]


# treasury submodule
_treasury_mod = types.ModuleType("stdlib.treasury")


def _treas_balance(addr: Optional[str] = None) -> int:
    ctx = _with_ctx()
    a = addr or ctx.address
    return int(ctx.balances.get(a, 0))


def _treas_transfer(to: str, amount: int) -> None:
    if amount < 0:
        raise Revert("negative transfer")
    ctx = _with_ctx()
    frm = ctx.address
    if ctx.balances.get(frm, 0) < amount:
        raise Revert("insufficient balance")
    ctx.balances[frm] = ctx.balances.get(frm, 0) - amount
    ctx.balances[to] = ctx.balances.get(to, 0) + amount


_treasury_mod.balance = _treas_balance  # type: ignore[attr-defined]
_treasury_mod.transfer = _treas_transfer  # type: ignore[attr-defined]


# syscalls submodule (stubs used by a few examples)
_syscalls_mod = types.ModuleType("stdlib.syscalls")


def _blob_pin(ns: int, data: bytes) -> Dict[str, Any]:
    # Return a deterministic "commitment" for tests.
    m = hashlib.sha3_256()
    m.update(b"blob|")
    m.update(int(ns).to_bytes(4, "big"))
    m.update(bytes(data))
    return {
        "namespace": int(ns),
        "size": len(data),
        "commitment": "0x" + m.hexdigest(),
    }


def _ai_enqueue(model: str, prompt: str) -> Dict[str, Any]:
    # Deterministic task id
    tid = _drbg(b"ai|" + model.encode() + b"|" + prompt.encode(), 16).hex()
    return {"kind": "AI", "task_id": tid}


def _quantum_enqueue(circuit: Mapping[str, Any], shots: int = 128) -> Dict[str, Any]:
    payload = json.dumps({"circuit": circuit, "shots": shots}, sort_keys=True).encode()
    tid = _drbg(b"q|" + payload, 16).hex()
    return {"kind": "QUANTUM", "task_id": tid}


def _read_result(task_id: str) -> Optional[Dict[str, Any]]:
    # In this minimal harness we don't simulate cross-block availability.
    # Tests can patch/monkeypatch this to feed expected results.
    return None


_syscalls_mod.blob_pin = _blob_pin  # type: ignore[attr-defined]
_syscalls_mod.ai_enqueue = _ai_enqueue  # type: ignore[attr-defined]
_syscalls_mod.quantum_enqueue = _quantum_enqueue  # type: ignore[attr-defined]
_syscalls_mod.read_result = _read_result  # type: ignore[attr-defined]


def _install_test_stdlib() -> None:
    """
    Inject the synthetic 'stdlib' package (and submodules) into sys.modules.
    Safe to call many times.
    """
    base = types.ModuleType("stdlib")
    sys.modules["stdlib"] = base
    sys.modules["stdlib.storage"] = _storage_mod
    sys.modules["stdlib.events"] = _events_mod
    sys.modules["stdlib.abi"] = _abi_mod
    sys.modules["stdlib.hash"] = _hash_mod
    sys.modules["stdlib.treasury"] = _treasury_mod
    sys.modules["stdlib.syscalls"] = _syscalls_mod


# --- Local contract instance wrapper -----------------------------------------

@dataclass
class ContractInstance:
    """
    A tiny in-memory "contract instance" bound to a module.
    """
    module: types.ModuleType
    address: str
    ctx: _Context

    @property
    def storage(self) -> Dict[bytes, bytes]:
        return self.ctx.storage

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.ctx.events

    def _enter(self) -> None:
        global _CURRENT_CTX
        _CURRENT_CTX = self.ctx

    def _exit(self) -> None:
        global _CURRENT_CTX
        _CURRENT_CTX = None

    def maybe_call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self.module, name, None)
        if fn is None:
            return None
        return self.call(name, *args, **kwargs)

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """
        Call a top-level function in the contract module within the bound context.
        """
        if not hasattr(self.module, name):
            raise AttributeError(f"Contract has no function '{name}'")
        fn = getattr(self.module, name)
        self._enter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._exit()


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(scope="session")
def funded_accounts() -> Dict[str, Dict[str, Any]]:
    """
    Three deterministic "funded" accounts used across tests.
    Balances are large enough for example flows.
    """
    deployer = _det_address("deployer")
    alice = _det_address("alice")
    bob = _det_address("bob")
    return {
        "deployer": {"address": deployer, "balance": 10_000_000},
        "alice": {"address": alice, "balance": 5_000_000},
        "bob": {"address": bob, "balance": 5_000_000},
    }


@pytest.fixture(scope="function")
def local_vm(funded_accounts: Mapping[str, Mapping[str, Any]]):
    """
    Provide a factory that compiles a contract file into a ContractInstance bound
    to a fresh context (empty storage, zero events, preloaded balances).
    """
    _install_test_stdlib()

    class _Factory:
        def compile(self, source_path: str | Path, address: Optional[str] = None) -> ContractInstance:
            sp = Path(source_path)
            if not sp.is_file():
                raise FileNotFoundError(f"Contract not found: {sp}")

            # Load the module under a unique name derived from path contents.
            mod_name = "contract_" + hashlib.sha3_256(sp.read_bytes()).hexdigest()[:16]
            spec = importlib.util.spec_from_loader(mod_name, loader=None)
            module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]

            # Execute the source with our stdlib available.
            code = sp.read_text(encoding="utf-8")
            exec(compile(code, str(sp), "exec"), module.__dict__)

            # Fresh context with balances seeded for this contract address as well.
            addr = address or _det_address(sp.as_posix())
            balances: Dict[str, int] = {
                funded_accounts["deployer"]["address"]: int(funded_accounts["deployer"]["balance"]),
                funded_accounts["alice"]["address"]: int(funded_accounts["alice"]["balance"]),
                funded_accounts["bob"]["address"]: int(funded_accounts["bob"]["balance"]),
                addr: 0,
            }
            ctx = _Context(address=addr, balances=balances)
            return ContractInstance(module=module, address=addr, ctx=ctx)

    return _Factory()


@pytest.fixture(scope="function")
def compile_contract(local_vm):
    """
    Convenience wrapper around local_vm.compile so tests can simply do:
        c = compile_contract("contracts/templates/counter/contract.py")
    """
    return local_vm.compile


# --- optional monkeypatch hooks ----------------------------------------------

@pytest.fixture(scope="function")
def patch_syscalls(monkeypatch):
    """
    Helper to monkeypatch syscalls for tests that need to simulate results.

    Example:
        def test_ai_flow(compile_contract, patch_syscalls):
            recorded = {}
            def fake_read_result(task_id: str):
                return {"task_id": task_id, "status": "ok", "output": "hi"}
            patch_syscalls(read_result=fake_read_result)
            c = compile_contract("contracts/examples/ai_agent/contract.py")
            res = c.call("request_ai", b"llama2", b"hello")
            # ... next call sees read_result return the object above
    """
    def _apply(**kwargs):
        _install_test_stdlib()
        for name, fn in kwargs.items():
            if not hasattr(_syscalls_mod, name):
                raise AttributeError(f"stdlib.syscalls has no attribute '{name}'")
            monkeypatch.setattr(_syscalls_mod, name, fn, raising=True)

    return _apply


# --- pretty assertion diffs for bytes & small dicts --------------------------

def pytest_assertrepr_compare(op: str, left: Any, right: Any) -> Optional[List[str]]:
    if isinstance(left, (bytes, bytearray)) and isinstance(right, (bytes, bytearray)) and op == "==":
        def hexdump(b: bytes) -> str:
            return " ".join(f"{x:02x}" for x in b)
        return [
            "bytes differ:",
            f" left: {hexdump(bytes(left))}",
            f"right: {hexdump(bytes(right))}",
        ]
    if isinstance(left, dict) and isinstance(right, dict) and op == "==":
        # Show a compact JSON diff for small dicts to aid debugging.
        try:
            lj = json.dumps(left, sort_keys=True, indent=2)
            rj = json.dumps(right, sort_keys=True, indent=2)
            return ["dicts differ (compact JSON):", " left:", lj, " right:", rj]
        except Exception:
            return None
    return None
