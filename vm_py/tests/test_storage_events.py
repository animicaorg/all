from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple, Optional
import pytest


# ---------------------------------------------------------------------------
# Helpers to compile & run a tiny contract that uses storage + events.
# These try multiple function/return shapes so the tests are resilient to refactors.
# ---------------------------------------------------------------------------

CONTRACT_SRC = """
from stdlib import storage, events

def set_value(x: int) -> None:
    storage.set(b'k', x)
    events.emit(b'Set', {'x': x})

def get_value() -> int:
    return storage.get(b'k', 0)

def multi_emit(a: int, b: int) -> None:
    events.emit(b'A', {'a': a})
    events.emit(b'B', {'b': b})
"""

# Minimal, permissive manifest the loader can accept.
# Implementations may ignore parts of this, which is fine.
MANIFEST: Dict[str, Any] = {
    "name": "StorageEventsTest",
    "version": "0.0.1",
    "abi": {
        "functions": [
            {"name": "set_value", "inputs": [{"name": "x", "type": "int"}], "outputs": []},
            {"name": "get_value", "inputs": [], "outputs": [{"type": "int"}]},
            {"name": "multi_emit", "inputs": [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}], "outputs": []},
        ],
        "events": [
            {"name": "Set", "inputs": [{"name": "x", "type": "int"}]},
            {"name": "A", "inputs": [{"name": "a", "type": "int"}]},
            {"name": "B", "inputs": [{"name": "b", "type": "int"}]},
        ],
    },
}


class _ContractRunner:
    """
    Thin adapter over whatever the loader returns, providing:
      - call(fn_name, *args) -> (return_value, events_list)
    """

    def __init__(self, loader_obj: Any):
        self._obj = loader_obj

    def _pull_events(self, call_result: Any) -> Tuple[Any, List[Dict[str, Any]]]:
        """
        Normalize various return/event shapes:
          - (ret, events)
          - {'return': ret, 'events': [...]}
          - ret; events on .events / .last_events / .get_events()
        """
        # Tuple (ret, events)
        if isinstance(call_result, tuple) and len(call_result) == 2:
            ret, ev = call_result
            if isinstance(ev, list):
                return ret, ev

        # Dict-style
        if isinstance(call_result, dict):
            ret = call_result.get("return", call_result.get("result"))
            ev = call_result.get("events") or call_result.get("logs") or []
            if isinstance(ev, list):
                return ret, ev

        # Object with attributes
        for attr in ("events", "last_events", "logs"):
            if hasattr(self._obj, attr):
                ev = getattr(self._obj, attr)
                # Support callable getter
                if callable(ev):
                    try:
                        ev = ev()
                    except TypeError:
                        ev = ev  # leave as-is if no-arg call fails
                if isinstance(ev, list):
                    return call_result, ev

        # Fallback: no events found
        return call_result, []

    def call(self, fn_name: str, *args: Any) -> Tuple[Any, List[Dict[str, Any]]]:
        # Prefer explicit "call"/"invoke" methods
        for meth in ("call", "invoke", "run", "run_call"):
            fn = getattr(self._obj, meth, None)
            if callable(fn):
                # Try common shapes
                for style in (
                    lambda: fn(fn_name, *args),                 # call('fn', args…)
                    lambda: fn(name=fn_name, args=list(args)),  # run_call(name=, args=)
                    lambda: fn(method=fn_name, args=list(args)),
                ):
                    try:
                        result = style()
                        return self._pull_events(result)
                    except TypeError:
                        continue

        # Some loaders expose methods as attributes on an inner object
        # e.g., obj.methods['name'](*args)
        methods = getattr(self._obj, "methods", None)
        if isinstance(methods, dict) and fn_name in methods and callable(methods[fn_name]):
            result = methods[fn_name](*args)
            return self._pull_events(result)

        # If nothing matched, raise to make the test output actionable.
        raise AssertionError("Could not find a callable entrypoint on the compiled contract object.")


