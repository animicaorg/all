# -*- coding: utf-8 -*-
"""
abi_gen.py
==========

Generate a contract ABI JSON from a Python source file by extracting
function/event/error definitions from:

1) Lightweight decorators in the source (purely for *annotation*; the VM
   never imports these):
   -------------------------------------------------------------------
   @abi.fn(
       name="inc",
       inputs=[{"name": "delta", "type": "uint64"}],
       outputs=[{"name": "value", "type": "uint64"}],
       stateMutability="nonpayable",
   )
   def inc(delta: int) -> int:
       """Increment the counter."""
       ...

   @abi.event(
       name="Inc",
       inputs=[{"name": "by", "type": "uint64", "indexed": True}],
       anonymous=False,
   )
   def _emit_inc(by: int): ...

   @abi.error(name="Unauthorized", inputs=[{"name": "who", "type": "address"}])
   def _unauth(who: bytes): ...

   Supported decorator names (dotted or flat):
     - abi.fn / abi.function / abi_fn / abi_function
     - abi.event / abi_event
     - abi.error / abi_error

2) Docstring-embedded ABI blocks (JSON or Python-literal) on a function:
   -------------------------------------------------------------------
   def inc(delta: int) -> int:
       \"\"\"Increment the counter.

       ```abi
       {
         "type": "function",
         "name": "inc",
         "inputs": [{"name":"delta","type":"uint64"}],
         "outputs": [{"name":"value","type":"uint64"}],
         "stateMutability": "nonpayable"
       }
       ```
       \"\"\"
       ...

The script merges all discovered entries into one ABI list, validates
basic shapes, and outputs canonical JSON (stable key ordering, no
insignificant whitespace).

Usage
-----
python -m contracts.tools.abi_gen --source path/to/contract.py --out contracts/build/counter-abi.json
python -m contracts.tools.abi_gen --source path/to/contract.py --stdout
python -m contracts.tools.abi_gen --source path/to/contract.py --validate

If a schema exists at one of:
  - contracts/schemas/abi.schema.json
  - spec/abi.schema.json
it will be used for extra validation when --validate is set. Otherwise,
a built-in shallow validator runs.

Exit codes: 0 success; non-zero on errors.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from contracts.tools import (  # type: ignore
    atomic_write_text,
    canonical_json_str,
    ensure_dir,
    project_root,
)

# ------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------


def _load_text(p: Union[str, Path]) -> str:
    return Path(p).read_text(encoding="utf-8")


def _json_try_load(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _py_literal_try_eval(s: str) -> Optional[Any]:
    try:
        return ast.literal_eval(s)
    except Exception:
        return None


def _strip_code_fences(text: str) -> str:
    """
    If text is surrounded by a Markdown code fence (``` ... ```),
    return the inner content; otherwise return as-is.
    """
    fence = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*(.*?)\s*```\s*$", re.S)
    m = fence.match(text)
    return m.group(1) if m else text


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def _decorator_dotted_name(node: ast.AST) -> Optional[str]:
    """Resolve dotted name of a decorator target, e.g., abi.fn → 'abi.fn'."""
    if isinstance(node, ast.Attribute):
        base = _decorator_dotted_name(node.value)
        if base is None:
            return node.attr
        return base + "." + node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _normalize_deco_key(name: str) -> str:
    name = name.lower()
    name = name.replace("_", ".")
    return name


def _simplify_call_kwargs(call: ast.Call) -> Dict[str, Any]:
    """
    Convert a decorator call's keyword args into Python values using
    ast.literal_eval where possible. Positional args are ignored (discouraged).
    """
    out: Dict[str, Any] = {}
    for kw in call.keywords:
        if kw.arg is None:
            # **kwargs splat not supported in annotations
            continue
        try:
            out[kw.arg] = ast.literal_eval(kw.value)
        except Exception:
            # Fallback: try to stringify Name/Attribute for simple identifiers
            if isinstance(kw.value, ast.Name):
                out[kw.arg] = kw.value.id
            elif isinstance(kw.value, ast.Attribute):
                out[kw.arg] = _decorator_dotted_name(kw.value) or "<expr>"
            else:
                out[kw.arg] = "<expr>"
    return out


def _ensure_param_list(items: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in _as_list(items):
        if not isinstance(it, dict):
            raise ValueError(f"ABI param must be an object: got {type(it).__name__}")
        name = str(it.get("name", ""))
        typ = str(it.get("type", ""))
        if not name or not typ:
            raise ValueError("ABI param requires 'name' and 'type'")
        param = {"name": name, "type": typ}
        # Optional flags (events)
        if "indexed" in it:
            param["indexed"] = bool(it["indexed"])
        out.append(param)
    return out


def _mk_fn_entry(kwargs: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    name = str(kwargs.get("name", fallback_name))
    inputs = _ensure_param_list(kwargs.get("inputs", []))
    outputs = _ensure_param_list(kwargs.get("outputs", []))
    # Normalize mutability knobs
    mut = (
        kwargs.get("stateMutability")
        or kwargs.get("state_mutability")
        or ("view" if kwargs.get("view") else "payable" if kwargs.get("payable") else None)
        or "nonpayable"
    )
    if mut not in ("view", "nonpayable", "payable"):
        mut = "nonpayable"
    return {
        "type": "function",
        "name": name,
        "inputs": inputs,
        "outputs": outputs,
        "stateMutability": mut,
    }


def _mk_event_entry(kwargs: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    name = str(kwargs.get("name", fallback_name))
    inputs = _ensure_param_list(kwargs.get("inputs", []))
    anon = bool(kwargs.get("anonymous", False))
    return {"type": "event", "name": name, "inputs": inputs, "anonymous": anon}


def _mk_error_entry(kwargs: Dict[str, Any], fallback_name: str) -> Dict[str, Any]:
    name = str(kwargs.get("name", fallback_name))
    inputs = _ensure_param_list(kwargs.get("inputs", []))
    return {"type": "error", "name": name, "inputs": inputs}


# ------------------------------------------------------------------------------
# Extraction from AST
# ------------------------------------------------------------------------------


def _extract_from_decorators(fn: ast.FunctionDef) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for deco in fn.decorator_list:
        call: Optional[ast.Call] = None
        dotted: Optional[str] = None

        if isinstance(deco, ast.Call):
            call = deco
            dotted = _decorator_dotted_name(deco.func)
        else:
            dotted = _decorator_dotted_name(deco)

        if not dotted:
            continue

        key = _normalize_deco_key(dotted)
        if call is None and key.startswith("abi."):
            # Allow bare markers like @abi.fn without args → use defaults
            kwargs: Dict[str, Any] = {}
        elif call is not None:
            kwargs = _simplify_call_kwargs(call)
        else:
            kwargs = {}

        if key in ("abi.fn", "abi.function", "abi.fnction", "abi.fnction", "abi.fn_", "abi.abi_fn", "abi.function_") or key in ("abi_fn", "abi_function"):
            out.append(_mk_fn_entry(kwargs, fn.name))
        elif key in ("abi.event", "abi_event"):
            out.append(_mk_event_entry(kwargs, fn.name))
        elif key in ("abi.error", "abi_error"):
            out.append(_mk_error_entry(kwargs, fn.name))
        # else: ignore unrecognized decorators
    return out


_DOC_ABI_FENCE_RE = re.compile(
    r"```abi\s*(?P<body>.*?)```", re.IGNORECASE | re.DOTALL
)
_DOC_ABI_JSON_RE = re.compile(
    r"ABI\s*:\s*(?P<body>\{.*\}|\[.*\])", re.IGNORECASE | re.DOTALL
)


def _extract_from_docstring(fn: ast.FunctionDef) -> List[Dict[str, Any]]:
    """
    Parse function docstring for an ABI entry or entries.
    Accepts:
      - ```abi ...json... ```
      - 'ABI: { ... }' (JSON) or '[ ... ]'
      - 'ABI:' followed by a Python-literal dict/list (last resort)
    """
    out: List[Dict[str, Any]] = []
    doc = ast.get_docstring(fn) or ""
    if not doc:
        return out

    # Prefer fenced block
    m = _DOC_ABI_FENCE_RE.search(doc)
    body: Optional[str] = None
    if m:
        body = m.group("body")
    else:
        m2 = _DOC_ABI_JSON_RE.search(doc)
        if m2:
            body = m2.group("body")
        else:
            # Look for a block starting after a line with just "ABI:"
            m3 = re.search(r"^\s*ABI:\s*$([\s\S]*)", doc, re.IGNORECASE | re.MULTILINE)
            if m3:
                body = m3.group(1)

    if not body:
        return out

    raw = _strip_code_fences(body).strip()
    val: Any = _json_try_load(raw)
    if val is None:
        val = _py_literal_try_eval(raw)
    if val is None:
        return out

    items = _as_list(val)
    # Normalize single-object shapes (no 'type' → assume function)
    for item in items:
        if not isinstance(item, dict):
            continue
        if "type" not in item:
            item["type"] = "function"
        # Make sure basic fields exist
        t = item.get("type")
        n = item.get("name", fn.name)
        item["name"] = n
        if t == "function":
            item.setdefault("inputs", [])
            item.setdefault("outputs", [])
            item.setdefault("stateMutability", "nonpayable")
        elif t == "event":
            item.setdefault("inputs", [])
            item.setdefault("anonymous", False)
        elif t == "error":
            item.setdefault("inputs", [])
        out.append(item)
    return out


def extract_abi_from_source(src_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Walk the AST and gather ABI entries from decorators + docstrings.
    Deduplicate by (type, name, signature) where sensible.
    """
    text = _load_text(src_path)
    tree = ast.parse(text, filename=str(src_path))
    entries: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            entries.extend(_extract_from_decorators(node))
            entries.extend(_extract_from_docstring(node))

    # Dedup (type+name+inputs signature)
    seen: set = set()
    uniq: List[Dict[str, Any]] = []
    for e in entries:
        t = e.get("type")
        n = e.get("name")
        sig_key = []
        if t in ("function", "event", "error"):
            for p in e.get("inputs", []):
                sig_key.append((p.get("name", ""), p.get("type", ""), bool(p.get("indexed", False))))
        key = (t, n, tuple(sig_key), e.get("stateMutability") if t == "function" else None)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(e)

    return uniq


