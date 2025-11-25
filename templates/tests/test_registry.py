"""
tests for templates registry & individual templates

This suite validates that:
  1) templates/index.json is well-formed and internally consistent.
  2) Each indexed template directory exists and contains the required files:
       - template.json (template manifest/metadata)
       - variables.json (declares inputs needed to render)
  3) IDs are unique, slug-like, and paths are sane (no directory traversal).
  4) variables.json follows a minimal contract (shape & types) and names are unique.
  5) template.json has basic required metadata fields.
  6) (If the rendering engine is available) each template can be dry-rendered
     using default values from variables.json into a temporary directory.

The tests are deliberately conservative (no 3rd-party deps) and rely on
standard library + the tiny helpers from templates.tests.__init__.
"""

from __future__ import annotations

import json
import os
import re
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

import pytest

from templates.tests import (
    REPO_ROOT,
    temp_dir,
    temp_cwd,
    read_json,
)

TEMPLATES_DIR = REPO_ROOT / "templates"
INDEX_PATH = TEMPLATES_DIR / "index.json"

# Acceptable variable "type" values for a minimal contract.
VAR_TYPES = {"string", "integer", "number", "boolean", "enum"}

# Simple semver-ish matcher (not strict semver; just guards obvious mistakes)
SEMVER_RX = re.compile(r"^\d+(?:\.\d+){0,2}([+-].+)?$")

# Slug rules for template IDs (and baseline for directory names).
SLUG_RX = re.compile(r"^[a-z0-9]+(?:[a-z0-9\-]*[a-z0-9])?$")


# ---------- helpers ----------


def _load_index() -> Any:
    assert INDEX_PATH.exists(), f"Missing templates/index.json at {INDEX_PATH}"
    try:
        return read_json(INDEX_PATH)
    except json.JSONDecodeError as e:
        pytest.fail(f"templates/index.json is not valid JSON: {e}")


def _iter_entries(index_obj: Any) -> List[Mapping[str, Any]]:
    """
    Normalize index structure to a list of template entries.

    We support either:
      - {"templates": [ ... ]}  (preferred)
      - [ ... ]                 (legacy/alternate)
    """
    if isinstance(index_obj, dict) and "templates" in index_obj:
        entries = index_obj["templates"]
    elif isinstance(index_obj, list):
        entries = index_obj
    else:
        pytest.fail(
            "index.json must be either a list or an object with a 'templates' array"
        )

    assert isinstance(entries, list), "Index entries must be a list"
    for i, it in enumerate(entries):
        assert isinstance(it, dict), f"Entry #{i} must be an object/dict"
    return entries  # type: ignore[return-value]


def _template_dir(entry: Mapping[str, Any]) -> Path:
    base = TEMPLATES_DIR
    # Prefer explicit 'path', fallback to 'id' if omitted.
    rel = entry.get("path", entry.get("id"))
    assert isinstance(rel, str) and rel, f"Entry missing 'path' or 'id': {entry}"
    # Avoid directory traversal risk
    assert ".." not in Path(rel).parts, f"Illegal path traversal in: {rel}"
    return (base / rel).resolve()


def _required(entry: Mapping[str, Any], key: str, typ: type) -> Any:
    assert key in entry, f"Entry missing required key '{key}': {entry}"
    val = entry[key]
    assert isinstance(val, typ), f"'{key}' must be {typ.__name__}: {entry}"
    return val


