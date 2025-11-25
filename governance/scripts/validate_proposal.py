#!/usr/bin/env python3
"""
validate_proposal.py — schema check & param bounds validation

Usage:
  python governance/scripts/validate_proposal.py <path/to/proposal.{json|yaml|yml|md}> [--schemas-dir governance/schemas] [--bounds governance/registries/params_bounds.json] [--strict]

What it does:
1) Extracts a machine-readable proposal payload from:
   - JSON (.json)
   - YAML (.yaml/.yml)
   - Markdown with YAML front-matter (.md) — payload taken from the first --- ... --- block

2) Validates the payload against the appropriate JSON Schema found under --schemas-dir.
   Expected schema filenames (conventions; adjust if you rename):
     - upgrade.schema.json
     - param_change.schema.json
     - pq_rotation.schema.json
     - ballot.schema.json
     - tally.schema.json

   Dispatch is driven by proposal["type"] (e.g., "upgrade", "params", "param_change", "pq", "pq_rotation").

3) If the proposal is a parameter change, applies additional bounds checks using a
   machine-readable bounds registry (default: governance/registries/params_bounds.json).
   The bounds file shape (example):
     {
       "vm.blockGasLimit": { "min": 1000000, "max": 80000000, "step": 1000 },
       "fees.baseFeeMin": { "min": 0, "max": 1, "type": "float" },
       "pq.policy.rotationDays": { "min": 7, "max": 365 }
     }

Exit codes:
  0 = OK (schema-valid and bounds-valid)
  2 = Schema validation failed
  3 = Bounds validation failed (schema may still be valid)
  4 = I/O or usage error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import yaml  # PyYAML
except Exception as e:  # pragma: no cover
    print("ERROR: PyYAML is required. pip install pyyaml", file=sys.stderr)
    sys.exit(4)

try:
    from jsonschema import Draft202012Validator, RefResolver, exceptions as js_exceptions
except Exception as e:  # pragma: no cover
    print("ERROR: jsonschema is required. pip install jsonschema", file=sys.stderr)
    sys.exit(4)


# ----------------------------
# Helpers: load & extract data
# ----------------------------

FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*$", re.DOTALL | re.MULTILINE)


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to read {p}: {e}") from e


def load_payload(path: Path) -> Dict[str, Any]:
    """
    Returns a dict representing the proposal payload.
    For .md files, extracts the first YAML front-matter block.
    """
    suffix = path.suffix.lower()
    text = read_text(path)

    if suffix == ".json":
        return json.loads(text)

    if suffix in (".yaml", ".yml"):
        return yaml.safe_load(text) or {}

    if suffix == ".md":
        m = FRONT_MATTER_RE.search(text)
        if not m:
            raise RuntimeError(
                f"{path} looks like Markdown but has no YAML front-matter delimited by '---' lines."
            )
        try:
            return yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            raise RuntimeError(f"YAML front-matter parse error in {path}: {e}") from e

    raise RuntimeError(f"Unsupported file extension for {path.name}")


# ----------------------------
# Schema loading & validation
# ----------------------------

SCHEMA_NAME_MAP = {
    # proposal['type'] -> schema filename
    "upgrade": "upgrade.schema.json",
    "param_change": "param_change.schema.json",
    "params": "param_change.schema.json",
    "pq_rotation": "pq_rotation.schema.json",
    "pq": "pq_rotation.schema.json",
    "ballot": "ballot.schema.json",
    "tally": "tally.schema.json",
}


def guess_schema_filename(payload: Dict[str, Any]) -> str:
    t = (payload.get("type") or "").strip().lower()
    if not t:
        raise RuntimeError("Proposal payload missing required field: 'type'")
    fname = SCHEMA_NAME_MAP.get(t)
    if not fname:
        raise RuntimeError(
            f"Unknown proposal type '{t}'. Known types: {sorted(set(SCHEMA_NAME_MAP.keys()))}"
        )
    return fname


def load_schema(schemas_dir: Path, fname: str) -> Dict[str, Any]:
    path = schemas_dir / fname
    if not path.exists():
        raise RuntimeError(f"Schema not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to parse schema {path}: {e}") from e


def make_validator(schema: Dict[str, Any], base_uri: str) -> Draft202012Validator:
    resolver = RefResolver(base_uri=base_uri, referrer=schema)  # support $ref within the schemas dir
    return Draft202012Validator(schema, resolver=resolver)


def validate_against_schema(payload: Dict[str, Any], schemas_dir: Path, strict: bool) -> Tuple[bool, List[str], str]:
    fname = guess_schema_filename(payload)
    schema = load_schema(schemas_dir, fname)
    validator = make_validator(schema, base_uri=f"file://{schemas_dir.as_posix()}/")

    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
    msgs = []
    for err in errors:
        loc = "/".join([str(p) for p in err.path]) or "(root)"
        msgs.append(f"[schema] {loc}: {err.message}")

    if strict and not errors:
        # Optional: forbid unknown fields if schema doesn't already
        # Here we do a shallow check at top-level for unexpected keys.
        allowed = set(schema.get("properties", {}).keys())
        if allowed:
            extra = set(payload.keys()) - allowed
            if extra:
                msgs.append(f"[schema(strict)] unexpected top-level keys: {sorted(extra)}")

    return (len(msgs) == 0), msgs, fname


# ----------------------------
# Bounds validation for params
# ----------------------------

@dataclass
class BoundRule:
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None  # enforce value % step == 0 (for ints) or near-multiple for floats
    type: Optional[str] = None    # "int" | "float" | "number" | "string" | "bool"
    enum: Optional[List[Any]] = None


def _coerce_number(v: Any, typ: Optional[str]) -> Optional[float]:
    if typ in (None, "number", "float"):
        try:
            return float(v)
        except Exception:
            return None
    if typ == "int":
        try:
            iv = int(v)
            return float(iv)
        except Exception:
            return None
    return None


def load_bounds(bounds_path: Path) -> Dict[str, BoundRule]:
    if not bounds_path.exists():
        return {}
    try:
        raw = json.loads(bounds_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Failed to parse bounds file {bounds_path}: {e}") from e

    rules: Dict[str, BoundRule] = {}
    for k, v in raw.items():
        rules[k] = BoundRule(
            min=v.get("min"),
            max=v.get("max"),
            step=v.get("step"),
            type=v.get("type"),
            enum=v.get("enum"),
        )
    return rules


def _flatten_changes(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """
    Normalizes param change proposals into (key, newValue) pairs.

    Supported shapes:
      - payload["changes"] = [{"key": "vm.blockGasLimit", "newValue": 123}, ...]
      - payload["params"]  = {"vm.blockGasLimit": 123, ...}
    """
    pairs: List[Tuple[str, Any]] = []

    if isinstance(payload.get("changes"), list):
        for item in payload["changes"]:
            if not isinstance(item, dict):
                continue
            k = item.get("key") or item.get("name") or item.get("path")
            if k is None:
                continue
            pairs.append((str(k), item.get("newValue", item.get("value"))))
    elif isinstance(payload.get("params"), dict):
        for k, v in payload["params"].items():
            pairs.append((str(k), v))

    return pairs


def validate_bounds(payload: Dict[str, Any], rules: Dict[str, BoundRule]) -> List[str]:
    msgs: List[str] = []

    # Only apply for param changes
    kind = (payload.get("type") or "").lower()
    if kind not in ("param_change", "params", "param-change"):
        return msgs

    changes = _flatten_changes(payload)
    if not changes:
        msgs.append("[bounds] No parameter changes found (expected 'changes' array or 'params' object).")
        return msgs

    for key, value in changes:
        rule = rules.get(key)
        if rule is None:
            msgs.append(f"[bounds] WARNING: No bound rule for '{key}'. (Not failing; add to params_bounds.json)")
            continue

        # Enum constraint
        if rule.enum is not None:
            if value not in rule.enum:
                msgs.append(f"[bounds] {key}: value {value!r} not in enum {rule.enum}")
                continue  # further numeric checks not applicable

        # Type & numeric constraints
        num = _coerce_number(value, rule.type)
        if (rule.type in ("int", "float", "number", None)) and (rule.enum is None):
            if num is None:
                msgs.append(f"[bounds] {key}: value {value!r} is not a valid {rule.type or 'number'}")
                continue
            if rule.min is not None and num < float(rule.min):
                msgs.append(f"[bounds] {key}: {num} < min {rule.min}")
            if rule.max is not None and num > float(rule.max):
                msgs.append(f"[bounds] {key}: {num} > max {rule.max}")
            if rule.step:
                # For ints, enforce exact modulo; for floats, allow small epsilon.
                if (rule.type == "int") and (int(num) % int(rule.step) != 0):
                    msgs.append(f"[bounds] {key}: {int(num)} not a multiple of step {int(rule.step)}")
                elif rule.type in (None, "float", "number"):
                    eps = 1e-9
                    rem = (num / float(rule.step)) % 1.0
                    if min(rem, 1.0 - rem) > eps:
                        msgs.append(f"[bounds] {key}: {num} not aligned to step {rule.step}")
        elif rule.type == "bool":
            if not isinstance(value, bool):
                msgs.append(f"[bounds] {key}: expected boolean, got {type(value).__name__}")
        elif rule.type == "string":
            if not isinstance(value, str):
                msgs.append(f"[bounds] {key}: expected string, got {type(value).__name__}")
        # else: unknown type → no-op

    return msgs


# ----------------------------
# CLI
# ----------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate Animica governance proposals.")
    ap.add_argument("proposal", help="Path to proposal file (.json|.yaml|.yml|.md)")
    ap.add_argument("--schemas-dir", default="governance/schemas", help="Directory containing JSON Schemas")
    ap.add_argument("--bounds", default="governance/registries/params_bounds.json", help="Param bounds JSON")
    ap.add_argument("--strict", action="store_true", help="Enable extra strict checks for unknown top-level keys")
    args = ap.parse_args(argv)

    proposal_path = Path(args.proposal)
    schemas_dir = Path(args.schemas_dir)
    bounds_path = Path(args.bounds)

    try:
        payload = load_payload(proposal_path)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    # Schema validation
    ok_schema, schema_msgs, schema_file = False, [], ""
    try:
        ok_schema, schema_msgs, schema_file = validate_against_schema(payload, schemas_dir, strict=args.strict)
    except Exception as e:
        print(f"ERROR during schema validation: {e}", file=sys.stderr)
        return 2

    # Bounds validation
    bounds_msgs: List[str] = []
    try:
        rules = load_bounds(bounds_path)
        bounds_msgs = validate_bounds(payload, rules)
    except Exception as e:
        bounds_msgs = [f"[bounds] ERROR: {e}"]

    # Report
    report = {
        "proposal": str(proposal_path),
        "type": payload.get("type"),
        "schema": str((Path(args.schemas_dir) / (schema_file or "<unknown>")).as_posix()),
        "schemaValid": ok_schema and len(schema_msgs) == 0,
        "boundsFile": str(bounds_path.as_posix()),
        "boundsIssues": bounds_msgs,
        "issues": schema_msgs + bounds_msgs,
    }

    print(json.dumps(report, indent=2, sort_keys=False))

    # Exit code policy
    if not ok_schema or schema_msgs:
        return 2
    # If any bounds issue is a hard error (prefixed with "[bounds] " but not "WARNING")
    hard_bounds = [m for m in bounds_msgs if m and "WARNING" not in m]
    if hard_bounds:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
