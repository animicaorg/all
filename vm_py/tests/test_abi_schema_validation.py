from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pytest

SCHEMA_CANDIDATES: Iterable[Path] = (
    Path("contracts/schemas/abi.schema.json"),
    Path("spec/abi.schema.json"),
)


ALLOWED_ABI_TYPES = {
    "u8",
    "u16",
    "u32",
    "u64",
    "u128",
    "u256",
    "i32",
    "i64",
    "bool",
    "bytes",
    "bytes32",
    "string",
    "address",
    "array",
    "tuple",
    "bytesN",
}


def _load_schema_path() -> Path:
    for path in SCHEMA_CANDIDATES:
        if path.is_file():
            return path
    raise pytest.skip("ABI schema not found")


def _assert_type(type_obj: object) -> None:
    assert isinstance(type_obj, dict), "type must be an object with abiType"
    abi_type = type_obj.get("abiType")
    assert abi_type in ALLOWED_ABI_TYPES, f"unsupported abiType: {abi_type!r}"
    if abi_type == "array":
        assert "items" in type_obj, "array types must declare items"
        _assert_type(type_obj["items"])
    if abi_type == "tuple":
        comps = type_obj.get("components")
        assert isinstance(comps, list) and comps, "tuple types must have components"
        for comp in comps:
            _assert_type(comp)
    if abi_type == "bytesN":
        size = type_obj.get("size")
        assert isinstance(size, int) and size > 0, "bytesN must declare positive size"


def _assert_param(param: object, *, allow_indexed: bool = False) -> None:
    assert isinstance(param, dict), "parameter must be a dict"
    assert "type" in param, "parameter missing type"
    _assert_type(param["type"])
    if "name" in param:
        assert (
            isinstance(param["name"], str) and param["name"]
        ), "param name must be non-empty string"
    if allow_indexed:
        assert isinstance(
            param.get("indexed", False), bool
        ), "indexed must be boolean when present"


def _assert_function(fn: object) -> None:
    assert isinstance(fn, dict), "function must be a dict"
    assert isinstance(fn.get("name"), str) and fn["name"], "function name required"
    assert fn.get("stateMutability") in {
        "view",
        "pure",
        "nonpayable",
        "payable",
    }, "invalid stateMutability"
    assert isinstance(fn.get("inputs"), list), "function inputs must be list"
    assert isinstance(fn.get("outputs"), list), "function outputs must be list"
    for p in fn.get("inputs", []):
        _assert_param(p)
    for p in fn.get("outputs", []):
        _assert_param(p)


def _assert_event(ev: object) -> None:
    assert isinstance(ev, dict), "event must be a dict"
    assert isinstance(ev.get("name"), str) and ev["name"], "event name required"
    assert isinstance(ev.get("inputs"), list), "event inputs must be list"
    for p in ev.get("inputs", []):
        _assert_param(p, allow_indexed=True)


def _assert_error(err: object) -> None:
    assert isinstance(err, dict), "error must be a dict"
    assert isinstance(err.get("name"), str) and err["name"], "error name required"
    assert isinstance(err.get("inputs"), list), "error inputs must be list"
    for p in err.get("inputs", []):
        _assert_param(p)


@pytest.mark.parametrize(
    "manifest_path",
    sorted(Path("contracts/fixtures/abi").glob("*.json")),
)
def test_fixtures_align_with_abi_schema(manifest_path: Path) -> None:
    schema_path = _load_schema_path()
    assert schema_path.is_file(), "ABI schema file is required for validation"

    manifest = json.loads(manifest_path.read_text())
    assert isinstance(manifest.get("abiVersion"), int), "abiVersion must be an integer"
    assert manifest.get("abiVersion") == 1, "abiVersion must be 1"
    assert (
        isinstance(manifest.get("name"), str) and manifest["name"]
    ), "name must be non-empty string"

    fns = manifest.get("functions")
    assert isinstance(fns, list) and fns, "functions list must be non-empty"
    for fn in fns:
        _assert_function(fn)

    events = manifest.get("events", [])
    assert isinstance(events, list), "events must be a list"
    for ev in events:
        _assert_event(ev)

    errors = manifest.get("errors", [])
    assert isinstance(errors, list), "errors must be a list"
    for err in errors:
        _assert_error(err)