# ------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------


def _basic_validate(abi: List[Dict[str, Any]]) -> List[str]:
    """
    Very shallow shape checks; returns a list of warnings/errors.
    """
    errs: List[str] = []
    if not isinstance(abi, list):
        return ["ABI root must be a list"]

    for i, e in enumerate(abi):
        if not isinstance(e, dict):
            errs.append(f"[{i}] entry must be an object")
            continue
        t = e.get("type")
        if t not in ("function", "event", "error", "constructor"):
            errs.append(f"[{i}] unknown type={t!r}")
            continue
        name = e.get("name")
        if t != "constructor" and not isinstance(name, str):
            errs.append(f"[{i}] missing/invalid name")
        if t in ("function", "event", "error"):
            ins = e.get("inputs", [])
            if not isinstance(ins, list):
                errs.append(f"[{i}] inputs must be a list")
            else:
                for j, p in enumerate(ins):
                    if not isinstance(p, dict):
                        errs.append(f"[{i}].inputs[{j}] must be an object")
                        continue
                    if "name" not in p or "type" not in p:
                        errs.append(f"[{i}].inputs[{j}] requires name & type")
        if t == "function":
            mut = e.get("stateMutability", "nonpayable")
            if mut not in ("view", "nonpayable", "payable"):
                errs.append(f"[{i}] invalid stateMutability={mut!r}")
            outs = e.get("outputs", [])
            if not isinstance(outs, list):
                errs.append(f"[{i}] outputs must be a list")
            else:
                for j, p in enumerate(outs):
                    if not isinstance(p, dict) or "type" not in p:
                        errs.append(f"[{i}].outputs[{j}] requires type (and optional name)")
    return errs


