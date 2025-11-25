"""
Animica SDK — Multi-language Codegen CLI
========================================

Generate strongly-typed contract client stubs from a normalized ABI IR.

Usage:
    python -m sdk.codegen.cli --lang py --abi path/to/abi.json --out out_dir [--class Counter]
    python -m sdk.codegen.cli --lang ts --abi path/to/abi.json --out out_dir [--class Counter]
    python -m sdk.codegen.cli --lang rs --abi path/to/abi.json --out out_dir [--class Counter]

Notes
-----
- The input should be a *normalized* ABI IR (see sdk/codegen/common/normalize.py).
  If you provide a raw ABI-like object, we'll try to normalize it.
- Outputs:
    * py:  <out>/<snake_class>.py
    * ts:  <out>/<Class>.ts
    * rs:  <out>/<snake_class>.rs
- Defaults use the SDK base client in each language:
    * Python:  base_import="omni_sdk.contracts.client", base_class="ContractClient"
    * TS:      base_import="@animica/sdk/contracts/client", base_class="ContractClient"
    * Rust:    base_path="animica_sdk::contracts::client", base_class="ContractClient"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- Helpers: identifiers & types ------------------------------------------------

_RS_KEYWORDS = {
    "as","break","const","continue","crate","else","enum","extern","false","fn","for","if","impl",
    "in","let","loop","match","mod","move","mut","pub","ref","return","self","Self","static","struct",
    "super","trait","true","type","unsafe","use","where","while","async","await","dyn","abstract","become",
    "box","do","final","macro","override","priv","typeof","unsized","virtual","yield","try"
}
_TS_KEYWORDS = {
    "break","case","catch","class","const","continue","debugger","default","delete","do","else","enum",
    "export","extends","false","finally","for","function","if","import","in","instanceof","new","null",
    "return","super","switch","this","throw","true","try","typeof","var","void","while","with","as",
    "implements","interface","let","package","private","protected","public","static","yield","any","boolean",
    "constructor","declare","get","module","require","number","set","string","symbol","type","from","of"
}
_PY_KEYWORDS = {
    "False","None","True","and","as","assert","async","await","break","class","continue","def","del",
    "elif","else","except","finally","for","from","global","if","import","in","is","lambda","nonlocal",
    "not","or","pass","raise","return","try","while","with","yield"
}


def snake(s: str) -> str:
    out = []
    prev_lower = False
    for ch in s:
        if ch.isalnum():
            if ch.isupper() and prev_lower:
                out.append("_")
            out.append(ch.lower())
            prev_lower = ch.islower()
        else:
            out.append("_")
            prev_lower = False
    name = "".join(out).strip("_")
    if not name:
        name = "client"
    if name in {"class", "def", "return"}:
        name += "_"
    if name[0].isdigit():
        name = "_" + name
    return name


def py_ident(s: str) -> str:
    s2 = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s.strip())
    if not s2:
        s2 = "x"
    if s2 in _PY_KEYWORDS:
        s2 += "_"
    if s2[0].isdigit():
        s2 = "_" + s2
    return s2


def ts_ident(s: str) -> str:
    s2 = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s.strip())
    if not s2:
        s2 = "x"
    if s2 in _TS_KEYWORDS:
        s2 += "_"
    if s2[0].isdigit():
        s2 = "_" + s2
    return s2


def rs_ident(s: str) -> str:
    s2 = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s.trim())
    if not s2:
        s2 = "x"
    if s2 in _RS_KEYWORDS:
        s2 += "_"
    if s2[0].isdigit():
        s2 = "_" + s2
    return s2


def method_name(name: str, discriminator: Optional[str]) -> str:
    return (name if not discriminator else f"{name}_{discriminator}")


# --- Load & normalize ABI --------------------------------------------------------

def load_json(path: str) -> Any:
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_ir(raw: Any) -> Dict[str, Any]:
    """
    Try to normalize using the repo's normalizer; if it's not available,
    assume the input is already normalized.
    """
    try:
        from sdk.codegen.common.normalize import normalize_abi  # type: ignore
        return normalize_abi(raw)
    except Exception:
        # Best-effort: if the shape looks like it's already normalized, return as-is.
        if isinstance(raw, dict) and "functions" in raw and "events" in raw:
            return raw  # type: ignore
        raise SystemExit(
            "Provided ABI does not look normalized and normalizer is unavailable. "
            "Please ensure ABI IR matches sdk/codegen/common/normalize.py."
        )


# --- Type mapping helpers --------------------------------------------------------

def py_type(t: Dict[str, Any]) -> str:
    kind = t.get("kind")
    if kind in ("uint", "int"):
        return "int"
    if kind == "bool":
        return "bool"
    if kind == "string":
        return "str"
    if kind == "address":
        return "str"
    if kind == "bytes":
        return "bytes | str"
    if kind == "array":
        inner = py_type(t.get("array_item") or {})
        return f"list[{inner}]"
    if kind == "tuple":
        elems = [py_type(x) for x in (t.get("tuple_elems") or [])]
        return f"tuple[{', '.join(elems)}]" if elems else "tuple[()]"
    return "object"


def ts_type(t: Dict[str, Any]) -> str:
    kind = t.get("kind")
    if kind in ("uint", "int"):
        return "bigint"
    if kind == "bool":
        return "boolean"
    if kind == "string":
        return "string"
    if kind == "address":
        return "string"
    if kind == "bytes":
        return "Uint8Array | string"
    if kind == "array":
        inner = ts_type(t.get("array_item") or {})
        return f"Array<{inner}>"
    if kind == "tuple":
        elems = [ts_type(x) for x in (t.get("tuple_elems") or [])]
        return f"[{', '.join(elems)}]" if elems else "[]"
    return "unknown"


def rs_scalar_ty(kind: str, bits: Optional[int], signed: bool) -> str:
    b = bits or 256
    table = [
        (8, "i8" if signed else "u8"),
        (16, "i16" if signed else "u16"),
        (32, "i32" if signed else "u32"),
        (64, "i64" if signed else "u64"),
        (128, "i128" if signed else "u128"),
    ]
    for limit, ty in table:
        if b <= limit:
            return ty
    return "String"  # fall back to string for bignums


def rs_type(t: Dict[str, Any]) -> str:
    kind = t.get("kind")
    if kind == "uint":
        return rs_scalar_ty("uint", t.get("bits"), False)
    if kind == "int":
        return rs_scalar_ty("int", t.get("bits"), True)
    if kind == "bool":
        return "bool"
    if kind == "string":
        return "String"
    if kind == "address":
        return "String"
    if kind == "bytes":
        return "Vec<u8>"
    if kind == "array":
        inner = rs_type(t.get("array_item") or {})
        return f"Vec<{inner}>"
    if kind == "tuple":
        elems = [rs_type(x) for x in (t.get("tuple_elems") or [])]
        return f"({', '.join(elems)})" if elems else "()"
    return "serde_json::Value"


# --- Emitters -------------------------------------------------------------------

def emit_python(ir: Dict[str, Any], class_name: str,
                base_import: str = "omni_sdk.contracts.client",
                base_class: str = "ContractClient") -> str:
    sel_map = {method_name(f["name"], f.get("discriminator")): f["selector"] for f in ir["functions"]}
    topic_map = {method_name(e["name"], e.get("discriminator")): e["topic_id"] for e in ir.get("events", [])}

    lines: List[str] = []
    lines.append("# This file was generated by Animica SDK codegen (Python). Do not edit by hand.\n")
    lines.append("from __future__ import annotations\n")
    lines.append("from typing import Any, Optional, Tuple\n")
    lines.append(f"from {base_import} import {base_class}\n")
    lines.append("import json\n")
    lines.append("\n")
    abi_str = json.dumps(ir, separators=(",", ":"), ensure_ascii=False)
    lines.append(f"ABI_JSON: str = {json.dumps(abi_str)}\n")
    lines.append("ABI: dict[str, Any] = json.loads(ABI_JSON)\n")
    lines.append(f"SELECTORS: dict[str, str] = {json.dumps(sel_map, separators=(',',':'))}\n")
    lines.append(f"TOPICS: dict[str, str] = {json.dumps(topic_map, separators=(',',':'))}\n")
    lines.append("\n")
    lines.append(f"class {class_name}({base_class}):\n")
    lines.append(f"    def __init__(self, address: str, **opts: Any) -> None:\n")
    lines.append(f"        super().__init__(address, ABI, **opts)\n\n")
    for f in ir["functions"]:
        mname = py_ident(method_name(f["name"], f.get("discriminator")))
        params = ", ".join(f"{py_ident(p['name'])}: {py_type(p['type'])}" for p in f["inputs"])
        if params:
            params = ", " + params
        is_view = f["state_mutability"] in ("view", "pure")
        ret: str
        if is_view:
            if len(f["outputs"]) == 0:
                ret = "Any"
            elif len(f["outputs"]) == 1:
                ret = py_type(f["outputs"][0])
            else:
                ret = "Tuple[" + ", ".join(py_type(o) for o in f["outputs"]) + "]"
        else:
            ret = "dict[str, Any] | Any"
        lines.append(f"    def {mname}(self{params}) -> {ret}:\n")
        arg_list = ", ".join(py_ident(p["name"]) for p in f["inputs"])
        inv = "call" if is_view else "transact"
        lines.append(f"        return self.{inv}({json.dumps(f['name'])}, [{arg_list}])\n\n")
    return "".join(lines)


def emit_typescript(ir: Dict[str, Any], class_name: str,
                    base_import: str = "@animica/sdk/contracts/client",
                    base_class: str = "ContractClient") -> str:
    sel_map = {method_name(f["name"], f.get("discriminator")): f["selector"] for f in ir["functions"]}
    topic_map = {method_name(e["name"], e.get("discriminator")): e["topic_id"] for e in ir.get("events", [])}

    abi_str = json.dumps(ir, separators=(",", ":"), ensure_ascii=False)
    lines: List[str] = []
    lines.append("// This file was generated by Animica SDK codegen (TypeScript). Do not edit by hand.\n")
    lines.append("import type { Abi } from '@animica/sdk/types/abi';\n")
    lines.append(f"import {{ {base_class} }} from '{base_import}';\n\n")
    lines.append(f"export const ABI: Abi = {abi_str} as const;\n")
    lines.append(f"export const SELECTORS = {json.dumps(sel_map, separators=(',',':'))} as const;\n")
    lines.append(f"export const TOPICS = {json.dumps(topic_map, separators=(',',':'))} as const;\n\n")
    lines.append(f"export default class {class_name} extends {base_class} {{\n")
    lines.append("  constructor(address: string, opts: Partial<{ rpcUrl: string; chainId: number; signer: any; httpClient: any; }> = {}) {\n")
    lines.append("    super(address, ABI, opts);\n")
    lines.append("  }\n\n")
    for f in ir["functions"]:
        mname = ts_ident(method_name(f["name"], f.get("discriminator")))
        params = ", ".join(f"{ts_ident(p['name'])}: {ts_type(p['type'])}" for p in f["inputs"])
        is_view = f["state_mutability"] in ("view", "pure")
        if is_view:
            if len(f["outputs"]) == 0:
                ret = "Promise<unknown>"
            elif len(f["outputs"]) == 1:
                ret = f"Promise<{ts_type(f['outputs'][0])}>"
            else:
                ret = "Promise<[" + ", ".join(ts_type(o) for o in f["outputs"]) + "]>"
        else:
            ret = "Promise<unknown>"
        arg_list = ", ".join(ts_ident(p["name"]) for p in f["inputs"])
        inv = "call" if is_view else "transact"
        lines.append(f"  async {mname}({params}): {ret} {{\n")
        lines.append(f"    return this.{inv}({json.dumps(f['name'])}, [{arg_list}]);\n")
        lines.append("  }\n\n")
    lines.append("}\n")
    return "".join(lines)


def emit_rust(ir: Dict[str, Any], class_name: str,
              base_path: str = "animica_sdk::contracts::client",
              base_class: str = "ContractClient",
              address_type: str = "String") -> str:
    abi_str = json.dumps(ir, separators=(",", ":"), ensure_ascii=False).replace('"', '\\"')
    sel_map = {method_name(f["name"], f.get("discriminator")): f["selector"] for f in ir["functions"]}
    topic_map = {method_name(e["name"], e.get("discriminator")): e["topic_id"] for e in ir.get("events", [])}

    def rs_string(s: str) -> str:
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines: List[str] = []
    lines.append("//! This file was generated by Animica SDK codegen (Rust). Do not edit by hand.\n\n")
    lines.append(f"use {base_path}::{base_class};\n")
    lines.append("use serde_json::Value as Json;\nuse std::collections::BTreeMap;\n\n")
    lines.append(f"pub static ABI_JSON: &str = r#\"{abi_str}\"#;\n")
    lines.append("pub fn abi_json() -> &'static str { ABI_JSON }\n")
    lines.append("pub fn abi_value() -> Json { serde_json::from_str(ABI_JSON).expect(\"ABI_JSON\") }\n\n")
    lines.append("pub fn selectors() -> BTreeMap<&'static str, &'static str> {\n  let mut m = BTreeMap::new();\n")
    for k, v in sel_map.items():
        lines.append(f"  m.insert({rs_string(k)}, {rs_string(v)});\n")
    lines.append("  m\n}\n\n")
    lines.append("pub fn topics() -> BTreeMap<&'static str, &'static str> {\n  let mut m = BTreeMap::new();\n")
    for k, v in topic_map.items():
        lines.append(f"  m.insert({rs_string(k)}, {rs_string(v)});\n")
    lines.append("  m\n}\n\n")
    lines.append("#[derive(Clone)]\n")
    lines.append(f"pub struct {class_name} {{\n  inner: {base_class},\n}}\n\n")
    lines.append(f"impl {class_name} {{\n")
    lines.append(f"  pub fn new(address: {address_type}, opts: Option<{base_path}::Options>) -> Self {{\n")
    lines.append(f"    let inner = {base_class}::new(address, abi_value(), opts);\n    Self {{ inner }}\n  }}\n\n")
    lines.append("  #[inline] pub fn address(&self) -> &str { self.inner.address() }\n")
    lines.append(f"  #[inline] pub fn set_signer(&mut self, signer: Option<{base_path}::Signer>) {{ self.inner.set_signer(signer) }}\n\n")
    for f in ir["functions"]:
        mname = method_name(f["name"], f.get("discriminator"))
        params_sig = ", ".join(f"{rs_ident(p['name'])}: {rs_type(p['type'])}" for p in f["inputs"])
        is_view = f["state_mutability"] in ("view", "pure")
        if is_view:
            if len(f["outputs"]) == 0:
                ret_ty = "Json"
            elif len(f["outputs"]) == 1:
                ret_ty = rs_type(f["outputs"][0])
            else:
                ret_ty = "(" + ", ".join(rs_type(o) for o in f["outputs"]) + ")"
        else:
            ret_ty = "Json"
        inv = "call" if is_view else "transact"
        args_vec = ", ".join(rs_ident(p["name"]) for p in f["inputs"])
        lines.append(f"  /// {f['name']} {f['state_mutability']} | Selector: {f['selector']}\n")
        lines.append(f"  pub async fn {rs_ident(mname)}(&self, {params_sig}) -> Result<{ret_ty}, {base_path}::Error> {{\n")
        lines.append(f"    self.inner.{inv}::<{ret_ty}>({rs_string(f['name'])}, serde_json::json!([{args_vec}])).await\n")
        lines.append("  }\n\n")
    lines.append("  #[inline] pub fn event_topics(&self) -> BTreeMap<&'static str, &'static str> { topics() }\n")
    lines.append("}\n")
    return "".join(lines)


# --- CLI ------------------------------------------------------------------------

@dataclass
class Options:
    lang: str
    abi_path: str
    out_dir: str
    class_name: Optional[str]
    file_name: Optional[str]
    py_base_import: str = "omni_sdk.contracts.client"
    py_base_class: str = "ContractClient"
    ts_base_import: str = "@animica/sdk/contracts/client"
    ts_base_class: str = "ContractClient"
    rs_base_path: str = "animica_sdk::contracts::client"
    rs_base_class: str = "ContractClient"
    rs_address_type: str = "String"


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Animica SDK Codegen")
    p.add_argument("--lang", required=True, choices=["py", "ts", "rs"], help="Target language")
    p.add_argument("--abi", required=True, help="Path to normalized ABI IR JSON (or '-' for stdin)")
    p.add_argument("--out", required=True, help="Output directory")
    p.add_argument("--class", dest="class_name", default=None, help="Class/struct name (default inferred or 'ContractClient')")
    p.add_argument("--file", dest="file_name", default=None, help="Optional override for output filename")
    # Advanced knobs:
    p.add_argument("--py-base-import", default="omni_sdk.contracts.client")
    p.add_argument("--py-base-class", default="ContractClient")
    p.add_argument("--ts-base-import", default="@animica/sdk/contracts/client")
    p.add_argument("--ts-base-class", default="ContractClient")
    p.add_argument("--rs-base-path", default="animica_sdk::contracts::client")
    p.add_argument("--rs-base-class", default="ContractClient")
    p.add_argument("--rs-address-type", default="String")

    args = p.parse_args(argv)

    opts = Options(
        lang=args.lang,
        abi_path=args.abi,
        out_dir=args.out,
        class_name=args.class_name,
        file_name=args.file_name,
        py_base_import=args.py_base_import,
        py_base_class=args.py_base_class,
        ts_base_import=args.ts_base_import,
        ts_base_class=args.ts_base_class,
        rs_base_path=args.rs_base_path,
        rs_base_class=args.rs_base_class,
        rs_address_type=args.rs_address_type,
    )

    raw = load_json(opts.abi_path)
    ir = normalize_ir(raw)

    cls = opts.class_name or ir.get("metadata", {}).get("title") or "ContractClient"
    # Ensure ClassCase
    cls = "".join(part.capitalize() for part in snake(str(cls)).split("_"))

    if opts.lang == "py":
        code = emit_python(ir, cls, base_import=opts.py_base_import, base_class=opts.py_base_class)
        fname = opts.file_name or f"{snake(cls)}.py"
    elif opts.lang == "ts":
        code = emit_typescript(ir, cls, base_import=opts.ts_base_import, base_class=opts.ts_base_class)
        fname = opts.file_name or f"{cls}.ts"
    else:
        code = emit_rust(ir, cls, base_path=opts.rs_base_path, base_class=opts.rs_base_class, address_type=opts.rs_address_type)
        fname = opts.file_name or f"{snake(cls)}.rs"

    out_dir = Path(opts.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)

    print(f"✔ Generated {opts.lang} client: {out_path}")

if __name__ == "__main__":
    main()
