from __future__ import annotations

import json
from pathlib import Path

import pytest

from stdlib import storage as std_storage, events as std_events, abi as std_abi  # type: ignore
from vm_py.runtime.vm_error import VmError
from vm_py.examples.counter import contract as counter  # type: ignore


HERE = Path(__file__).resolve()
# .../animica/vm_py/tests
VM_PY_ROOT = HERE.parents[1]
REPO_ROOT = HERE.parents[2]

COUNTER_MANIFEST = VM_PY_ROOT / "examples" / "counter" / "manifest.json"


def _load_manifest() -> dict:
    assert COUNTER_MANIFEST.is_file(), f"missing Counter manifest at {COUNTER_MANIFEST}"
    with COUNTER_MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def _reset_state() -> None:
    """
    Reset the in-VM storage and event sink so each test is isolated.
    """
    # stdlib.storage / stdlib.events should be thin wrappers over the VM runtime
    std_storage.reset_backend()
    std_events.clear_events()


def test_counter_manifest_matches_contract_signatures() -> None:
    """
    Sanity check that the manifest for the Counter example is consistent with
    the Python contract module: same function names, reasonable metadata.
    """
    m = _load_manifest()

    # Minimal metadata checks (the stronger ones live in the dedicated
    # manifest metadata tests).
    assert m.get("name") == "Counter"
    assert isinstance(m.get("version"), str) and m["version"]
    # We normalized the VM-Py examples to manifestVersion == 1 earlier.
    assert m.get("manifestVersion") == 1

    abi = m.get("abi") or {}
    funcs = {f["name"] for f in abi.get("functions", [])}
    # The canonical entrypoints.
    for fname in ["inc", "get", "set"]:
        assert fname in funcs, f"{fname} missing from Counter ABI"
        assert hasattr(counter, fname), f"{fname} missing from counter contract module"


def test_counter_basic_flow_inc_and_get() -> None:
    """
    Counter should start at 0, and successive inc() calls should bump
    the value while emitting Counter.Incremented events.
    """
    _reset_state()

    # Default is 0 when nothing stored yet.
    assert counter.get() == 0

    counter.inc()
    assert counter.get() == 1

    counter.inc()
    assert counter.get() == 2

    events = std_events.get_events()
    # Two increment events in order
    assert [e.name for e in events] == [b"Counter.Incremented", b"Counter.Incremented"]
    # Args should be dict-like and round-trip correctly.
    assert events[0].args["new"] == 1
    assert events[1].args["new"] == 2


def test_counter_set_updates_value_and_emits_event() -> None:
    """
    set(n) should overwrite the stored value and emit Counter.Set with
    the new value.
    """
    _reset_state()

    counter.set(10)
    assert counter.get() == 10

    events = std_events.get_events()
    # Exactly one Set event for this simple flow.
    assert len(events) == 1
    ev = events[0]
    assert ev.name == b"Counter.Set"
    # Manifest says the field is named "value".
    assert ev.args["value"] == 10


def test_counter_set_rejects_negative_with_vm_error() -> None:
    """
    Guard rails in the contract:
      - set(n) must reject negative values via abi.require, raising VmError.
    """
    _reset_state()

    with pytest.raises(VmError) as excinfo:
        counter.set(-1)

    # The exact message is less important than the fact that this is a
    # structured VM error coming from the contract's abi.require.
    msg = str(excinfo.value)
    # We expect the error reason message to mention "negative" somewhere.
    assert "negative" in msg.lower()
