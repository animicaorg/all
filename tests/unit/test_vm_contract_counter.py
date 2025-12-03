# SPDX-License-Identifier: Apache-2.0
"""
Compile & run the canonical "counter" contract using the pure-Python VM (vm_py).

We exercise a minimal stateful round-trip:
  1) compile the Python source with its manifest
  2) call `inc(n)` a few times
  3) call `get()` and verify the accumulated value

This test talks to the VM directly (no node, no RPC). It uses the Python-side
bridge helpers exposed by studio-wasm under studio-wasm/py/bridge/.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

# --- Locate the vm_py sources -------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_BRIDGE_DIR = REPO_ROOT / "studio-wasm" / "py"
COUNTER_DIR = REPO_ROOT / "studio-wasm" / "examples" / "counter"
COUNTER_SRC = COUNTER_DIR / "contract.py"
COUNTER_MANIFEST = COUNTER_DIR / "manifest.json"

if PY_BRIDGE_DIR.exists():
    # Ensure "vm_pkg" and "bridge" are importable
    sys.path.insert(0, str(PY_BRIDGE_DIR))
else:
    pytest.skip(
        f"studio-wasm Python runtime not found at {PY_BRIDGE_DIR}",
        allow_module_level=True,
    )

# Import bridge entry points (compile/run)
try:
    from bridge.entry import compile_bytes, run_call  # type: ignore
except Exception as e:  # pragma: no cover
    pytest.skip(f"Could not import vm_py bridge entry points: {e}", allow_module_level=True)


# --- Helpers ------------------------------------------------------------------


def _read_counter_sources() -> Tuple[bytes, str]:
    if not COUNTER_SRC.exists() or not COUNTER_MANIFEST.exists():
        pytest.skip(
            f"Counter example not found under {COUNTER_DIR} "
            "(expected contract.py and manifest.json)"
        )
    src_bytes = COUNTER_SRC.read_bytes()
    manifest_str = COUNTER_MANIFEST.read_text(encoding="utf-8")
    # Quick sanity of manifest JSON
    json.loads(manifest_str)
    return src_bytes, manifest_str


@pytest.fixture(scope="session")
def compiled_counter() -> Dict[str, Any]:
    """
    Compile the counter contract once per test session.
    Returns an artifact dict as produced by bridge.entry.compile_bytes.
    Expected minimal keys:
      - "program": opaque VM program bytes (or IR)
      - "abi": manifest / ABI JSON object (optional but preferred)
    """
    src_bytes, manifest_str = _read_counter_sources()

    # Some build systems pin deterministic behavior via env flags.
    os.environ.setdefault("VM_DETERMINISTIC", "1")
    os.environ.setdefault("VM_SEED", "deadbeef")  # deterministic PRNG for tests

    artifact = compile_bytes(src_bytes, manifest_str)
    assert isinstance(artifact, dict), "compile_bytes should return a dict artifact"
    # Be forgiving about exact field names but require a program-like payload
    program = artifact.get("program") or artifact.get("ir") or artifact.get("bytecode")
    assert program, "artifact must contain 'program' (or 'ir' / 'bytecode')"
    return artifact


def _call(
    artifact: Dict[str, Any],
    fn: str,
    args: list[Any] | None = None,
    state: Dict[str, Any] | None = None,
):
    """
    Thin wrapper around bridge.entry.run_call with stable defaults.
    Returns (retval, new_state, gas_used) where fields may be None if not provided.
    """
    args = args or []
    state = state or {}
    # run_call is expected to accept:
    #   (artifact, method_name, args, state, gas_limit=None, read_only=False)
    # and return a dict like:
    #   {"return": <val or list>, "state": {...}, "gas_used": <int>, "logs": [...]}.
    result = run_call(artifact, fn, args, state, gas_limit=1_000_000, read_only=False)
    assert isinstance(result, dict), "run_call should return a dict result"
    return result.get("return"), result.get("state") or state, result.get("gas_used")


# --- Tests --------------------------------------------------------------------


@pytest.mark.parametrize("steps", [[1], [2, 3], [5, 7, 11]])
def test_counter_inc_and_get_roundtrip(compiled_counter: Dict[str, Any], steps):
    """
    For several increment sequences, ensure the final get() equals the sum.
    """
    artifact = compiled_counter
    state: Dict[str, Any] = {}

    total = 0
    for n in steps:
        ret, state, gas_inc = _call(artifact, "inc", [int(n)], state)
        # inc usually returns the new value or None; tolerate either but assert no error
        assert "error" not in (
            ret if isinstance(ret, dict) else {}
        ), f"inc({n}) returned error: {ret}"
        total += n

    ret, state, gas_get = _call(artifact, "get", [], state)
    # get() should return the accumulated value; accept scalar or [scalar]
    if isinstance(ret, list):
        assert len(ret) == 1, f"get() returned multiple values: {ret}"
        ret_val = ret[0]
    else:
        ret_val = ret
    assert (
        int(ret_val) == total
    ), f"counter value mismatch: expected {total}, got {ret_val}"

    # Optional: basic gas sanity (only if VM reports it)
    if gas_get is not None:
        assert isinstance(gas_get, int) and gas_get > 0


def test_counter_is_deterministic(compiled_counter: Dict[str, Any]):
    """
    Running the same call twice against the same starting state should
    yield identical outputs and state transitions (deterministic VM).
    """
    artifact = compiled_counter

    # First run: inc(4) then get()
    state_a: Dict[str, Any] = {}
    _, state_a, _ = _call(artifact, "inc", [4], state_a)
    ret_a, state_a, _ = _call(artifact, "get", [], state_a)

    # Second run from empty state should mirror behavior
    state_b: Dict[str, Any] = {}
    _, state_b, _ = _call(artifact, "inc", [4], state_b)
    ret_b, state_b, _ = _call(artifact, "get", [], state_b)

    # Compare results (accept scalar or [scalar])
    def _norm(x):
        if isinstance(x, list):
            return tuple(x)
        return x

    assert _norm(ret_a) == _norm(ret_b), "Deterministic get() result mismatch"
    # State equality: VM state is a JSON-like dict
    assert state_a == state_b, "Deterministic state mismatch after identical calls"


def test_counter_storage_persists_across_calls(compiled_counter: Dict[str, Any]):
    """
    Ensure mutated storage persists across subsequent invocations when we carry the
    returned state forward explicitly (simulates a stateful environment).
    """
    artifact = compiled_counter

    state: Dict[str, Any] = {}
    _, state, _ = _call(artifact, "inc", [10], state)
    _, state, _ = _call(artifact, "inc", [32], state)
    ret, state, _ = _call(artifact, "get", [], state)

    if isinstance(ret, list):
        ret = ret[0]
    assert int(ret) == 42, f"Expected counter to persist to 42, got {ret}"
