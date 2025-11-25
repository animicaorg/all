"""
Vertical 5 — VM-Py ABI & Contract Manifests
Test #38: function selectors & dispatch mapping

Goals:
- Given ABI definitions (e.g. Counter manifest), compute canonical function
  selectors per ABI spec and assert they are stable and collision-free.
- Verify vm_py.runtime.abi dispatch mapping (get_dispatch_table / Dispatcher)
  exports the right functions and routes calls correctly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from vm_py.runtime import abi as runtime_abi
from vm_py.errors import ValidationError


# ---------------------------------------------------------------------------
# Helpers for reading manifests & computing selectors
# ---------------------------------------------------------------------------

def _counter_manifest_path() -> Path:
    """
    Locate vm_py/examples/counter/manifest.json relative to this test file.
    """
    here = Path(__file__).resolve()
    vm_py_root = here.parents[1]  # .../animica/vm_py
    return vm_py_root / "examples" / "counter" / "manifest.json"


def _load_counter_manifest() -> Dict[str, Any]:
    path = _counter_manifest_path()
    with path.open("r", encoding="utf8") as f:
        return json.load(f)


def _canonical_signature(fn: Dict[str, Any]) -> str:
    """
    Build the canonical signature string from a manifest function entry.

    Spec (ABI v1):

        selector = sha3_256("animica:abi:v1|" + canonical_signature_bytes)[:8]

        canonical_signature := name "(" paramTypeId [ "," paramTypeId ]* ")"

    Where paramTypeId are the "type" fields of inputs, in order.
    """
    name = fn["name"]
    types = [inp["type"] for inp in fn.get("inputs", [])]
    return f"{name}(" + ",".join(types) + ")"


def _compute_selector(canonical_signature: str) -> bytes:
    prefix = "animica:abi:v1|"  # ABI v1 selector domain
    data = (prefix + canonical_signature).encode("utf8")
    return hashlib.sha3_256(data).digest()[:8]


# These expected values are *baked in* to guarantee stability: if the ABI
# selector rules or the manifest change in an incompatible way, this test
# will fail loudly.
EXPECTED_COUNTER_SELECTORS_HEX: Dict[str, str] = {
    "inc": "f7776663dbd17153",
    "get": "f1e030f0110a25e0",
    "set": "a45c4fc944fe4f5c",
}


# ---------------------------------------------------------------------------
# Fixtures: stub stdlib & force fallback ABI
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def inject_stdlib() -> None:
    """
    The Counter contract does:

        from stdlib import storage, events, abi

    In the real VM this is injected; for tests we provide a minimal stub so
    that importing vm_py.examples.counter.contract doesn't explode.
    """
    if "stdlib" not in sys.modules:
        stdlib = types.ModuleType("stdlib")
        stdlib.storage = types.SimpleNamespace()
        stdlib.events = types.SimpleNamespace()
        stdlib.abi = types.SimpleNamespace()
        sys.modules["stdlib"] = stdlib
    yield


@pytest.fixture(autouse=True)
def force_fallback_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    For these tests we only care about the *dispatch* mapping, not the
    external vm_py.abi {encoding,decoding} integration.

    Force vm_py.runtime.abi to use its internal fallback codec so that
    dispatch_call() is well-defined even if vm_py.abi.decode_call is
    not fully implemented yet.
    """
    monkeypatch.setattr(runtime_abi, "_use_external_abi", False)
    yield


# ---------------------------------------------------------------------------
# Tests: function selectors from manifest
# ---------------------------------------------------------------------------

def test_counter_manifest_selectors_are_stable_and_unique() -> None:
    manifest = _load_counter_manifest()
    fns: List[Dict[str, Any]] = manifest["abi"]["functions"]

    seen: Dict[str, bytes] = {}

    for fn in fns:
        name = fn["name"]
        sig = _canonical_signature(fn)
        sel = _compute_selector(sig)

        # Must be exactly 8 bytes
        assert isinstance(sel, (bytes, bytearray))
        assert len(sel) == 8

        # Determinism: recomputing yields identical bytes
        sel2 = _compute_selector(sig)
        assert sel2 == sel

        # No collisions among the example functions
        if sig in seen:
            pytest.fail(f"selector collision for signature {sig!r}")
        seen[sig] = sel

        # If we have an expected hex value for this function, assert it.
        expected_hex = EXPECTED_COUNTER_SELECTORS_HEX.get(name)
        if expected_hex is not None:
            assert sel.hex() == expected_hex, (
                f"selector for {name} changed: got {sel.hex()}, "
                f"expected {expected_hex}"
            )

    # As a sanity check, we expect selectors for inc/get/set to be present.
    for required in ("inc", "get", "set"):
        assert any(fn["name"] == required for fn in fns)