def _try_schema_validate(abi: List[Dict[str, Any]], *, verbose: bool = False) -> List[str]:
    """
    If a JSON-Schema is present locally, use jsonschema (if installed) to validate.
    This is optional; failure to import jsonschema is a soft warning.
    """
    schema_paths = [
        project_root() / "contracts" / "schemas" / "abi.schema.json",
        project_root() / "spec" / "abi.schema.json",
    ]
    schema = None
    for p in schema_paths:
        if p.is_file():
            try:
                schema = json.loads(p.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    if not schema:
        return []

    try:
        import jsonschema  # type: ignore
    except Exception:
        return ["jsonschema not installed; skipped strict schema validation"]

    try:
        jsonschema.validate(instance=abi, schema=schema)  # type: ignore[attr-defined]
        return []
    except Exception as exc:
        return [f"Schema validation failed: {exc}"]


# ------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.tools.abi_gen",
        description="Emit ABI JSON by scanning decorators/docstrings in a contract source.",
    )
    p.add_argument("--source", type=Path, required=True, help="Path to contract source (.py)")
    p.add_argument("--out", type=Path, default=None, help="Output path for ABI JSON")
    p.add_argument("--stdout", action="store_true", help="Print ABI to stdout (canonical JSON)")
    p.add_argument("--validate", action="store_true", help="Run shape & optional schema validation")
    p.add_argument("--fail-on-warn", action="store_true", help="Non-zero exit on validation warnings")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    abi = extract_abi_from_source(args.source)
    if args.validate:
        issues = _basic_validate(abi)
        issues.extend(_try_schema_validate(abi))
        for w in issues:
            print(f"[abi_gen] WARN: {w}", file=sys.stderr)
        if args.fail_on_warn and issues:
            return 2

    data = canonical_json_str(abi)

    if args.stdout or not args.out:
        print(data)
    else:
        ensure_dir(Path(args.out).parent)
        atomic_write_text(Path(args.out), data)
        print(f"[abi_gen] Wrote ABI → {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
