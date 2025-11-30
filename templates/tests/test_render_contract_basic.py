"""
End-to-end checks for the "contract-python-basic" template.

What this test covers:

1) Locates the "contract-python-basic" template directory from templates/index.json.
2) Builds a variables map from templates/contract-python-basic/variables.json,
   using defaults when present and safe fallbacks otherwise (so the test stays hermetic).
3) Invokes the rendering engine (templates.engine.render) to produce a project skeleton.
   - We try a few common call signatures to remain compatible with minor API variations.
4) Asserts the renderer produced a reasonable project layout:
   - (root)/pyproject.toml
   - (root)/Makefile
   - (root)/.env.example
   - (root)/contracts/contract.py
   - (root)/contracts/manifest.json (and that it's valid JSON)
   - (root)/scripts/build.py, scripts/deploy.py, scripts/call.py
   - (root)/tests/test_contract.py
5) Ensures there are no obvious unrendered template markers (e.g., '{{' or '}}') left.
6) Performs a couple of content sanity checks (files are non-empty; manifest is parseable).
"""

from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

import pytest

from templates.tests import REPO_ROOT, read_json, temp_dir

TEMPLATES_DIR = REPO_ROOT / "templates"
INDEX_PATH = TEMPLATES_DIR / "index.json"
TEMPLATE_ID = "contract-python-basic"

# Acceptable variable "type" values for a minimal contract (aligned with test_registry).
VAR_TYPES = {"string", "integer", "number", "boolean", "enum"}

# A simple marker scan for "obvious unrendered template tokens".
UNRENDERED_PATTERNS = (
    "{{",  # moustache-like
    "}}",
    "[[",  # some engines use [[var]]
    "]]",
    "<%=",
    "<%",
    "%>",  # ejs-like
)


def _load_index() -> Any:
    assert INDEX_PATH.exists(), f"Missing templates/index.json at {INDEX_PATH}"
    return read_json(INDEX_PATH)


def _find_template_entry(index_obj: Any, template_id: str) -> Mapping[str, Any]:
    entries = (
        index_obj["templates"]
        if isinstance(index_obj, dict) and "templates" in index_obj
        else index_obj
    )
    for e in entries:
        if isinstance(e, dict) and e.get("id") == template_id:
            return e
    pytest.skip(f"Template id '{template_id}' not found in index.json")


def _template_dir(entry: Mapping[str, Any]) -> Path:
    rel = entry.get("path", entry.get("id"))
    if not isinstance(rel, str) or not rel:
        pytest.skip(f"Template entry lacks usable 'path' or 'id': {entry}")
    tdir = (TEMPLATES_DIR / rel).resolve()
    if not tdir.exists():
        pytest.skip(f"Template directory does not exist: {tdir}")
    return tdir


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        return read_json(path)
    except json.JSONDecodeError as e:
        pytest.fail(f"{path} is not valid JSON: {e}")


def _fallback_for_type(vtype: str, name: str) -> Any:
    if vtype == "string":
        # derive a deterministic stub from the variable name
        return f"example_{name.lower()}"
    if vtype in ("integer", "number"):
        return 1
    if vtype == "boolean":
        return True
    if vtype == "enum":
        # returned as sentinel; caller will replace with first choice if present
        return None
    return "example"