def _collect_defaults(vars_spec: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Build a variables mapping using defaults where present and reasonable fallbacks otherwise.
    """
    out: Dict[str, Any] = {}
    items = vars_spec.get("variables", [])
    if not isinstance(items, list):
        pytest.fail("variables.json must contain a 'variables' array")

    for v in items:
        assert isinstance(v, dict), "Each variable spec must be an object"
        name = v.get("name")
        vtype = v.get("type", "string")
        required = bool(v.get("required", False))
        default = v.get("default", None)
        assert isinstance(name, str) and name, f"Variable missing 'name': {v}"
        assert vtype in VAR_TYPES, f"Unsupported variable type: {vtype} in {v}"
        # Simple name hygiene (snake_case is common, but allow alnum/underscore)
        assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name), f"Bad variable name: {name}"

        if default is not None:
            out[name] = default
            continue
        if not required:
            # Optional without default: pick a sensible stub
            out[name] = _fallback_for_type(vtype, name)
        else:
            # Required without default: still produce a fallback to enable dry render,
            # but the renderer may override/validate more strictly.
            out[name] = _fallback_for_type(vtype, name)
    return out


def _fallback_for_type(vtype: str, name: str) -> Any:
    if vtype == "string":
        # Derive something stable-ish from the name for determinism
        return f"example_{name.lower()}"
    if vtype in ("integer", "number"):
        return 1
    if vtype == "boolean":
        return True
    if vtype == "enum":
        # Renderer is expected to check the 'choices' field; we default to first later.
        return None
    return "example"


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        return read_json(path)
    except json.JSONDecodeError as e:
        pytest.fail(f"{path} is not valid JSON: {e}")


# ---------- tests ----------


def test_index_json_well_formed_and_nonempty() -> None:
    index_obj = _load_index()
    entries = _iter_entries(index_obj)
    assert entries, "Index must list at least one template"

    seen_ids = set()
    for i, entry in enumerate(entries):
        tid = _required(entry, "id", str)
        name = _required(entry, "name", str)
        desc = _required(entry, "description", str)
        version = _required(entry, "version", str)

        assert SLUG_RX.match(tid), f"Template id must be slug-like: '{tid}'"
        assert tid not in seen_ids, f"Duplicate template id: '{tid}'"
        seen_ids.add(tid)

        assert name.strip(), "Template name cannot be empty"
        assert desc.strip(), "Template description cannot be empty"
        assert SEMVER_RX.match(version), f"Suspicious version string: '{version}'"


def test_index_paths_exist_and_are_contained() -> None:
    entries = _iter_entries(_load_index())

    for entry in entries:
        tdir = _template_dir(entry)
        assert tdir.exists(), f"Template directory does not exist: {tdir}"
        # Ensure path stays under templates/
        assert str(tdir).startswith(str(TEMPLATES_DIR.resolve())), (
            f"Template path escapes templates/: {tdir}"
        )


def test_each_template_has_required_files() -> None:
    entries = _iter_entries(_load_index())

    for entry in entries:
        tdir = _template_dir(entry)
        tjson = tdir / "template.json"
        vjson = tdir / "variables.json"
        assert tjson.exists(), f"Missing template.json in {tdir}"
        assert vjson.exists(), f"Missing variables.json in {tdir}"

        # Quick parse smoke tests
        _ = _load_json(tjson)
        _ = _load_json(vjson)


def test_variables_json_shape_and_names_unique() -> None:
    entries = _iter_entries(_load_index())

    for entry in entries:
        tdir = _template_dir(entry)
        vjson = _load_json(tdir / "variables.json")
        variables = vjson.get("variables", [])
        assert isinstance(variables, list), f"'variables' must be an array in {tdir}"

        names = []
        for var in variables:
            assert isinstance(var, dict), f"Variable entries must be objects in {tdir}"
            name = _required(var, "name", str)
            vtype = var.get("type", "string")
            assert vtype in VAR_TYPES, f"Unsupported type '{vtype}' in {tdir}"
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name), (
                f"Variable name must be snake/alnum: '{name}' in {tdir}"
            )
            if vtype == "enum":
                choices = var.get("choices")
                assert isinstance(choices, list) and choices, (
                    f"Enum variable '{name}' must specify non-empty 'choices' in {tdir}"
                )
        # uniqueness
        names = [v["name"] for v in variables if isinstance(v, dict) and "name" in v]
        assert len(names) == len(set(names)), f"Duplicate variable names in {tdir}"


def test_template_json_minimal_contract() -> None:
    entries = _iter_entries(_load_index())

    for entry in entries:
        tdir = _template_dir(entry)
        spec = _load_json(tdir / "template.json")

        # Required metadata
        name = _required(spec, "name", str)
        version = _required(spec, "version", str)
        description = _required(spec, "description", str)

        assert name.strip(), f"name is empty in {tdir}"
        assert description.strip(), f"description is empty in {tdir}"
        assert SEMVER_RX.match(version), f"version is not semver-like in {tdir}"

        # Rendering plan should exist. We allow multiple shapes; minimally require
        # either a 'files' list or a 'render' dict.
        has_files = isinstance(spec.get("files"), list) and len(spec["files"]) >= 0
        has_render = isinstance(spec.get("render"), dict)
        assert has_files or has_render, (
            f"{tdir}/template.json must contain a 'files' list or a 'render' object"
        )


@pytest.mark.parametrize("engine_symbol", ["render_template", "render"])
def test_templates_can_dry_render(engine_symbol: str) -> None:
    """
    Attempt to render each template into a temporary directory using defaults.
    This is a *best-effort* dry run: if the engine module or symbol isn't present,
    the test is skipped. If enums are present, we prefer the first choice.
    """
    # Try to import the rendering engine (soft dependency)
    try:
        mod = import_module("templates.engine.render")
    except Exception as e:  # pragma: no cover - platform/import specific
        pytest.skip(f"templates.engine.render not importable: {e}")
        return

    if not hasattr(mod, engine_symbol):
        pytest.skip(f"templates.engine.render has no '{engine_symbol}'")
        return

    render_fn = getattr(mod, engine_symbol)

    entries = _iter_entries(_load_index())
    for entry in entries:
        tdir = _template_dir(entry)
        vars_spec = _load_json(tdir / "variables.json")
        vars_map = _collect_defaults(vars_spec)

        # If any enum variable has None, replace with first choice.
        for v in vars_spec.get("variables", []):
            if not isinstance(v, dict):
                continue
            if v.get("type") == "enum":
                name = v.get("name")
                choices = v.get("choices") or []
                if name and vars_map.get(name) in (None, "") and choices:
                    vars_map[name] = choices[0]

        with temp_dir(prefix=f"tmpl-{entry.get('id','unknown')}-") as outdir:
            # Try a couple of common call signatures
            called = False
            # Signature: (template_dir, output_dir, variables)
            try:
                render_fn(tdir, Path(outdir), vars_map)  # type: ignore[misc]
                called = True
            except TypeError:
                pass

            # Signature: keyword-only
            if not called:
                try:
                    render_fn(
                        template_dir=tdir,
                        output_dir=Path(outdir),
                        variables=vars_map,
                    )
                    called = True
                except TypeError:
                    pass

            # Signature: (template_dir, output_dir, **variables)
            if not called:
                try:
                    render_fn(tdir, Path(outdir), **vars_map)  # type: ignore[misc]
                    called = True
                except TypeError:
                    pass

            if not called:
                pytest.skip(
                    f"'{engine_symbol}' has an unexpected signature; "
                    "skipping dry-render checks"
                )
                continue

            # Out dir should have at least one file (not counting dotfiles)
            produced = [
                p for p in Path(outdir).rglob("*") if p.is_file() and not p.name.startswith(".")
            ]
            assert produced, f"Renderer produced no files for template {tdir}"


def test_index_only_references_existing_directories() -> None:
    """
    Ensure no phantom paths exist in index.json.
    """
    entries = _iter_entries(_load_index())
    for entry in entries:
        tdir = _template_dir(entry)
        assert tdir.is_dir(), f"Indexed path is not a directory: {tdir}"


def test_ids_are_unique_and_match_directory_names() -> None:
    """
    Enforce a gentle convention: either the directory name equals the 'id',
    or 'path' explicitly specifies a different directory. If 'path' is omitted,
    directory must match 'id'.
    """
    entries = _iter_entries(_load_index())
    seen = set()
    for entry in entries:
        tid = _required(entry, "id", str)
        assert tid not in seen, f"Duplicate id found: {tid}"
        seen.add(tid)

        path = entry.get("path")
        tdir = _template_dir(entry)
        if path is None:
            assert tdir.name == tid, (
                f"When 'path' is omitted, directory name must equal id: {tdir.name} != {tid}"
            )


def test_optional_tags_and_kind_types() -> None:
    """
    If present, 'tags' must be a list of short strings.
    If present, 'kind' must be a short slug-like identifier (e.g., 'contract', 'dapp').
    """
    entries = _iter_entries(_load_index())

    for entry in entries:
        tags = entry.get("tags")
        if tags is not None:
            assert isinstance(tags, list), "'tags' must be a list if present"
            assert all(isinstance(t, str) and t.strip() for t in tags), (
                "All tags must be non-empty strings"
            )
            assert all(len(t) <= 32 for t in tags), "Tags should be concise (<= 32 chars)"

        kind = entry.get("kind")
        if kind is not None:
            assert isinstance(kind, str), "'kind' must be a string if present"
            assert SLUG_RX.match(kind), f"'kind' should be slug-like: {kind!r}"


def test_variables_enums_have_valid_choices() -> None:
    """
    For enum variables, ensure 'choices' are unique and strings.
    """
    entries = _iter_entries(_load_index())
    for entry in entries:
        tdir = _template_dir(entry)
        vjson = _load_json(tdir / "variables.json")
        for v in vjson.get("variables", []):
            if not isinstance(v, dict):
                continue
            if v.get("type") != "enum":
                continue
            choices = v.get("choices", [])
            assert isinstance(choices, list) and choices, (
                f"Enum variable '{v.get('name')}' in {tdir} must have non-empty 'choices'"
            )
            assert all(isinstance(c, str) and c for c in choices), (
                f"Enum choices must be non-empty strings in {tdir}"
            )
            assert len(choices) == len(set(choices)), (
                f"Enum choices contain duplicates in {tdir}"
            )
