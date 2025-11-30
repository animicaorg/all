# -*- coding: utf-8 -*-
"""
Local smoke tests for contracts/examples/quantum_rng/contract.py

These tests run entirely in-process against the Python VM (vm_py). We monkeypatch
the VM syscall layer so the contract can:
  • enqueue a "quantum" job (returns a deterministic task_id), and
  • later read back a mocked result for that task_id, and
  • mix the result with a deterministic beacon value.

We don't assert the exact cryptographic mix formula (that lives in the contract);
instead we check round-trip determinism, lengths, and that `last()` reflects the
latest fulfillment.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import pytest

# ---------------------------------------------------------------------------
# VM loader shims
# ---------------------------------------------------------------------------

VM_LOADER_MODS = (
    "vm_py.runtime.loader",
    "vm_pkg.runtime.loader",  # studio-wasm subset, if running in that env
)

CONTRACT_DIR = Path(__file__).resolve().parent
ROOT = CONTRACT_DIR.parent.parent.parent  # repo root
MANIFEST = CONTRACT_DIR / "manifest.json"
SRC = CONTRACT_DIR / "contract.py"


def _import_first(mod_names: tuple[str, ...]):
    last_exc: Optional[BaseException] = None
    for name in mod_names:
        try:
            return importlib.import_module(name)
        except BaseException as exc:  # pragma: no cover - best-effort loader
            last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError("No loader module found")


def _load_contract(manifest_path: Path):
    """
    Loads/compiles the contract via vm_py loader, returning a (handle, call) pair.

    The returned `call(name, *args)` must execute the contract function named `name`
    with positional args and return Python-native values per ABI.
    """
    loader = _import_first(VM_LOADER_MODS)

    # Try common loader shapes across environments.
    handle = None
    if hasattr(loader, "load"):
        handle = loader.load(str(manifest_path))
    elif hasattr(loader, "load_manifest"):
        handle = loader.load_manifest(str(manifest_path))
    elif hasattr(loader, "Loader"):
        handle = loader.Loader().load(str(manifest_path))
    else:  # pragma: no cover
        raise RuntimeError("Could not find a load() function in VM loader")

    # Resolve a callable entrypoint
    if hasattr(handle, "call"):

        def _call(fname: str, *args):
            return handle.call(fname, *args)

        return handle, _call

    # Some builds expose (program, abi) and require a dispatcher
    # Try to find a dispatcher in vm_py.runtime.abi
    try:
        abi_mod = importlib.import_module("vm_py.runtime.abi")
    except Exception:  # pragma: no cover - studio-wasm alt path
        abi_mod = importlib.import_module("vm_pkg.runtime.abi")  # type: ignore

    if hasattr(handle, "program") and hasattr(handle, "abi"):
        program = getattr(handle, "program")
        abi = getattr(handle, "abi")

        def _call(fname: str, *args):
            return abi_mod.dispatch_call(program, fname, list(args))  # type: ignore

        return handle, _call

    # Fallback: look for generic attributes
    if hasattr(handle, "invoke"):

        def _call(fname: str, *args):
            return handle.invoke(fname, list(args))

        return handle, _call

    raise RuntimeError("Unknown contract handle shape; cannot find call() or invoke()")


# ---------------------------------------------------------------------------
# Syscall fakes (monkeypatched into vm_py.runtime.syscalls_api)
# ---------------------------------------------------------------------------


class QuantumSyscallFakes:
    """
    Deterministic fakes for quantum enqueue/read + beacon access.
    """

    def __init__(self) -> None:
        self._results: dict[bytes, bytes] = {}
        self._beacon = b"\x42" * 32  # 32 bytes of deterministic 'beacon'

    def quantum_enqueue(self, *args, **kwargs) -> bytes:
        """
        Return a deterministic task_id from the arguments. The contract doesn't
        need to know the format; the test will inject a result later using the
        returned task_id.
        """
        material = repr((args, kwargs)).encode("utf-8")
        try:
            from hashlib import sha3_256
        except Exception:  # pragma: no cover
            import hashlib

            sha3_256 = hashlib.sha3_256  # type: ignore
        task_id = sha3_256(b"quantum|" + material).digest()
        # Do not set a result yet; tests will inject via inject_result()
        return task_id

    def read_result(self, task_id: bytes) -> Optional[bytes]:
        return self._results.get(task_id)

    def read_beacon(self) -> bytes:
        return self._beacon

    # Test helper: inject a deterministic "quantum" output for a task_id.
    def inject_result(self, task_id: bytes, payload: bytes) -> None:
        self._results[task_id] = payload


def _monkeypatch_syscalls(mp, fakes: QuantumSyscallFakes):
    """
    Replace vm_py runtime syscall hooks with our fakes. We patch both the
    runtime API and stdlib surface to be resilient to layout differences.
    """
    # Primary target: runtime.syscalls_api
    try:
        sys_api = importlib.import_module("vm_py.runtime.syscalls_api")
    except Exception:
        sys_api = importlib.import_module("vm_pkg.runtime.syscalls_api")  # type: ignore

    mp.setattr(sys_api, "quantum_enqueue", fakes.quantum_enqueue, raising=False)
    mp.setattr(sys_api, "read_result", fakes.read_result, raising=False)
    # Some environments expose read_beacon; if not present, we skip
    mp.setattr(sys_api, "read_beacon", fakes.read_beacon, raising=False)

    # Secondary: stdlib.syscalls may directly re-export helpers
    try:
        std_sys = importlib.import_module("vm_py.stdlib.syscalls")
        mp.setattr(std_sys, "quantum_enqueue", fakes.quantum_enqueue, raising=False)
        mp.setattr(std_sys, "read_result", fakes.read_result, raising=False)
        mp.setattr(std_sys, "read_beacon", fakes.read_beacon, raising=False)
    except Exception:
        try:
            std_sys = importlib.import_module("vm_pkg.stdlib.syscalls")  # type: ignore
            mp.setattr(std_sys, "quantum_enqueue", fakes.quantum_enqueue, raising=False)
            mp.setattr(std_sys, "read_result", fakes.read_result, raising=False)
            mp.setattr(std_sys, "read_beacon", fakes.read_beacon, raising=False)
        except Exception:
            pass  # acceptable in trimmed environments


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not MANIFEST.is_file(), reason="manifest.json missing")
def test_request_then_poll_and_last(monkeypatch, tmp_path):
    """
    request(bits,shots,trap_rate) returns a task_id (bytes).
    poll(task_id) first indicates not-ready, then (after injection) returns (True, out).
    last() returns the same mixed bytes as the latest fulfilled task.
    """
    # Import/require the VM
    try:
        _import_first(VM_LOADER_MODS)
    except Exception as exc:
        pytest.skip(f"vm loader unavailable: {exc}")

    # Patch syscalls
    fakes = QuantumSyscallFakes()
    _monkeypatch_syscalls(monkeypatch, fakes)

    # Load contract
    handle, call = _load_contract(MANIFEST)

    # Basic happy path with small output (32 bits = 4 bytes)
    bits, shots, trap_rate = 32, 8, 10
    task_id = call("request", bits, shots, trap_rate)
    assert isinstance(task_id, (bytes, bytearray)) and len(task_id) > 0

    ready, out0 = call("poll", bytes(task_id))
    assert ready is False
    assert isinstance(out0, (bytes, bytearray))
    assert len(out0) == 0

    # Inject a deterministic "quantum" output for the task and poll again
    want_len = bits // 8
    quantum_bytes = b"\xaa" * max(
        32, want_len
    )  # longer than needed; contract should derive/crop
    fakes.inject_result(bytes(task_id), quantum_bytes)

    ready2, out1 = call("poll", bytes(task_id))
    assert ready2 is True
    assert isinstance(out1, (bytes, bytearray))
    assert len(out1) == want_len, "mixed output must honor requested bit-length"

    # last() should equal the mixed bytes we just produced
    out_last = call("last")
    assert bytes(out_last) == bytes(out1)

    # Idempotence: polling again stays ready and stable
    ready3, out2 = call("poll", bytes(task_id))
    assert ready3 is True
    assert bytes(out2) == bytes(out1) == bytes(out_last)


@pytest.mark.skipif(not MANIFEST.is_file(), reason="manifest.json missing")
def test_trap_rate_bounds(monkeypatch):
    """
    trap_rate outside an expected [0,100] range should be rejected by the contract.
    We accept either an explicit revert/exception or a boolean False/empty task id.
    """
    try:
        _import_first(VM_LOADER_MODS)
    except Exception as exc:
        pytest.skip(f"vm loader unavailable: {exc}")

    fakes = QuantumSyscallFakes()
    _monkeypatch_syscalls(monkeypatch, fakes)
    _, call = _load_contract(MANIFEST)

    # Too high trap rate
    with pytest.raises(Exception):
        _ = call("request", 32, 8, 101)  # expect revert/exception

    # Negative (if contract accepts unsigned, this may also revert internally)
    with pytest.raises(Exception):
        _ = call("request", 32, 8, -1)  # type: ignore[arg-type]


@pytest.mark.skipif(not MANIFEST.is_file(), reason="manifest.json missing")
def test_multiple_requests_independent_results(monkeypatch):
    """
    Two concurrent task_ids must not interfere; last() reflects the most recent fulfillment.
    """
    try:
        _import_first(VM_LOADER_MODS)
    except Exception as exc:
        pytest.skip(f"vm loader unavailable: {exc}")

    fakes = QuantumSyscallFakes()
    _monkeypatch_syscalls(monkeypatch, fakes)
    _, call = _load_contract(MANIFEST)

    t1 = call("request", 16, 4, 5)
    t2 = call("request", 24, 6, 20)

    # Inject result for t1 only; t2 remains pending
    fakes.inject_result(bytes(t1), b"\x01" * 32)

    ready1, out1 = call("poll", bytes(t1))
    ready2, out2 = call("poll", bytes(t2))

    assert ready1 is True and len(out1) == 2  # 16 bits → 2 bytes
    assert ready2 is False and len(out2) == 0

    # last() should reflect t1 completion
    assert bytes(call("last")) == bytes(out1)

    # Now fulfill t2; last() should switch to t2's mixed output
    fakes.inject_result(bytes(t2), b"\x02" * 32)
    ready2b, out2b = call("poll", bytes(t2))
    assert ready2b is True and len(out2b) == 3  # 24 bits → 3 bytes
    assert bytes(call("last")) == bytes(out2b)