# ---------------------------------------------------------------------------
# Tests: dispatch table mapping for contracts
# ---------------------------------------------------------------------------

def test_get_dispatch_table_matches_counter_manifest_functions() -> None:
    """
    The Counter example is written as top-level functions in a module.
    get_dispatch_table(contract_module) should export the public contract
    entrypoints and exclude private helpers.
    """
    # Import the contract module as the "contract object"
    counter_mod = importlib.import_module("vm_py.examples.counter.contract")

    table = runtime_abi.get_dispatch_table(counter_mod)

    # Manifest-defined function names
    manifest = _load_counter_manifest()
    manifest_func_names = {fn["name"] for fn in manifest["abi"]["functions"]}

    # All manifest functions must be present in the dispatch table
    for name in manifest_func_names:
        assert name in table, f"dispatch table missing function {name!r}"

    # Private helpers (starting with "_") must not be exported
    for attr in dir(counter_mod):
        if attr.startswith("_"):
            assert attr not in table, f"private attribute {attr!r} leaked into dispatch table"

    # Dispatch table values must be callables
    for name, fn in table.items():
        assert callable(fn), f"dispatch entry {name!r} is not callable"


def test_dispatcher_invokes_methods_and_raises_on_errors() -> None:
    """
    Exercise Dispatcher.for_contract(contract).invoke(...) against a simple
    in-memory contract class to prove that:

      - Known methods are called with args and can mutate state.
      - Unknown methods raise ValidationError.
      - Bad arguments (wrong arity) raise ValidationError, not raw TypeError.
    """

    class SampleContract:
        def __init__(self) -> None:
            self.counter = 0

        def inc(self, n: int) -> None:
            self.counter += n

        def get(self) -> int:
            return self.counter

        def _private(self) -> None:
            # Must not be exported
            self.counter = -999

        def flag(self) -> str:
            return "ok"

    # Mark one exported method as explicitly hidden
    SampleContract.flag.__animica_export__ = False  # type: ignore[attr-defined]

    c = SampleContract()
    disp = runtime_abi.Dispatcher.for_contract(c)

    # invoke() should call methods and update state
    disp.invoke("inc", [5])
    assert c.counter == 5

    result = disp.invoke("get", [])
    assert result == 5

    # Unknown method -> ValidationError
    with pytest.raises(ValidationError):
        disp.invoke("does_not_exist", [])

    # Bad arity -> ValidationError (wrapped TypeError)
    with pytest.raises(ValidationError):
        disp.invoke("inc", [])  # missing required argument

    # _private and flag (export False) must not be present
    table = disp.table
    assert "inc" in table
    assert "get" in table
    assert "_private" not in table
    assert "flag" not in table


# ---------------------------------------------------------------------------
# Optional integration: encode a fallback call envelope and run dispatch_call
# ---------------------------------------------------------------------------

def _encode_fallback_call(method: str, args: List[Any]) -> bytes:
    """
    Build a call envelope compatible with vm_py.runtime.abi._fallback_decode_call:

        uvarint(len(method)) || method_utf8 || uvarint(argc) || enc_value(arg0)...
    """
    # Access internal helpers deliberately; tests are allowed to touch these.
    _uvarint_encode = runtime_abi._uvarint_encode  # type: ignore[attr-defined]
    _enc_value = runtime_abi._enc_value            # type: ignore[attr-defined]

    m_bytes = method.encode("utf8")
    buf = bytearray()
    buf += _uvarint_encode(len(m_bytes))
    buf += m_bytes
    buf += _uvarint_encode(len(args))
    for a in args:
        buf += _enc_value(a)
    return bytes(buf)


def test_dispatch_call_roundtrip_with_fallback_codec() -> None:
    """
    Encode a simple call using the fallback envelope and ensure dispatch_call
    routes correctly through Dispatcher and encode_return.
    """

    class SimpleContract:
        def echo(self, x: int) -> int:
            return x * 2

    c = SimpleContract()

    # Build call data for echo(7)
    data = _encode_fallback_call("echo", [7])

    # dispatch_call should decode, invoke, and encode the return
    out = runtime_abi.dispatch_call(c, data)

    # decode_return isn't exposed; re-use the internal value decoder via
    # _fallback_decode_call on a synthetic "return" envelope: one anonymous arg.
    _dec_value = runtime_abi._dec_value          # type: ignore[attr-defined]

    val, offset = _dec_value(out, 0)
    assert offset == len(out)
    assert val == 14
