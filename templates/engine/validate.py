# -*- coding: utf-8 -*-
"""
templates.engine.validate

Validation helpers for the template system:

- Structural checks for the templates/ tree and an individual template dir.
- Optional JSON Schema validation (uses jsonschema if available; otherwise
  falls back to lightweight sanity checks).
- Variable validation & normalization against a template's manifest
  (required keys, basic typing/enum/pattern/min/max, default filling).
- A small CLI so you can run validations locally or in CI.

Conventions assumed by this module
----------------------------------
* The repository contains a "templates/" directory at (or above) your CWD.
* Each concrete template lives in a subdirectory with (at minimum):
    templates/<template_name>/
      ├─ manifest.json           <-- template manifest (required)
      ├─ ... other files ...
      └─ _hooks.py               <-- optional hook file
* Shared JSON Schemas live at:
    templates/schemas/template.schema.json
    templates/schemas/variables.schema.json
* A catalog file may exist:
    templates/index.json         <-- optional, loosely validated

The manifest.json is expected to declare variable metadata under a top-level
"variables" object. This module supports a pragmatic subset of constraints
that is expressive yet dependency-free:

Per-variable fields (all optional unless stated):
    {
      "type": "string"|"boolean"|"integer"|"number"|"enum",
      "required": true|false,
      "default": <value>,
      "enum": ["a","b","c"],                 # when type == "enum"
      "pattern": "^[a-z0-9_-]+$",            # regex for strings
      "minLength": 1, "maxLength": 64,       # for strings
      "minimum": 0, "maximum": 10,           # for numbers/integers
      "description": "Human hint"
    }

If jsonschema is installed, manifest/variables are additionally validated
against the JSON-Schema files in templates/schemas. Otherwise only the
lightweight checks run.

Public API
----------
- ValidationIssue, ValidationReport (dataclasses)
- find_templates_root(start)
- validate_templates_root(templates_root) -> ValidationReport
- validate_template_dir(template_dir) -> ValidationReport
- validate_and_normalize_variables(manifest: dict, user_vars: Mapping[str, Any])
    -> (normalized: Dict[str, str], report: ValidationReport)
- validate_template(template_dir, user_vars=None)
    -> (normalized_vars, report)

CLI
---
python -m templates.engine.validate \
  --template templates/counter \
  --vars path/to/vars.json \
  --strict

Exit code: 0 on success, 1 on errors (or warnings if --strict).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# Optional JSON-Schema support
try:
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore
    _HAS_JSONSCHEMA = False


# --------------------------------------------------------------------------- #
# Dataclasses for reporting
# --------------------------------------------------------------------------- #


@dataclass
class ValidationIssue:
    level: str  # "error" | "warning" | "note"
    where: str  # file path or logical section
    message: str
    detail: Optional[Dict[str, Any]] = None


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)

    def add(self, level: str, where: str, message: str, **detail: Any) -> None:
        self.issues.append(ValidationIssue(level=level, where=where, message=message,
                                           detail=(detail or None)))

    def error(self, where: str, message: str, **detail: Any) -> None:
        self.add("error", where, message, **detail)

    def warn(self, where: str, message: str, **detail: Any) -> None:
        self.add("warning", where, message, **detail)

    def note(self, where: str, message: str, **detail: Any) -> None:
        self.add("note", where, message, **detail)

    @property
    def ok(self) -> bool:
        return all(i.level != "error" for i in self.issues)

    def has_warnings(self) -> bool:
        return any(i.level == "warning" for i in self.issues)

    def summarize(self) -> str:
        errs = sum(1 for i in self.issues if i.level == "error")
        warns = sum(1 for i in self.issues if i.level == "warning")
        notes = sum(1 for i in self.issues if i.level == "note")
        return f"{errs} errors, {warns} warnings, {notes} notes"

    def __bool.explain__(self) -> str:  # not used; reserved for debug
        return self.summarize()

    def dump_to_stderr(self) -> None:
        for i in self.issues:
            detail = f" | {json.dumps(i.detail)}" if i.detail else ""
            print(f"[{i.level.upper():7}] {i.where}: {i.message}{detail}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON at {path}: {e}") from e


def _is_dir_nonempty(d: Path) -> bool:
    return any(p for p in d.iterdir() if p.name not in {".DS_Store"})


def find_templates_root(start: Optional[Path] = None) -> Path:
    """
    Walk upward from `start` (or CWD) until a directory containing "templates/schemas"
    exists. Returns the path to ".../templates". Raises if not found.
    """
    here = (start or Path.cwd()).resolve()
    # If `start` is already the templates/ directory, accept it.
    if (here / "schemas").is_dir():
        return here

    for p in [here] + list(here.parents):
        tpl = p / "templates"
        if (tpl / "schemas").is_dir():
            return tpl.resolve()
    raise FileNotFoundError("Could not locate templates/ (with schemas/) above "
                            f"{(start or Path.cwd())}")


def _load_schema(templates_root: Path, name: str) -> Optional[dict]:
    """
    Load a JSON schema from templates/schemas/<name>. Returns dict or None if missing.
    """
    path = templates_root / "schemas" / name
    if not path.is_file():
        return None
    return _read_json(path)


# --------------------------------------------------------------------------- #
# Root & directory validation
# --------------------------------------------------------------------------- #


def validate_templates_root(templates_root: Path) -> ValidationReport:
    r = ValidationReport()

    if not templates_root.is_dir():
        r.error(str(templates_root), "templates/ directory not found")
        return r

    schemas_dir = templates_root / "schemas"
    if not schemas_dir.is_dir():
        r.error(str(schemas_dir), "schemas/ directory missing under templates/")
    else:
        # Check canonical schema files (optional but recommended)
        if not (schemas_dir / "template.schema.json").is_file():
            r.warn(str(schemas_dir / "template.schema.json"), "template.schema.json missing "
                  "(JSON-Schema checks for manifests will be skipped)")
        if not (schemas_dir / "variables.schema.json").is_file():
            r.warn(str(schemas_dir / "variables.schema.json"), "variables.schema.json missing "
                  "(JSON-Schema checks for variables will be skipped)")

    index_file = templates_root / "index.json"
    if index_file.exists():
        try:
            idx = _read_json(index_file)
            if not isinstance(idx, (list, dict)):
                r.error(str(index_file), "index.json must be a list or an object")
            else:
                # Light sanity: ensure entries have names/paths if list
                if isinstance(idx, list):
                    for n, entry in enumerate(idx):
                        if not isinstance(entry, dict):
                            r.warn(f"{index_file}[{n}]", "Entry should be an object", got=type(entry).__name__)
                            continue
                        if "name" not in entry or "path" not in entry:
                            r.warn(f"{index_file}[{n}]", "Entry should include 'name' and 'path'")
        except Exception as e:  # pragma: no cover
            r.error(str(index_file), f"Failed to parse: {e}")

    return r


def validate_template_dir(template_dir: Path) -> ValidationReport:
    r = ValidationReport()

    if not template_dir.is_dir():
        r.error(str(template_dir), "template directory does not exist")
        return r

    manifest = template_dir / "manifest.json"
    if not manifest.is_file():
        r.error(str(manifest), "manifest.json is required in each template directory")
    else:
        # Quick JSON parse
        try:
            data = _read_json(manifest)
            if not isinstance(data, dict):
                r.error(str(manifest), "manifest.json must be a JSON object")
            else:
                for key in ("name", "version"):
                    if key not in data:
                        r.warn(str(manifest), f"'{key}' is recommended in manifest.json")
                if "variables" in data and not isinstance(data["variables"], dict):
                    r.error(str(manifest), "'variables' must be an object mapping names to constraints")
        except Exception as e:  # pragma: no cover
            r.error(str(manifest), f"Failed to parse JSON: {e}")

    # Ensure directory has at least one non-meta file besides manifest/_hooks
    contentful = False
    for p in template_dir.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(template_dir)
        if rel.parts and rel.parts[0].startswith("."):
            continue
        if rel.name in {"manifest.json", "_hooks.py"}:
            continue
        contentful = True
        break
    if not contentful:
        r.warn(str(template_dir), "No content files found (only manifest/_hooks present?)")

    # Warn on suspicious absolute paths or traversal placeholders in file paths
    for p in template_dir.rglob("*"):
        rel = p.relative_to(template_dir)
        if any(part in {".."} for part in rel.parts):
            r.error(str(p), "Template contains parent-directory navigation in path", rel=str(rel))
        if p.is_file() and p.is_absolute():
            r.warn(str(p), "Absolute file path inside template (unexpected)")

    return r


# --------------------------------------------------------------------------- #
# JSON-Schema validator (optional)
# --------------------------------------------------------------------------- #


def _schema_validate(instance: Any, schema: Optional[dict], *, where: str, r: ValidationReport) -> None:
    if not schema or not _HAS_JSONSCHEMA:
        return
    try:
        # Try Draft 2020-12 if present; fallback to default validator
        resolver = jsonschema.RefResolver.from_schema(schema)  # type: ignore[attr-defined]
        jsonschema.validate(instance=instance, schema=schema, resolver=resolver)  # type: ignore
    except Exception as e:
        r.error(where, f"JSON-Schema validation failed: {e}")


# --------------------------------------------------------------------------- #
# Variable typing & normalization (dependency-free)
# --------------------------------------------------------------------------- #


_TRUE = {"1", "true", "t", "yes", "y", "on"}
_FALSE = {"0", "false", "f", "no", "n", "off"}


def _to_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _coerce_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _coerce_int(v: Any) -> Optional[int]:
    try:
        if isinstance(v, bool) or v is None:
            return None
        return int(str(v), 10)
    except Exception:
        return None


def _coerce_float(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool) or v is None:
            return None
        return float(str(v))
    except Exception:
        return None


def _apply_string_constraints(name: str, s: str, spec: dict, r: ValidationReport, where: str) -> None:
    if "minLength" in spec:
        min_len = int(spec["minLength"])
        if len(s) < min_len:
            r.error(where, f"'{name}' shorter than minLength={min_len}", value=s)
    if "maxLength" in spec:
        max_len = int(spec["maxLength"])
        if len(s) > max_len:
            r.error(where, f"'{name}' longer than maxLength={max_len}", value=s)
    if "pattern" in spec:
        try:
            if not re.fullmatch(spec["pattern"], s):
                r.error(where, f"'{name}' does not match pattern", pattern=spec["pattern"], value=s)
        except re.error as e:
            r.warn(where, f"Invalid regex 'pattern' for '{name}': {e}")


def _apply_numeric_constraints(name: str, x: float, spec: dict, r: ValidationReport, where: str) -> None:
    if "minimum" in spec and x < float(spec["minimum"]):
        r.error(where, f"'{name}' below minimum", minimum=spec["minimum"], value=x)
    if "maximum" in spec and x > float(spec["maximum"]):
        r.error(where, f"'{name}' above maximum", maximum=spec["maximum"], value=x)


def validate_and_normalize_variables(
    manifest: Mapping[str, Any],
    user_vars: Mapping[str, Any],
    *,
    where: str = "variables",
) -> Tuple[Dict[str, str], ValidationReport]:
    """
    Merge defaults from manifest['variables'] and validate `user_vars`.
    Returns (normalized_vars_as_strings, report).
    """
    r = ValidationReport()
    vars_section = manifest.get("variables", {}) if isinstance(manifest, dict) else {}

    if not isinstance(vars_section, Mapping):
        r.error("manifest", "'variables' must be an object")
        # Minimal salvage: stringify incoming
        return ({str(k): _to_str(v) for k, v in (user_vars or {}).items()}, r)

    normalized: Dict[str, str] = {}

    # Pass 1: apply defaults & type-check any provided values
    for name, spec in vars_section.items():
        if not isinstance(spec, Mapping):
            r.warn(where, f"Variable spec for '{name}' must be an object", got=type(spec).__name__)
            continue

        required = bool(spec.get("required", False))
        vtype = spec.get("type", "string")
        enum_vals = spec.get("enum") if vtype == "enum" else None

        provided = name in user_vars
        raw = user_vars.get(name, None)
        value_written = False

        # Decide the value: provided > default > missing
        if provided:
            # Validate/normalize by type
            if vtype == "string" or vtype is None:
                s = str(raw)
                _apply_string_constraints(name, s, spec, r, where)
                normalized[name] = s
                value_written = True
            elif vtype == "boolean":
                b = _coerce_bool(raw)
                if b is None:
                    r.error(where, f"'{name}' must be boolean-like (true/false/yes/no/1/0)")
                else:
                    normalized[name] = "true" if b else "false"
                    value_written = True
            elif vtype == "integer":
                iv = _coerce_int(raw)
                if iv is None:
                    r.error(where, f"'{name}' must be an integer")
                else:
                    _apply_numeric_constraints(name, float(iv), spec, r, where)
                    normalized[name] = str(iv)
                    value_written = True
            elif vtype == "number":
                fv = _coerce_float(raw)
                if fv is None:
                    r.error(where, f"'{name}' must be a number")
                else:
                    _apply_numeric_constraints(name, float(fv), spec, r, where)
                    # Keep canonical representation (no trailing .0 when int)
                    normalized[name] = str(int(fv)) if fv.is_integer() else str(fv)
                    value_written = True
            elif vtype == "enum":
                if not isinstance(enum_vals, list) or not enum_vals:
                    r.error(where, f"'{name}' enum missing or empty in manifest")
                else:
                    raw_s = str(raw)
                    if raw_s not in map(str, enum_vals):
                        r.error(where, f"'{name}' must be one of {enum_vals}", got=raw_s)
                    else:
                        normalized[name] = raw_s
                        value_written = True
            else:
                r.warn(where, f"Unknown type '{vtype}' for variable '{name}', coercing to string")
                normalized[name] = str(raw)
                value_written = True
        else:
            # Not provided by user; try default
            if "default" in spec:
                d = spec["default"]
                # We still enforce constraints & stringify
                if vtype == "boolean":
                    b = _coerce_bool(d)
                    if b is None:
                        r.warn(where, f"Default for '{name}' is not boolean-like; using string")
                        normalized[name] = _to_str(d)
                    else:
                        normalized[name] = "true" if b else "false"
                elif vtype == "integer":
                    iv = _coerce_int(d)
                    if iv is None:
                        r.warn(where, f"Default for '{name}' is not an integer; using string")
                        normalized[name] = _to_str(d)
                    else:
                        normalized[name] = str(iv)
                elif vtype == "number":
                    fv = _coerce_float(d)
                    if fv is None:
                        r.warn(where, f"Default for '{name}' is not a number; using string")
                        normalized[name] = _to_str(d)
                    else:
                        normalized[name] = str(int(fv)) if fv.is_integer() else str(fv)
                elif vtype == "enum":
                    if not isinstance(enum_vals, list) or not enum_vals:
                        r.error(where, f"'{name}' enum missing or empty in manifest")
                        normalized[name] = _to_str(d)
                    else:
                        ds = _to_str(d)
                        if ds not in map(str, enum_vals):
                            r.error(where, f"Default for '{name}' not in enum {enum_vals}", default=ds)
                            normalized[name] = ds
                        else:
                            normalized[name] = ds
                else:  # string or unknown
                    s = _to_str(d)
                    _apply_string_constraints(name, s, spec, r, where)
                    normalized[name] = s
                value_written = True

            # If still missing and required, raise error
            if not value_written and required:
                r.error(where, f"Required variable '{name}' is missing")

    # Pass 2: include unknown variables from user (stringified) with a note
    for name, raw in user_vars.items():
        if name in normalized or name in vars_section:
            continue
        normalized[name] = _to_str(raw)
        r.note(where, f"Ignoring unknown variable not declared in manifest: '{name}'")

    return normalized, r


# --------------------------------------------------------------------------- #
# High-level validation helpers
# --------------------------------------------------------------------------- #


def validate_template(
    template_dir: Path,
    user_vars: Optional[Mapping[str, Any]] = None,
    *,
    templates_root: Optional[Path] = None,
) -> Tuple[Dict[str, str], ValidationReport]:
    """
    Validate the template directory and (optionally) a set of user variables
    against the template's manifest and repository schemas.

    Returns (normalized_vars, report).
    """
    report = ValidationReport()
    troot = templates_root or find_templates_root(template_dir)
    report_root = validate_templates_root(troot)
    report.issues.extend(report_root.issues)

    report_dir = validate_template_dir(template_dir)
    report.issues.extend(report_dir.issues)

    manifest_path = template_dir / "manifest.json"
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = _read_json(manifest_path)
        except Exception as e:
            report.error(str(manifest_path), f"Failed to read manifest: {e}")
            # Proceed with empty manifest

    # JSON-Schema validation (optional)
    tpl_schema = _load_schema(troot, "template.schema.json")
    _schema_validate(manifest, tpl_schema, where=str(manifest_path), r=report)

    normalized: Dict[str, str] = {}
    if user_vars is not None:
        normalized, vr = validate_and_normalize_variables(manifest, user_vars, where=str(manifest_path))
        report.issues.extend(vr.issues)

        # Optionally validate variables against variables.schema.json as a whole object
        var_schema = _load_schema(troot, "variables.schema.json")
        if var_schema and _HAS_JSONSCHEMA:
            try:
                jsonschema.validate(instance=normalized, schema=var_schema)  # type: ignore
            except Exception as e:
                report.error("variables", f"variables.schema.json validation failed: {e}")

    return normalized, report


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate a template directory and variables")
    p.add_argument("--template", "-t", type=str, required=True,
                   help="Path to a template directory (e.g., templates/counter)")
    p.add_argument("--vars", "-v", type=str,
                   help="Path to a JSON file with variables to validate/normalize")
    p.add_argument("--print", action="store_true",
                   help="Print normalized variables as JSON on success")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero on warnings as well as errors")
    return p.parse_args(list(argv) if argv is not None else None)


def _main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    template_dir = Path(args.template).resolve()

    user_vars: Optional[Dict[str, Any]] = None
    if args.vars:
        try:
            user_vars = _read_json(Path(args.vars))
            if not isinstance(user_vars, dict):
                print(f"--vars JSON must be an object (got {type(user_vars).__name__})", file=sys.stderr)
                return 1
        except Exception as e:
            print(f"Failed to read --vars: {e}", file=sys.stderr)
            return 1

    normalized, report = validate_template(template_dir, user_vars=user_vars)
    report.dump_to_stderr()

    if report.ok and args.print:
        print(json.dumps(normalized, indent=2, sort_keys=True))

    if not report.ok:
        return 1
    if args.strict and report.has_warnings():
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
