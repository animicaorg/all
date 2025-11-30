# SPDX-License-Identifier: Apache-2.0
"""
OpenRPC schemas — load & sanity-check, and validate example JSON-RPC requests.

This test suite aims to be *forgiving* about repository layout and dependencies:
- It searches several common locations for OpenRPC documents.
- It searches several common locations for example JSON-RPC request vectors.
- If `jsonschema` is available, it performs real JSON Schema validation of
  method params against the OpenRPC-described schemas. Otherwise it performs
  structural checks and skips strict validation gracefully.

Environment overrides:
- OPENRPC_DIR: directory to scan for *.json OpenRPC files
- OPENRPC_EXAMPLES_DIR: directory to scan for example JSON-RPC request *.json files
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Dict, Iterable, Iterator, List, Mapping, Optional,
                    Tuple)

import pytest

# Optional import: if missing, strict validation is skipped.
try:  # pragma: no cover
    import jsonschema  # type: ignore
    from jsonschema.validators import Draft202012Validator  # type: ignore
except Exception:  # pragma: no cover
    jsonschema = None
    Draft202012Validator = None  # type: ignore


# -----------------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------------

DEFAULT_SCHEMA_DIRS = [
    "spec/openrpc",
    "openrpc",
    "schemas/openrpc",
    "sdk/openrpc",
    "studio-services/schemas/openrpc",
]

DEFAULT_EXAMPLE_DIRS = [
    "spec/test_vectors/jsonrpc",
    "spec/test_vectors/rpc",
    "tests/vectors/jsonrpc",
    "tests/vectors/rpc",
    "test_vectors/jsonrpc",
    "studio-services/fixtures/jsonrpc",
]


def _find_json_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.json"):
        # Skip hidden and build/cache dirs
        if any(part.startswith(".") for part in p.parts):
            continue
        yield p


def discover_openrpc_docs() -> List[Path]:
    dirs = []
    if env := os.getenv("OPENRPC_DIR"):
        dirs.append(Path(env))
    dirs.extend(Path(d) for d in DEFAULT_SCHEMA_DIRS)
    found: List[Path] = []
    for d in dirs:
        if d.exists() and d.is_dir():
            for f in _find_json_files(d):
                try:
                    with f.open("rb") as fh:
                        data = json.load(fh)
                    if (
                        isinstance(data, dict)
                        and "openrpc" in data
                        and "methods" in data
                    ):
                        found.append(f)
                except Exception:
                    # Non-JSON or not an OpenRPC doc — ignore
                    pass
    return found


def discover_example_requests() -> List[Path]:
    dirs = []
    if env := os.getenv("OPENRPC_EXAMPLES_DIR"):
        dirs.append(Path(env))
    dirs.extend(Path(d) for d in DEFAULT_EXAMPLE_DIRS)
    found: List[Path] = []
    for d in dirs:
        if d.exists() and d.is_dir():
            for f in _find_json_files(d):
                found.append(f)
    return found


# -----------------------------------------------------------------------------
# OpenRPC model helpers (minimal)
# -----------------------------------------------------------------------------


@dataclass
class Param:
    name: str
    schema: Dict[str, Any]
    required: bool


@dataclass
class Method:
    name: str
    params: List[Param]
    param_structure: str  # "by-name", "by-position", "either"
    result: Optional[Dict[str, Any]]


@dataclass
class OpenRPCDoc:
    title: str
    version: str
    methods: Dict[str, Method]
    components: Dict[str, Dict[str, Any]]  # components.schemas


def _as_bool(x: Any, default: bool) -> bool:
    if isinstance(x, bool):
        return x
    return default


def parse_openrpc(path: Path) -> OpenRPCDoc:
    data = json.loads(path.read_text(encoding="utf-8"))

    assert isinstance(data, dict), f"{path} must be a JSON object"
    assert "openrpc" in data and isinstance(
        data["openrpc"], str
    ), f"{path} missing 'openrpc' version"
    info = data.get("info") or {}
    assert isinstance(info, dict), f"{path} info must be object"
    title = str(info.get("title") or "unknown")
    version = str(info.get("version") or "0.0.0")

    comps = data.get("components", {}) or {}
    schemas = comps.get("schemas", {}) or {}
    assert isinstance(schemas, dict), f"{path} components.schemas must be object"
    components: Dict[str, Dict[str, Any]] = {}
    for k, v in schemas.items():
        if isinstance(v, dict):
            components[str(k)] = v

    methods: Dict[str, Method] = {}
    for m in data.get("methods", []) or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        ps = []
        for p in m.get("params", []) or []:
            if not isinstance(p, dict):
                continue
            pname = str(p.get("name") or "").strip()
            if not pname:
                continue
            schema = p.get("schema") or {}
            if not isinstance(schema, dict):
                schema = {}
            required = _as_bool(p.get("required"), True)
            ps.append(Param(name=pname, schema=schema, required=required))
        param_structure = str(m.get("paramStructure") or "either")
        result = m.get("result", {}) or None
        if result is not None and not isinstance(result, dict):
            result = None
        methods[name] = Method(
            name=name, params=ps, param_structure=param_structure, result=result
        )

    assert methods, f"{path} contains no methods"
    return OpenRPCDoc(
        title=title, version=version, methods=methods, components=components
    )


# -----------------------------------------------------------------------------
# JSON Schema resolution (very small, local-only)
# -----------------------------------------------------------------------------

REF_PREFIX = "#/components/schemas/"


def _resolve_refs(
    schema: Dict[str, Any],
    components: Mapping[str, Dict[str, Any]],
    _seen: Optional[set] = None,
) -> Dict[str, Any]:
    """Resolve local $ref to components.schemas recursively (best-effort)."""
    from copy import deepcopy

    def _res(node: Any) -> Any:
        if isinstance(node, dict):
            if (
                "$ref" in node
                and isinstance(node["$ref"], str)
                and node["$ref"].startswith(REF_PREFIX)
            ):
                key = node["$ref"][len(REF_PREFIX) :]
                target = components.get(key)
                if target is None:
                    # Leave unresolved; validator will catch if available
                    return node
                return _res(deepcopy(target))
            # Recurse into dict
            new = {}
            for k, v in node.items():
                new[k] = _res(v)
            return new
        elif isinstance(node, list):
            return [_res(x) for x in node]
        else:
            return node

    return _res(schema)


def build_params_schema(
    method: Method, components: Mapping[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """Build a JSON Schema describing the 'params' shape for a method."""
    # Resolve each param's schema first.
    params = [
        Param(p.name, _resolve_refs(p.schema, components), p.required)
        for p in method.params
    ]

    def by_position() -> Dict[str, Any]:
        items = [p.schema for p in params]
        # minItems = number of leading required params
        min_items = 0
        for p in params:
            if p.required:
                min_items += 1
            else:
                break  # requireds are assumed as a prefix (OpenRPC best practice)
        schema = {
            "type": "array",
            "items": items,
            "minItems": min_items,
            "maxItems": len(items),
            "additionalItems": False,
        }
        return schema

    def by_name() -> Dict[str, Any]:
        properties = {p.name: p.schema for p in params}
        required = [p.name for p in params if p.required]
        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        return schema

    s = method.param_structure.lower()
    if s == "by-position":
        return by_position()
    if s == "by-name":
        return by_name()
    # either (default)
    return {
        "anyOf": [by_position(), by_name()],
    }


# -----------------------------------------------------------------------------
# Examples loader
# -----------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_example_requests(
    paths: Iterable[Path],
) -> Iterator[Tuple[Path, Dict[str, Any]]]:
    for p in paths:
        try:
            obj = load_json(p)
        except Exception:
            continue
        if (
            isinstance(obj, dict)
            and obj.get("jsonrpc") == "2.0"
            and isinstance(obj.get("method"), str)
        ):
            yield (p, obj)
        elif isinstance(obj, list):
            for i, it in enumerate(obj):
                if (
                    isinstance(it, dict)
                    and it.get("jsonrpc") == "2.0"
                    and isinstance(it.get("method"), str)
                ):
                    yield (p.with_suffix(p.suffix + f"#{i}"), it)


# -----------------------------------------------------------------------------
# Tests
# -----------------------------------------------------------------------------


def test_openrpc_documents_load_and_have_minimal_shape():
    docs = discover_openrpc_docs()
    if not docs:
        pytest.skip(
            "No OpenRPC documents found (set OPENRPC_DIR to point at your specs)."
        )

    for path in docs:
        doc = parse_openrpc(path)
        assert doc.title, f"{path}: info.title required"
        assert doc.version, f"{path}: info.version required"
        # semver-ish shape for visibility (not strict)
        assert any(
            ch.isdigit() for ch in doc.version
        ), f"{path}: info.version should look like a version"
        # minimal method shapes
        for m in doc.methods.values():
            assert m.name, f"{path}: method missing name"
            assert isinstance(m.params, list), f"{path}:{m.name} params must be list"
            assert m.param_structure in {
                "by-name",
                "by-position",
                "either",
            }, f"{path}:{m.name} invalid paramStructure"


@pytest.mark.parametrize("strict", [False, True])
def test_examples_are_covered_and_params_validate(strict):
    """
    - strict=False: only require method presence & basic params kind checks.
    - strict=True: if jsonschema available, validate params against built schema.
    """
    example_paths = discover_example_requests()
    docs_paths = discover_openrpc_docs()
    if not example_paths:
        pytest.skip("No example JSON-RPC requests found (set OPENRPC_EXAMPLES_DIR).")
    if not docs_paths:
        pytest.skip("No OpenRPC documents found (set OPENRPC_DIR).")

    # Merge methods across all discovered specs
    methods: Dict[str, Tuple[Method, OpenRPCDoc]] = {}
    for dp in docs_paths:
        doc = parse_openrpc(dp)
        for name, m in doc.methods.items():
            # Prefer first occurrence
            methods.setdefault(name, (m, doc))

    # Collect examples
    examples = list(iter_example_requests(example_paths))
    assert examples, "No valid JSON-RPC 2.0 example requests discovered"

    # If strict=True but jsonschema is absent, downgrade expectations
    strict = strict and jsonschema is not None

    for p, req in examples:
        # jsonrpc version
        assert req.get("jsonrpc") == "2.0", f"{p}: jsonrpc must be '2.0'"
        method = str(req.get("method"))
        assert (
            method in methods
        ), f"{p}: method '{method}' not found in any OpenRPC spec"
        m, doc = methods[method]

        # Basic param kind checks
        params = req.get("params", None)
        if m.param_structure == "by-name":
            assert isinstance(params, dict), f"{p}: params must be object for by-name"
        elif m.param_structure == "by-position":
            assert isinstance(
                params, list
            ), f"{p}: params must be array for by-position"
        else:
            assert isinstance(
                params, (dict, list, type(None))
            ), f"{p}: params must be object or array"

        # Strict validation (if jsonschema available)
        if strict:  # pragma: no cover (exercise in CI where jsonschema installed)
            schema = build_params_schema(m, doc.components)
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as e:
                pytest.fail(
                    f"{p}: built params schema for '{method}' is invalid JSON Schema: {e}"
                )

            # Treat None params as []/{} depending on structure for validation
            val = params
            if val is None and isinstance(schema, dict):
                if schema.get("type") == "array":
                    val = []
                elif schema.get("type") == "object":
                    val = {}
            try:
                jsonschema.validate(instance=val, schema=schema)  # type: ignore
            except Exception as e:
                pytest.fail(f"{p}: params do not validate for method '{method}': {e}")


def test_components_jsonschemas_are_well_formed_if_validator_available():
    docs_paths = discover_openrpc_docs()
    if not docs_paths:
        pytest.skip("No OpenRPC documents found.")

    if Draft202012Validator is None:  # pragma: no cover
        pytest.skip("jsonschema not installed; skipping schema-form check.")

    for dp in docs_paths:
        doc = parse_openrpc(dp)
        for name, schema in doc.components.items():
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as e:
                pytest.fail(
                    f"{dp}: components.schemas['{name}'] is not a valid JSON Schema: {e}"
                )
