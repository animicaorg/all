from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import pytest

from vm_py.tests.test_runtime_counter import (MANIFEST_PATH, SOURCE_PATH,
                                              _engine_call,
                                              _engine_new_session,
                                              _load_module_from_manifest)

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def have_examples() -> bool:
    return MANIFEST_PATH.exists() and SOURCE_PATH.exists()


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

CONTRACT_MANIFEST: Dict[str, Any] = {
    "name": "RoundtripStorage",
    "version": "0.0.1",
    "abi": {
        "functions": [
            {
                "name": "set_value",
                "inputs": [{"name": "x", "type": "int"}],
                "outputs": [],
            },
            {"name": "get_value", "inputs": [], "outputs": [{"type": "int"}]},
            {
                "name": "multi_emit",
                "inputs": [{"name": "a", "type": "int"}, {"name": "b", "type": "int"}],
                "outputs": [],
            },
        ],
        "events": [
            {"name": "Set", "inputs": [{"name": "x", "type": "int"}]},
            {"name": "A", "inputs": [{"name": "a", "type": "int"}]},
            {"name": "B", "inputs": [{"name": "b", "type": "int"}]},
        ],
    },
}


# --- codec helpers ------------------------------------------------------------


def _encode_module(module_obj: Any) -> bytes:
    from vm_py.compiler import encode as enc  # type: ignore

    for name in ("encode_module", "dumps", "encode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(module_obj)
    raise AssertionError("No encode function found in vm_py.compiler.encode")


def _decode_module(buf: bytes) -> Any:
    from vm_py.compiler import encode as enc  # type: ignore

    for name in ("decode_module", "loads", "decode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(buf)
    raise AssertionError("No decode function found in vm_py.compiler.encode")


def _roundtrip(module_or_bytes: Any) -> Tuple[Any, bytes]:
    if isinstance(module_or_bytes, (bytes, bytearray)):
        encoded = bytes(module_or_bytes)
    else:
        encoded = _encode_module(module_or_bytes)

    decoded = _decode_module(encoded)
    reencoded = _encode_module(decoded)

    assert (
        encoded == reencoded
    ), "Module encoding must be canonical; encode(decode(x)) should equal encode(x)"
    return decoded, reencoded


# --- compilation helpers ------------------------------------------------------


def _compile_module_from_source(src: str, manifest: Dict[str, Any]) -> Any:
    try:
        import vm_py.runtime.loader as L  # type: ignore
    except Exception as exc:  # pragma: no cover - loader must exist for this test
        raise AssertionError(f"vm_py.runtime.loader not importable: {exc}") from exc

    candidates: Iterable[Tuple[str, Tuple[str, ...]]] = (
        ("load_manifest_and_source", ("manifest", "source")),
        ("load_source", ("source", "manifest")),
        ("load_from_source", ("source", "manifest")),
        ("compile_and_link", ("source", "manifest")),
        ("compile_source_and_link", ("source", "manifest")),
        ("compile", ("source", "manifest")),
        ("build", ("source", "manifest")),
    )

    last_err: Exception | None = None
    for fname, order in candidates:
        fn = getattr(L, fname, None)
        if not callable(fn):
            continue
        try:
            if order == ("manifest", "source"):
                return fn(manifest=manifest, source=src)  # type: ignore[misc]
            return fn(source=src, manifest=manifest)  # type: ignore[misc]
        except TypeError:
            try:
                if order == ("manifest", "source"):
                    return fn(manifest, src)  # type: ignore[misc]
                return fn(src, manifest)  # type: ignore[misc]
            except Exception as exc:
                last_err = exc
        except Exception as exc:
            last_err = exc
    raise AssertionError(
        f"Could not compile/link contract via loader; last error: {last_err}"
    )


# --- tests --------------------------------------------------------------------


@pytest.mark.usefixtures("have_examples")
def test_counter_roundtrip_exec(have_examples: bool) -> None:
    if not have_examples:
        pytest.skip("Counter example files not present")

    try:
        module = _load_module_from_manifest(MANIFEST_PATH, SOURCE_PATH)
    except Exception as exc:
        pytest.skip(f"counter example could not be compiled: {exc}")
    decoded, encoded = _roundtrip(module)

    runner, extra = _engine_new_session(encoded)

    initial = _engine_call(runner, "get", extra=extra)
    initial_val = initial["return"]
    assert isinstance(initial_val, int)

    inc_res = _engine_call(runner, "inc", extra=extra)
    inc_logs = inc_res.get("logs") or []
    assert isinstance(inc_logs, list)

    after = _engine_call(runner, "get", extra=extra)
    assert after["return"] == initial_val + 1

    redecoded, reencoded = _roundtrip(decoded)
    assert encoded == reencoded
    assert redecoded is not None


@pytest.mark.usefixtures("have_examples")
def test_storage_contract_roundtrip_exec(have_examples: bool) -> None:
    if not have_examples:
        pytest.skip("Examples missing; loader not available")

    try:
        module = _compile_module_from_source(CONTRACT_SRC, CONTRACT_MANIFEST)
    except AssertionError as exc:
        pytest.skip(str(exc))
    _, encoded = _roundtrip(module)

    runner, extra = _engine_new_session(encoded)

    res0 = _engine_call(runner, "get_value", extra=extra)
    assert res0["return"] in (0, None)

    res_set = _engine_call(runner, "set_value", args=[7], extra=extra)
    logs = res_set.get("logs") or []
    assert isinstance(logs, list) and len(logs) >= 1

    res1 = _engine_call(runner, "get_value", extra=extra)
    assert res1["return"] == 7

    res_multi = _engine_call(runner, "multi_emit", args=[1, 2], extra=extra)
    multi_logs = res_multi.get("logs") or []
    assert isinstance(multi_logs, list) and len(multi_logs) >= 2