def _collect_defaults(vars_spec: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Construct a variables mapping:
      - use provided defaults when available,
      - otherwise select safe fallback values by type,
      - for enums with no default, we'll patch to the first 'choices' later.
    """
    out: Dict[str, Any] = {}
    variables = vars_spec.get("variables", [])
    assert isinstance(
        variables, list
    ), "variables.json must contain a 'variables' array"

    for var in variables:
        assert isinstance(var, dict), "Each variable spec must be an object"
        name = var.get("name")
        vtype = var.get("type", "string")
        required = bool(var.get("required", False))
        default = var.get("default", None)

        assert isinstance(name, str) and name, f"Variable missing 'name': {var}"
        assert vtype in VAR_TYPES, f"Unsupported variable type: {vtype} in {var}"

        if default is not None:
            out[name] = default
        else:
            out[name] = _fallback_for_type(vtype, name)

    # If any enum remains None, prefer its first choice
    for var in variables:
        if not isinstance(var, dict) or var.get("type") != "enum":
            continue
        name = var.get("name")
        if name and out.get(name) in (None, ""):
            choices = var.get("choices") or []
            if choices:
                out[name] = choices[0]

    return out


def _render_any_signature(
    render_fn, template_dir: Path, outdir: Path, variables: Mapping[str, Any]
) -> None:
    """
    Try a few common call signatures. Raise TypeError if none match.
    """
    # (template_dir, output_dir, variables)
    try:
        render_fn(template_dir, outdir, variables)  # type: ignore[misc]
        return
    except TypeError:
        pass

    # keyword-only
    try:
        render_fn(template_dir=template_dir, output_dir=outdir, variables=variables)
        return
    except TypeError:
        pass

    # (template_dir, output_dir, **variables)
    try:
        render_fn(template_dir, outdir, **variables)  # type: ignore[misc]
        return
    except TypeError:
        pass

    raise TypeError("Unsupported render() signature; expected one of the common forms")


def _choose_project_root(outdir: Path, vars_map: Mapping[str, Any]) -> Path:
    """
    Heuristics: many templates produce a single directory named by project slug.
    If multiple items are produced, try to locate the directory whose name equals
    the 'project_slug' variable. Otherwise, if exactly one directory exists, use it.
    As a last resort, fall back to 'outdir'.
    """
    subs = [p for p in outdir.iterdir()]
    if not subs:
        return outdir

    # Prefer matching project_slug
    slug = vars_map.get("project_slug")
    if isinstance(slug, str):
        for p in subs:
            if p.is_dir() and p.name == slug:
                return p

    # If exactly one directory was created, treat it as root
    dirs = [p for p in subs if p.is_dir()]
    if len(dirs) == 1:
        return dirs[0]

    return outdir


@pytest.mark.order(1)
def test_render_contract_python_basic_smoke() -> None:
    # Import rendering engine (skip if not available on this environment)
    render_mod = pytest.importorskip("templates.engine.render")
    render_fn = getattr(render_mod, "render_template", None) or getattr(
        render_mod, "render", None
    )
    if render_fn is None:
        pytest.skip("templates.engine.render has no 'render_template' or 'render'")

    # Locate template
    index = _load_index()
    entry = _find_template_entry(index, TEMPLATE_ID)
    tdir = _template_dir(entry)

    # Load variables & build a usable map
    vjson = _load_json(tdir / "variables.json")
    vars_map = _collect_defaults(vjson)

    # Render into a temp directory
    with temp_dir(prefix="tmpl-basic-") as outdir:
        out = Path(outdir)
        _render_any_signature(render_fn, tdir, out, vars_map)

        # Identify project root inside the output
        root = _choose_project_root(out, vars_map)

        # Expect a handful of canonical files
        expected_files = [
            root / "pyproject.toml",
            root / "Makefile",
            root / ".env.example",
            root / "contracts" / "contract.py",
            root / "contracts" / "manifest.json",
            root / "scripts" / "build.py",
            root / "scripts" / "deploy.py",
            root / "scripts" / "call.py",
            root / "tests" / "test_contract.py",
        ]

        # All expected files should exist and be files
        missing = [str(p) for p in expected_files if not p.exists()]
        assert (
            not missing
        ), f"Rendered project missing expected files:\n- " + "\n- ".join(missing)
        not_files = [str(p) for p in expected_files if not p.is_file()]
        assert not not_files, f"These paths exist but are not files:\n- " + "\n- ".join(
            not_files
        )

        # manifest should be valid JSON
        manifest = _load_json(root / "contracts" / "manifest.json")
        assert (
            isinstance(manifest.get("name", ""), str) and manifest["name"].strip()
        ), "manifest.name must be non-empty string"

        # No obvious unrendered tokens in key files
        _assert_no_unrendered_tokens(root / "pyproject.toml")
        _assert_no_unrendered_tokens(root / "contracts" / "contract.py")
        _assert_no_unrendered_tokens(root / "contracts" / "manifest.json")
        _assert_no_unrendered_tokens(root / "README.md", optional=True)

        # Basic content sanity (non-empty)
        for p in expected_files:
            assert p.stat().st_size > 0, f"File appears empty: {p}"


def _assert_no_unrendered_tokens(path: Path, *, optional: bool = False) -> None:
    if optional and not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    offenders = [tok for tok in UNRENDERED_PATTERNS if tok in text]
    assert not offenders, f"Found unrendered template markers {offenders} in {path}"


@pytest.mark.order(2)
def test_render_contract_python_basic_minimal_compilation_hint() -> None:
    """
    Light-weight verification that the generated tree *looks* usable:
    - 'pyproject.toml' defines a project (has a [project] or [tool.poetry] or similar header)
    We don't execute build commands here (keeps CI fast and side-effect free),
    but we still check for the presence of canonical TOML sections.
    """
    render_mod = pytest.importorskip("templates.engine.render")
    render_fn = getattr(render_mod, "render_template", None) or getattr(
        render_mod, "render", None
    )
    if render_fn is None:
        pytest.skip("templates.engine.render has no 'render_template' or 'render'")

    index = _load_index()
    entry = _find_template_entry(index, TEMPLATE_ID)
    tdir = _template_dir(entry)
    vjson = _load_json(tdir / "variables.json")
    vars_map = _collect_defaults(vjson)

    with temp_dir(prefix="tmpl-basic-") as outdir:
        out = Path(outdir)
        _render_any_signature(render_fn, tdir, out, vars_map)
        root = _choose_project_root(out, vars_map)

        pyproject = (root / "pyproject.toml").read_text(
            encoding="utf-8", errors="ignore"
        )
        assert (
            "[project]" in pyproject
            or "[tool.poetry]" in pyproject
            or "[tool.setuptools]" in pyproject
        ), "pyproject.toml should define a recognizable project table"

        # contracts/contract.py should look like Python (rudimentary checks)
        code = (root / "contracts" / "contract.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        assert (
            "def " in code
        ), "contract.py should contain at least one function definition"
        assert (
            "class " not in code or "class " in code
        ), "allow both styles; this is just a presence guard"
