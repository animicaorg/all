# -*- coding: utf-8 -*-
"""
templates.engine.variables

Helpers for loading, merging, coercing, and validating template variables.

This module is dependency-free and intentionally lightweight. It supports:
- Loading default variables from a template-local "_vars.json".
- Loading variables from JSON, .env/TXT, and optionally YAML files.
- Collecting variables from environment with a prefix (e.g., TPL_NAME=demo).
- Merging maps with deterministic precedence.
- Coercing values according to a (subset of) JSON Schema types.
- Validating variables against "templates/schemas/variables.schema.json" when present.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (Dict, Iterable, List, Mapping, MutableMapping, Optional,
                    Sequence, Tuple)

# ----------------------------- Data Structures ------------------------------


@dataclass
class ValidationReport:
    """Outcome of applying/validating variables against a schema."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    applied_defaults: Dict[str, str] = field(default_factory=dict)
    unknown_keys: List[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errors


# ----------------------------- File Loaders ---------------------------------


def load_defaults_from_template(template_dir: Path) -> Dict[str, str]:
    """
    Load template-local defaults from `<template>/_vars.json` if it exists.
    Values are stringified for deterministic substitution.
    """
    p = Path(template_dir) / "_vars.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse {p}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{p} must contain a JSON object at the top level")
    return {str(k): _coerce_str(v) for k, v in data.items()}


