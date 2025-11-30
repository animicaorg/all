#!/usr/bin/env python3
"""
check_registry.py — lint governance registries (dup keys, out-of-range, simple sanity).

Usage:
  python governance/scripts/check_registry.py
      [--registries-dir governance/registries]
      [--bounds governance/registries/params_bounds.json]
      [--strict]
      [--pretty]

What it does:
1) Scans JSON files under --registries-dir and loads them with a duplicate-key
   detector. Reports any duplicate object keys with their paths.
2) If a bounds file is provided (default: params_bounds.json), validates any
   registry that looks like "params" (flat dotted keys → values) against the
   bounds (min/max/enum/step/type).
   • Accepts either:
       { "vm.blockGasLimit": 20_000_000, "fees.baseFeeMin": 0.0, ... }
     or:
       { "params": { "vm.blockGasLimit": 20_000_000, ... }, ... }
3) Light sanity checks for known registries (best-effort, non-fatal):
   • upgrade_paths.json — ensures unique edges and basic semver format.
   • contracts.json     — warns on duplicate "id" fields and empty addresses.

Exit codes:
  0 = no issues
  2 = duplicate keys found
  3 = bounds validation errors (schema may still be valid)
  4 = usage / I/O error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ----------------------------
# JSON loader with dup detection
# ----------------------------


@dataclass
class Dup:
    path: str
    key: str


def _obj_pairs_hook(stack: List[str], dups: List[Dup]):
    def hook(pairs: List[Tuple[str, Any]]):
        obj: Dict[str, Any] = {}
        seen: Dict[str, int] = {}
        for k, v in pairs:
            if k in seen:
                dups.append(Dup(path="/".join(stack) or "(root)", key=k))
            seen[k] = seen.get(k, 0) + 1
            obj[k] = v
        return obj

    return hook


def load_json_with_dups(path: Path) -> Tuple[Any, List[Dup]]:
    text = path.read_text(encoding="utf-8")
    dups: List[Dup] = []
    stack: List[str] = []

    # We need a recursive decoder to maintain a "stack" path. Use a small trick:
    # parse once to Python, then walk to discover dups already captured by hook.
    def _parse(s: str) -> Any:
        # The json library will call our hook for every object.
        return json.loads(
            s,
            object_pairs_hook=_obj_pairs_hook(stack, dups),  # type: ignore[arg-type]
        )

    try:
        data = _parse(text)
    except Exception as e:
        raise RuntimeError(f"Failed to parse JSON {path}: {e}")

    return data, dups


# ----------------------------
# Bounds support (reuse shape from validate_proposal.py)
# ----------------------------


@dataclass
class BoundRule:
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    type: Optional[str] = None  # "int" | "float" | "number" | "string" | "bool"
    enum: Optional[List[Any]] = None


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


def _extract_params_like(doc: Any) -> Dict[str, Any]:
    """
    Looks for a flat map of dotted keys to values, either at the root or under 'params'.
    Returns {} if the document doesn't look like a params registry.
    """
    if isinstance(doc, dict):
        if "params" in doc and isinstance(doc["params"], dict):
            # ensure this is flat dotted keys
            if all(isinstance(k, str) and "." in k for k in doc["params"].keys()):
                return doc["params"]
        # else try root
        if all(isinstance(k, str) and "." in k for k in doc.keys()):
            return doc  # type: ignore[return-value]
    return {}


def check_bounds(params_map: Dict[str, Any], rules: Dict[str, BoundRule]) -> List[str]:
    msgs: List[str] = []
    if not params_map:
        return msgs
    for key, value in params_map.items():
        rule = rules.get(key)
        if not rule:
            msgs.append(f"[bounds] WARNING: No rule for '{key}'")
            continue

        # Enums override
        if rule.enum is not None:
            if value not in rule.enum:
                msgs.append(f"[bounds] {key}: value {value!r} not in enum {rule.enum}")
            continue

        # Type checks
        if rule.type == "bool":
            if not isinstance(value, bool):
                msgs.append(
                    f"[bounds] {key}: expected boolean, got {type(value).__name__}"
                )
            continue
        if rule.type == "string":
            if not isinstance(value, str):
                msgs.append(
                    f"[bounds] {key}: expected string, got {type(value).__name__}"
                )
            continue

        # Numeric checks
        num = _coerce_number(value, rule.type)
        if num is None:
            msgs.append(
                f"[bounds] {key}: value {value!r} is not a valid {rule.type or 'number'}"
            )
            continue
        if rule.min is not None and num < float(rule.min):
            msgs.append(f"[bounds] {key}: {num} < min {rule.min}")
        if rule.max is not None and num > float(rule.max):
            msgs.append(f"[bounds] {key}: {num} > max {rule.max}")
        if rule.step:
            if rule.type == "int":
                if int(num) % int(rule.step) != 0:
                    msgs.append(
                        f"[bounds] {key}: {int(num)} not a multiple of {int(rule.step)}"
                    )
            else:
                eps = 1e-9
                rem = (num / float(rule.step)) % 1.0
                if min(rem, 1.0 - rem) > eps:
                    msgs.append(
                        f"[bounds] {key}: {num} not aligned to step {rule.step}"
                    )
    return msgs


# ----------------------------
# Light sanity for specific registries
# ----------------------------

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([\-+][0-9A-Za-z\.-]+)?$")


def check_upgrade_paths(doc: Any) -> List[str]:
    """
    Expected shape (flexible):
      { "paths": [ {"from":"0.12.0","to":"0.13.0"}, ... ] }
    or a list of edges directly.
    """
    msgs: List[str] = []
    edges: List[Tuple[str, str]] = []
    if isinstance(doc, dict) and isinstance(doc.get("paths"), list):
        for e in doc["paths"]:
            if isinstance(e, dict) and "from" in e and "to" in e:
                edges.append((str(e["from"]), str(e["to"])))
    elif isinstance(doc, list):
        for e in doc:
            if isinstance(e, dict) and "from" in e and "to" in e:
                edges.append((str(e["from"]), str(e["to"])))

    if not edges:
        return msgs

    seen = set()
    for a, b in edges:
        if (a, b) in seen:
            msgs.append(f"[upgrade_paths] duplicate edge {a} → {b}")
        seen.add((a, b))
        if not SEMVER_RE.match(a):
            msgs.append(f"[upgrade_paths] '{a}' is not semver-like")
        if not SEMVER_RE.match(b):
            msgs.append(f"[upgrade_paths] '{b}' is not semver-like")
        if a == b:
            msgs.append(f"[upgrade_paths] self-edge {a} → {b}")
    return msgs


def check_contracts(doc: Any) -> List[str]:
    """
    Expected flexible shapes:
      • { "contracts": [ { "id": "...", "address": "..." }, ... ] }
      • [ { "id": "...", "address": "..." }, ... ]
    Only uniqueness of 'id' and non-empty 'address' are enforced here.
    """
    msgs: List[str] = []
    rows: List[Dict[str, Any]] = []
    if isinstance(doc, dict) and isinstance(doc.get("contracts"), list):
        rows = [r for r in doc["contracts"] if isinstance(r, dict)]
    elif isinstance(doc, list):
        rows = [r for r in doc if isinstance(r, dict)]

    if not rows:
        return msgs

    ids = {}
    for i, r in enumerate(rows):
        cid = str(r.get("id", "")).strip()
        addr = str(r.get("address", "")).strip()
        if not cid:
            msgs.append(f"[contracts] row {i}: missing 'id'")
        else:
            if cid in ids:
                msgs.append(f"[contracts] duplicate id '{cid}' (rows {ids[cid]} & {i})")
            ids[cid] = i
        if not addr:
            msgs.append(f"[contracts] id '{cid}' has empty address")
    return msgs


# ----------------------------
# Walk registries dir
# ----------------------------


def iter_registry_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.glob("*.json")):
        yield p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Lint governance registries for duplicates and bounds."
    )
    ap.add_argument("--registries-dir", default="governance/registries")
    ap.add_argument("--bounds", default="governance/registries/params_bounds.json")
    ap.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args(argv)

    reg_dir = Path(args.registries_dir)
    if not reg_dir.exists():
        print(f"ERROR: registries dir not found: {reg_dir}", file=sys.stderr)
        return 4

    try:
        rules = load_bounds(Path(args.bounds))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 4

    overall_dups = 0
    bounds_errors = 0
    report: Dict[str, Any] = {"registriesDir": str(reg_dir), "files": []}

    for path in iter_registry_files(reg_dir):
        try:
            data, dups = load_json_with_dups(path)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 4

        file_msgs: List[str] = []
        if dups:
            overall_dups += len(dups)
            for d in dups:
                file_msgs.append(f"[dupe] {d.path}: key '{d.key}' repeated")

        # Params-like bounds checks
        params_map = _extract_params_like(data)
        if params_map:
            for m in check_bounds(params_map, rules):
                file_msgs.append(m)
                if (args.strict and "WARNING" in m) or (
                    "WARNING" not in m and m.startswith("[bounds]")
                ):
                    bounds_errors += 1

        # Targeted sanity by filename
        name = path.name.lower()
        if "upgrade" in name and "path" in name:
            for m in check_upgrade_paths(data):
                file_msgs.append(m)
                if args.strict:
                    bounds_errors += 1
        if "contract" in name:
            for m in check_contracts(data):
                file_msgs.append(m)
                if args.strict:
                    bounds_errors += 1

        report["files"].append(
            {
                "file": str(path),
                "issues": file_msgs,
            }
        )

    # Print report
    if args.pretty:
        print(json.dumps(report, indent=2))
    else:
        print(json.dumps(report))

    # Exit policy
    if overall_dups > 0:
        return 2
    if bounds_errors > 0:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