def _compile_contract(src: str = CONTRACT_SRC, manifest: Dict[str, Any] = MANIFEST) -> _ContractRunner:
    # Try the loader first (preferred).
    try:
        import vm_py.runtime.loader as L  # type: ignore
    except Exception as e:  # pragma: no cover - loader module must exist in this repository
        raise AssertionError(f"vm_py.runtime.loader not importable: {e}")

    last_err: Optional[Exception] = None
    # Candidate loader function names & arg orders we support
    candidates = (
        ("load_manifest_and_source", ("manifest", "source")),
        ("load_source", ("source", "manifest")),
        ("load_from_source", ("source", "manifest")),
        ("compile_and_link", ("source", "manifest")),
        ("load", ("manifest", "source")),
        ("build", ("source", "manifest")),
    )

    for fname, order in candidates:
        fn = getattr(L, fname, None)
        if callable(fn):
            try:
                if order == ("manifest", "source"):
                    obj = fn(manifest=manifest, source=src)  # type: ignore[misc]
                else:
                    obj = fn(source=src, manifest=manifest)  # type: ignore[misc]
                return _ContractRunner(obj)
            except TypeError:
                # Retry with positional
                try:
                    if order == ("manifest", "source"):
                        obj = fn(manifest, src)  # type: ignore[misc]
                    else:
                        obj = fn(src, manifest)  # type: ignore[misc]
                    return _ContractRunner(obj)
                except Exception as e:
                    last_err = e
            except Exception as e:
                last_err = e

    raise AssertionError(f"Could not compile/link contract via loader; last error: {last_err}")


def _event_names(evts: List[Dict[str, Any]]) -> List[bytes]:
    names: List[bytes] = []
    for e in evts:
        # Accept either bytes names or hex/str; normalize to bytes for compare
        name = e.get("name") or e.get("event") or e.get("topic") or e.get("id")
        if isinstance(name, bytes):
            names.append(name)
        elif isinstance(name, str):
            try:
                # Allow "0x…" or plain string; if ascii, encode
                if name.startswith("0x"):
                    names.append(bytes.fromhex(name[2:]))
                else:
                    names.append(name.encode("utf-8"))
            except Exception:
                names.append(str(name).encode("utf-8"))
        else:
            names.append(b"?")
    return names


def _event_args(ev: Dict[str, Any]) -> Dict[str, Any]:
    # Common keys: 'args', 'data', 'payload'
    return ev.get("args") or ev.get("data") or ev.get("payload") or {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_storage_set_get_roundtrip():
    c = _compile_contract()

    # Initially unset → default 0
    ret0, ev0 = c.call("get_value")
    assert isinstance(ret0, int)
    assert ret0 == 0
    assert isinstance(ev0, list) and len(ev0) == 0

    # Set → event emitted
    ret1, ev1 = c.call("set_value", 7)
    assert ret1 is None or ret1 == 0  # allow None or 0 depending on ABI convention
    assert len(ev1) == 1
    names1 = _event_names(ev1)
    assert names1 == [b"Set"]
    args1 = _event_args(ev1[0])
    # value may be under key 'x' or b'x' depending on encoder
    assert (args1.get("x") or args1.get(b"x")) == 7

    # Read back → 7
    ret2, ev2 = c.call("get_value")
    assert ret2 == 7
    assert ev2 == []


def test_event_ordering_is_preserved():
    c = _compile_contract()

    # Emit two events in a single call; order must be A then B
    _, ev = c.call("multi_emit", 1, 2)
    names = _event_names(ev)
    assert names == [b"A", b"B"]

    a_args = _event_args(ev[0])
    b_args = _event_args(ev[1])
    assert (a_args.get("a") or a_args.get(b"a")) == 1
    assert (b_args.get("b") or b_args.get(b"b")) == 2


def test_multiple_calls_append_events_in_call_scope_only():
    """
    Each call should return only its own events, not accumulate across calls.
    """
    c = _compile_contract()
    _, ev1 = c.call("multi_emit", 3, 4)
    _, ev2 = c.call("multi_emit", 5, 6)

    names1 = _event_names(ev1)
    names2 = _event_names(ev2)

    assert names1 == [b"A", b"B"]
    assert names2 == [b"A", b"B"]

    a1 = _event_args(ev1[0]); b1 = _event_args(ev1[1])
    a2 = _event_args(ev2[0]); b2 = _event_args(ev2[1])

    assert (a1.get("a") or a1.get(b"a")) == 3
    assert (b1.get("b") or b1.get(b"b")) == 4
    assert (a2.get("a") or a2.get(b"a")) == 5
    assert (b2.get("b") or b2.get(b"b")) == 6