def load_vars_file(path: Path) -> Dict[str, str]:
    """
    Load a variables file. Supported formats:
      - .json : JSON object
      - .env/.txt : KEY=VALUE lines (ignores blank and '#' comments)
      - .yml/.yaml : requires PyYAML if available (optional)

    Returns a dict[str, str] with all values stringified.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Vars file not found: {path}")

    suf = path.suffix.lower()
    if suf == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return {str(k): _coerce_str(v) for k, v in data.items()}

    if suf in (".env", ".txt"):
        out: Dict[str, str] = {}
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                raise ValueError(f"{path}:{i}: expected KEY=VALUE")
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
        return out

    if suf in (".yml", ".yaml"):
        try:
            import yaml  # type: ignore
        except Exception:
            raise RuntimeError(
                f"{path} looks like YAML but PyYAML is not installed. "
                "Use JSON or .env format instead."
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping at the top level")
        return {str(k): _coerce_str(v) for k, v in data.items()}

    raise ValueError(f"Unsupported vars file type: {path.name}")


def load_vars_files(paths: Sequence[Path]) -> Dict[str, str]:
    """Load and merge multiple variable files, later files override earlier ones."""
    merged: Dict[str, str] = {}
    for p in paths:
        merged.update(load_vars_file(p))
    return merged


def load_from_env(prefix: Optional[str]) -> Dict[str, str]:
    """
    Collect environment variables with a given prefix.
    Example: prefix="TPL_" → TPL_NAME=demo → {"NAME": "demo"}.
    """
    if not prefix:
        return {}
    plen = len(prefix)
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k.startswith(prefix):
            key = k[plen:]
            if key:
                out[key] = v
    return out


# ----------------------------- Merging & Coercion ---------------------------


def merge_maps(*maps: Mapping[str, object]) -> Dict[str, str]:
    """
    Merge maps left→right (rightmost wins), converting values to strings.
    """
    out: Dict[str, str] = {}
    for m in maps:
        for k, v in m.items():
            out[str(k)] = _coerce_str(v)
    return out


def _coerce_str(v: object) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# ----------------------------- Schema Handling ------------------------------


def load_variables_schema(templates_root: Path) -> Optional[dict]:
    """
    Attempt to load a variables JSON Schema from:
        <templates_root>/schemas/variables.schema.json
    Returns the raw dict or None if not found.
    """
    p = Path(templates_root) / "schemas" / "variables.schema.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"Failed to parse schema at {p}: {e}") from e


def apply_schema(
    variables: Mapping[str, str],
    schema: Mapping[str, object],
    *,
    strict_unknown: bool = False,
) -> Tuple[Dict[str, str], ValidationReport]:
    """
    Apply a (subset of) JSON Schema rules to variables, injecting defaults and validating types.

    Supported keywords (per-property):
      - type: "string" | "integer" | "number" | "boolean"
      - enum: [..]
      - pattern: regex (Python re)
      - minLength, maxLength (strings)
      - minimum, maximum (numbers/integers)
      - default
    Document-level:
      - required: [..]
      - properties: {name: {..}}

    Returns (validated_vars, ValidationReport).
    Values are stored as strings after coercion.
    """
    props = _as_dict(schema.get("properties", {}))
    required = list(schema.get("required", []) or [])
    out: Dict[str, str] = dict(variables)  # already strings
    report = ValidationReport()

    # Inject defaults
    for name, spec in props.items():
        if name not in out and "default" in spec:
            val = _coerce_to_type(spec["default"], spec.get("type"))
            out[name] = _coerce_str(val)
            report.applied_defaults[name] = out[name]

    # Check required
    for name in required:
        if name not in out:
            report.errors.append(f"Missing required variable: {name}")

    # Validate/coerce known props
    for name, val in list(out.items()):
        spec = props.get(name)
        if not spec:
            if strict_unknown:
                report.unknown_keys.append(name)
            continue

        t = spec.get("type")
        try:
            coerced = _coerce_to_type(val, t)
        except ValueError as e:
            report.errors.append(f"{name}: {e}")
            continue

        # Enum
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            if coerced not in enum and str(coerced) not in [str(x) for x in enum]:
                report.errors.append(f"{name}: value {val!r} not in enum {enum}")

        # Strings: pattern, length
        if t == "string":
            s = str(coerced)
            pat = spec.get("pattern")
            if isinstance(pat, str):
                try:
                    if not re.fullmatch(pat, s):
                        report.errors.append(f"{name}: does not match pattern {pat!r}")
                except re.error as re_err:
                    report.warnings.append(
                        f"{name}: invalid pattern in schema: {re_err}"
                    )
            for key, fn in (("minLength", int), ("maxLength", int)):
                if key in spec:
                    try:
                        lim = fn(spec[key])
                        if key == "minLength" and len(s) < lim:
                            report.errors.append(
                                f"{name}: length {len(s)} < minLength {lim}"
                            )
                        if key == "maxLength" and len(s) > lim:
                            report.errors.append(
                                f"{name}: length {len(s)} > maxLength {lim}"
                            )
                    except Exception:
                        report.warnings.append(f"{name}: invalid {key} in schema")

        # Numbers/integers: bounds
        if t in ("number", "integer"):
            try:
                num = float(coerced)
            except Exception:
                report.errors.append(f"{name}: expected {t}, got {val!r}")
            else:
                for key, fn in (("minimum", float), ("maximum", float)):
                    if key in spec:
                        try:
                            lim = fn(spec[key])
                            if key == "minimum" and num < lim:
                                report.errors.append(f"{name}: {num} < minimum {lim}")
                            if key == "maximum" and num > lim:
                                report.errors.append(f"{name}: {num} > maximum {lim}")
                        except Exception:
                            report.warnings.append(f"{name}: invalid {key} in schema")

        # Persist coerced as string to keep substitution deterministic
        out[name] = _coerce_str(coerced)

    # Unknowns
    if strict_unknown and report.unknown_keys:
        for k in report.unknown_keys:
            report.errors.append(f"Unknown variable (not in schema): {k}")

    return out, report


def _as_dict(obj: object) -> Dict[str, dict]:
    if isinstance(obj, dict):
        return obj  # type: ignore[return-value]
    return {}


def _coerce_to_type(value: object, type_name: Optional[object]) -> object:
    """
    Coerce a raw value (often a string) to the target JSON Schema type.
    Returns the coerced Python value (may be str/bool/int/float).
    """
    t = type_name if isinstance(type_name, str) else None
    if t is None or t == "string":
        # Always keep as string
        return str(value)

    s = str(value).strip()

    if t == "boolean":
        lowered = s.lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"expected boolean, got {value!r}")

    if t == "integer":
        # permit decimal-only
        try:
            return int(s, 10)
        except Exception as e:
            raise ValueError(f"expected integer, got {value!r}") from e

    if t == "number":
        try:
            return float(s)
        except Exception as e:
            raise ValueError(f"expected number, got {value!r}") from e

    # Unknown type → keep as string but warn at call site if needed
    return s


# ------------------------ End-to-end Composition ----------------------------


def compute_effective_variables(
    *,
    template_dir: Path,
    user_vars: Optional[Mapping[str, object]] = None,
    vars_files: Optional[Sequence[Path]] = None,
    env_prefix: Optional[str] = "TPL_",
    templates_root: Path | str = "templates",
    strict_unknown: bool = False,
    apply_defaults: bool = True,
) -> Tuple[Dict[str, str], Optional[ValidationReport]]:
    """
    Compose variables from (defaults, env, files, user), then validate against schema if present.

    Precedence (low → high):
        defaults (_vars.json)  <  env (prefix)  <  vars_files[...]  <  user_vars

    Returns (final_vars, report_or_none). When no schema is present, report is None.
    """
    defaults = load_defaults_from_template(template_dir) if apply_defaults else {}
    env_map = load_from_env(env_prefix)
    file_map = load_vars_files(vars_files or [])
    usr_map = {str(k): _coerce_str(v) for k, v in (user_vars or {}).items()}

    merged = merge_maps(defaults, env_map, file_map, usr_map)

    schema = load_variables_schema(Path(templates_root))
    if not schema:
        return merged, None

    validated, report = apply_schema(merged, schema, strict_unknown=strict_unknown)
    return validated, report


# ------------------------------- __all__ ------------------------------------


__all__ = [
    "ValidationReport",
    "load_defaults_from_template",
    "load_vars_file",
    "load_vars_files",
    "load_from_env",
    "merge_maps",
    "load_variables_schema",
    "apply_schema",
    "compute_effective_variables",
]
