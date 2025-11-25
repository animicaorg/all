#!/usr/bin/env python3
"""
omni vm inspect-ir

Pretty-print an Animica VM IR module and report a static gas upper bound.

Examples:
  # Inspect a compiled IR file
  python -m vm_py.cli.inspect_ir --ir out.ir

  # Compile from a manifest, then inspect
  python -m vm_py.cli.inspect_ir --manifest vm_py/examples/counter/manifest.json

  # Compile directly from a Python source file
  python -m vm_py.cli.inspect_ir --source vm_py/examples/counter/contract.py

Output:
  - Code hash (sha3_256 over IR bytes)
  - Size (bytes)
  - Static gas upper bound (if estimator available)
  - Estimated counts (blocks/instructions)
  - Pretty-printed IR structure (truncated and sanitized), or JSON with --format json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from hashlib import sha3_256
from typing import Any, Dict, Iterable, Tuple, Union

# ---------------------- argparse ---------------------- #

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="omni-vm-inspect-ir", description="Inspect Animica VM IR and report a static gas estimate.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ir", help="Path to a compiled IR bytes file")
    g.add_argument("--manifest", help="Path to a contract manifest.json (will compile)")
    g.add_argument("--source", help="Path to a contract .py source file (will compile)")

    p.add_argument("--format", choices=("text", "json"), default="text", help="Output format (default: text)")
    p.add_argument("--max-depth", type=int, default=4, help="Max depth for pretty IR print (default: 4)")
    p.add_argument("--max-bytes", type=int, default=64, help="Max bytes to show inline for byte blobs (default: 64)")
    p.add_argument("--show-ir-bytes", action="store_true", help="Include raw IR bytes (hex, truncated) in output")
    p.add_argument("--quiet", action="store_true", help="Suppress non-essential logs to stderr")
    return p.parse_args(argv)


# ---------------------- small utils ---------------------- #

_CTX: Dict[str, Any] = {}

def eprint(*a: Any, **k: Any) -> None:
    if not _CTX.get("quiet", False):
        print(*a, file=sys.stderr, **k)

def _hex(b: Union[bytes, bytearray], limit: int | None = None) -> str:
    hx = bytes(b).hex()
    if limit is not None and len(hx) > 2 * limit:
        return f"0x{hx[:2*limit]}…(+{len(hx)//2 - limit}B)"
    return "0x" + hx

def _manifest_dir(path: str) -> str:
    return os.path.dirname(os.path.abspath(path)) or "."

def _safe_jsonable(obj: Any, *, max_bytes: int, depth: int) -> Any:
    """
    Convert IR object or nested dataclasses into a JSON-friendly structure.
    Sanitizes bytes and truncates depth to keep output readable.
    """
    if depth <= 0:
        return "…"
    if obj is None:
        return None
    if isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return _hex(obj, limit=max_bytes)
    if is_dataclass(obj):
        try:
            return {k: _safe_jsonable(v, max_bytes=max_bytes, depth=depth - 1) for k, v in asdict(obj).items()}
        except Exception:
            # asdict can recurse; fallback to vars
            return {k: _safe_jsonable(v, max_bytes=max_bytes, depth=depth - 1) for k, v in vars(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _safe_jsonable(v, max_bytes=max_bytes, depth=depth - 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_jsonable(x, max_bytes=max_bytes, depth=depth - 1) for x in obj]
    # Namedtuple / objects with __dict__
    if hasattr(obj, "_asdict"):
        try:
            d = obj._asdict()  # type: ignore[attr-defined]
            return {k: _safe_jsonable(v, max_bytes=max_bytes, depth=depth - 1) for k, v in d.items()}
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        return {k: _safe_jsonable(v, max_bytes=max_bytes, depth=depth - 1) for k, v in vars(obj).items()}
    # Fallback to repr
    return repr(obj)


# ---------------------- compile paths ---------------------- #

def compile_from_manifest(manifest_path: str) -> Tuple[bytes, Dict[str, Any]]:
    """
    Try vm_py.runtime.loader first; otherwise use compiler pipeline.
    Returns (ir_bytes, meta)
    """
    import json as _json
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = _json.load(f)

    # Try runtime.loader helpers
    try:
        from vm_py.runtime import loader
        for name in ("compile_manifest", "compile_from_manifest", "build_ir_from_manifest"):
            fn = getattr(loader, name, None)
            if callable(fn):
                out = fn(manifest)
                if isinstance(out, (bytes, bytearray)):
                    return bytes(out), {"source": "loader.compile_manifest"}
                if isinstance(out, tuple) and out and isinstance(out[0], (bytes, bytearray)):
                    meta = {}
                    if len(out) > 1 and isinstance(out[1], dict):
                        meta = dict(out[1])
                    return bytes(out[0]), {"source": "loader.compile_manifest", **meta}
    except Exception as e:
        eprint(f"[inspect] loader compile from manifest failed: {e}")

    # Else compile via source path from manifest
    src_rel = manifest.get("source") or manifest.get("code") or manifest.get("path")
    if not src_rel:
        raise RuntimeError("Manifest missing 'source' field and loader.compile_manifest not available.")
    src_path = os.path.join(_manifest_dir(manifest_path), src_rel)
    return compile_from_source(src_path)


def compile_from_source(source_path: str) -> Tuple[bytes, Dict[str, Any]]:
    with open(source_path, "r", encoding="utf-8") as f:
        src = f.read()

    # Prefer runtime.loader.compile_source if available
    try:
        from vm_py.runtime import loader
        for name in ("compile_source", "compile_text"):
            fn = getattr(loader, name, None)
            if callable(fn):
                out = fn(src) if fn.__code__.co_argcount <= 1 else fn(src, filename=source_path)  # type: ignore[misc]
                if isinstance(out, (bytes, bytearray)):
                    return bytes(out), {"source": name}
                if isinstance(out, tuple) and out and isinstance(out[0], (bytes, bytearray)):
                    meta = {}
                    if len(out) > 1 and isinstance(out[1], dict):
                        meta = dict(out[1])
                    return bytes(out[0]), {"source": name, **meta}
                # If returns IR object, encode it:
                return encode_ir_object(out), {"source": f"{name}+encode"}
    except Exception as e:
        eprint(f"[inspect] loader compile from source failed: {e}")

    # Fallback: lower pipeline
    import ast
    from importlib import import_module
    lower = import_module("vm_py.compiler.ast_lower")
    lower_fn = getattr(lower, "lower", None)
    if not callable(lower_fn):
        for alt in ("lower_module", "lower_from_ast", "lower_from_source"):
            lower_fn = getattr(lower, alt, None)
            if callable(lower_fn):
                break
    if lower_fn is None:
        raise RuntimeError("No lower() function found in vm_py.compiler.ast_lower")

    ir_obj = lower_fn(ast.parse(src, filename=source_path), source_path)  # type: ignore[misc]
    return encode_ir_object(ir_obj), {"source": "ast_lower+encode"}


# ---------------------- encode/decode ---------------------- #

def encode_ir_object(ir_obj: Any) -> bytes:
    from importlib import import_module
    enc = import_module("vm_py.compiler.encode")
    for name in ("dumps", "encode", "encode_module", "to_bytes"):
        fn = getattr(enc, name, None)
        if callable(fn):
            out = fn(ir_obj)  # type: ignore[misc]
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
            if isinstance(out, tuple) and out and isinstance(out[0], (bytes, bytearray)):
                return bytes(out[0])
    raise RuntimeError("Unable to encode IR object to bytes (vm_py.compiler.encode)")

def decode_ir_bytes(ir_bytes: bytes) -> Any:
    from importlib import import_module
    enc = import_module("vm_py.compiler.encode")
    for name in ("loads", "decode", "decode_module", "from_bytes"):
        fn = getattr(enc, name, None)
        if callable(fn):
            try:
                return fn(ir_bytes)  # type: ignore[misc]
            except Exception:
                continue
    # Best-effort: if msgspec/cbor encoded dataclass, we might not have a decoder. Return sentinel.
    return None


# ---------------------- stats & gas ---------------------- #

def estimate_static_gas(ir_bytes: bytes, ir_obj: Any | None) -> int | None:
    """
    Try various estimator function names. Returns an integer upper bound (or None if unavailable).
    """
    try:
        from importlib import import_module
        est = import_module("vm_py.compiler.gas_estimator")
        candidates = [
            ("estimate", (ir_obj,) if ir_obj is not None else (ir_bytes,)),
            ("estimate_module", (ir_obj,) if ir_obj is not None else ()),
            ("estimate_ir", (ir_bytes,)),
            ("static_estimate", (ir_obj,) if ir_obj is not None else (ir_bytes,)),
            ("upper_bound", (ir_obj,) if ir_obj is not None else (ir_bytes,)),
        ]
        for name, args in candidates:
            fn = getattr(est, name, None)
            if callable(fn):
                val = fn(*args)  # type: ignore[misc]
                if isinstance(val, int):
                    return val
                if isinstance(val, dict) and "gas_upper_bound" in val and isinstance(val["gas_upper_bound"], int):
                    return val["gas_upper_bound"]
    except Exception as e:
        eprint(f"[inspect] static gas estimate failed: {e}")
    return None

def count_blocks_and_instrs(ir_obj: Any | None) -> Tuple[int | None, int | None]:
    """
    Heuristic counters that try to identify common IR shapes:
      - Module.blocks: list[Block]
      - Block.instrs | Block.ops: list of Instr
      - Generic: count any mapping with key 'op' as an instruction
    """
    if ir_obj is None:
        return None, None

    def is_instr(x: Any) -> bool:
        if x is None: return False
        if isinstance(x, dict) and "op" in x:
            return True
        # dataclass/object with 'op' attribute
        if hasattr(x, "op"):
            try:
                return isinstance(getattr(x, "op"), (str, bytes))
            except Exception:
                return False
        # Namedtuple-like?
        if hasattr(x, "_asdict"):
            try:
                return "op" in x._asdict()
            except Exception:
                return False
        return False

    def children(x: Any) -> Iterable[Any]:
        if x is None: return ()
        if isinstance(x, dict): return x.values()
        if isinstance(x, (list, tuple)): return x
        if is_dataclass(x): 
            try:
                return vars(x).values()
            except Exception:
                return ()
        if hasattr(x, "__dict__"):
            try:
                return vars(x).values()
            except Exception:
                return ()
        if hasattr(x, "_asdict"):
            try:
                return getattr(x, "_asdict")().values()
            except Exception:
                return ()
        return ()

    # Block detection
    blocks = 0
    instrs = 0

    # Try Module.blocks attribute first
    try:
        if hasattr(ir_obj, "blocks"):
            bs = getattr(ir_obj, "blocks")
            if isinstance(bs, (list, tuple)):
                blocks = len(bs)
                for b in bs:
                    # common names: instrs / ops
                    seq = None
                    for name in ("instrs", "ops", "body"):
                        if hasattr(b, name):
                            seq = getattr(b, name)
                            break
                        if isinstance(b, dict) and name in b:
                            seq = b[name]
                            break
                    if isinstance(seq, (list, tuple)):
                        instrs += sum(1 for _ in seq)
                    else:
                        # fallback: scan children
                        instrs += sum(1 for c in children(b) if is_instr(c))
                return blocks, instrs
    except Exception:
        pass

    # Generic recursive scan
    seen: set[int] = set()
    def walk(x: Any) -> None:
        nonlocal instrs, blocks
        if x is None:
            return
        obj_id = id(x)
        if obj_id in seen:
            return
        seen.add(obj_id)

        if isinstance(x, (list, tuple)):
            for y in x:
                if is_instr(y):
                    instrs += 1
                walk(y)
            return
        if isinstance(x, dict):
            if x.get("kind") == "block" or ("instrs" in x and isinstance(x["instrs"], list)):
                blocks += 1
            for y in x.values():
                if is_instr(y):
                    instrs += 1
                walk(y)
            return
        # dataclass / object
        if is_dataclass(x) or hasattr(x, "__dict__") or hasattr(x, "_asdict"):
            try:
                items = vars(x).values() if hasattr(x, "__dict__") else getattr(x, "_asdict")().values()  # type: ignore
            except Exception:
                items = ()
            for y in items:
                if is_instr(y):
                    instrs += 1
                walk(y)

    walk(ir_obj)
    return blocks or None, instrs or None


# ---------------------- main ---------------------- #

def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _CTX["quiet"] = bool(args.quiet)

    # Acquire IR bytes (from --ir | --manifest | --source)
    meta_compile: Dict[str, Any] = {}
    if args.ir:
        with open(args.ir, "rb") as f:
            ir_bytes = f.read()
        meta_compile = {"compiled_from": "file", "path": os.path.abspath(args.ir)}
    elif args.manifest:
        ir_bytes, meta = compile_from_manifest(args.manifest)
        meta_compile = {"compiled_from": "manifest", "path": os.path.abspath(args.manifest), **meta}
    else:
        ir_bytes, meta = compile_from_source(args.source)
        meta_compile = {"compiled_from": "source", "path": os.path.abspath(args.source), **meta}

    code_hash = "0x" + sha3_256(ir_bytes).hexdigest()
    size_bytes = len(ir_bytes)

    # Try to decode to an IR object for richer printing
    ir_obj = decode_ir_bytes(ir_bytes)
    if ir_obj is None:
        eprint("[inspect] IR decode unavailable; showing bytes + gas estimate only")

    gas_upper = estimate_static_gas(ir_bytes, ir_obj)
    blocks, instrs = count_blocks_and_instrs(ir_obj)

    # Build output structure
    out: Dict[str, Any] = {
        "code_hash": code_hash,
        "size_bytes": size_bytes,
        "gas_upper_bound": gas_upper,
        "counts": {"blocks": blocks, "instructions": instrs},
        "compile_meta": meta_compile,
    }
    if args.show_ir_bytes:
        out["ir_bytes"] = _hex(ir_bytes, limit=args.max_bytes)

    if ir_obj is not None:
        out["ir"] = _safe_jsonable(ir_obj, max_bytes=args.max_bytes, depth=args.max_depth)
    else:
        out["ir"] = "(decoder not available)"

    # Render
    if args.format == "json":
        print(json.dumps(out, indent=2))
    else:
        print(f"IR Summary")
        print(f"  code hash    : {out['code_hash']}")
        print(f"  size (bytes) : {out['size_bytes']}")
        if gas_upper is not None:
            print(f"  gas upper    : {gas_upper}")
        if blocks is not None or instrs is not None:
            print(f"  blocks       : {blocks if blocks is not None else '?'}")
            print(f"  instructions : {instrs if instrs is not None else '?'}")
        print(f"  compiled from: {meta_compile.get('compiled_from')} ({meta_compile.get('path')})")
        if args.show_ir_bytes:
            print(f"  ir bytes     : {out['ir_bytes']}")
        print("\nPretty IR (truncated):")
        try:
            pretty = json.dumps(out["ir"], indent=2)
        except Exception:
            pretty = str(out["ir"])
        print(pretty)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
