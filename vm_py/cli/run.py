#!/usr/bin/env python3
"""
omni vm run

Run a deterministic Python contract (compiled to Animica VM IR) for a single call.

Examples:
  python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call get
  python -m vm_py.cli.run --manifest vm_py/examples/counter/manifest.json --call inc --args '[1]'

Notes:
- Prefers vm_py.runtime.loader helpers if available (easiest path).
- Falls back to a lower-level pipeline: parse manifest → compile → run via runtime.engine.
- No network access. This is a local simulator runner for development.

Exit codes:
  0 on success, non-zero on failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from hashlib import sha3_256
from typing import Any, Dict, List, Tuple

# ---------------------- small utils ---------------------- #

def eprint(*a: Any, **k: Any) -> None:
    if not _CTX.get("quiet", False):
        print(*a, file=sys.stderr, **k)

_CTX: Dict[str, Any] = {}

def _safe_json(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (bytes, bytearray)):
        return "0x" + bytes(obj).hex()
    if isinstance(obj, (list, tuple)):
        return [_safe_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    return obj

def _parse_args_json(s: str | None) -> List[Any]:
    if not s or not s.strip():
        return []
    val = json.loads(s)
    if isinstance(val, list):
        return val
    raise SystemExit("--args must be a JSON array, e.g. --args '[1, \"hello\", \"0xdead\"]'")

def _maybe_hex_to_bytes(x: Any) -> Any:
    # Convenience: strings like "0x…" become bytes; leave others intact.
    if isinstance(x, str) and x.startswith("0x"):
        try:
            return bytes.fromhex(x[2:])
        except ValueError:
            return x
    if isinstance(x, list):
        return [_maybe_hex_to_bytes(v) for v in x]
    if isinstance(x, dict):
        return {k: _maybe_hex_to_bytes(v) for k, v in x.items()}
    return x

def _manifest_dir(path: str) -> str:
    return os.path.dirname(os.path.abspath(path)) or "."

# ---------------------- loader-first path ---------------------- #

def try_run_via_loader(manifest_path: str, func: str, args: List[Any]) -> Tuple[Any, Dict[str, Any]]:
    """
    If vm_py.runtime.loader offers a direct 'run_call' or similar helper, use it.
    Expected flexible signatures (we attempt a few):
      - run_call(manifest_path, func, args)
      - run_call(manifest_dict, func, args)
      - call(...), execute_call(...)
    Returns (result, meta)
    """
    try:
        from vm_py.runtime import loader
    except Exception as e:
        raise RuntimeError(f"runtime.loader unavailable: {e}") from e

    # Load manifest JSON (we may pass either path or dict)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    for name in ("run_call", "call", "execute_call"):
        fn = getattr(loader, name, None)
        if callable(fn):
            # Try path form first
            try:
                out = fn(manifest_path, func, args)  # type: ignore[misc]
                return _normalize_loader_result(out)
            except TypeError:
                # Try dict form
                out = fn(manifest, func, args)  # type: ignore[misc]
                return _normalize_loader_result(out)
    raise RuntimeError("No suitable run_call() in vm_py.runtime.loader")

def _normalize_loader_result(out: Any) -> Tuple[Any, Dict[str, Any]]:
    """
    Accept:
      - result
      - (result, meta)
      - {'result': X, 'gas_used': Y, ...}
    """
    if isinstance(out, tuple) and len(out) == 2:
        return out[0], dict(out[1])
    if isinstance(out, dict) and "result" in out:
        return out["result"], out
    return out, {}

# ---------------------- compile helpers (fallback) ---------------------- #

def compile_manifest_to_ir(manifest_path: str) -> Tuple[bytes, Dict[str, Any], Dict[str, Any]]:
    """
    Return (ir_bytes, abi_dict, meta)
    Tries runtime.loader.compile_manifest or equivalent; falls back to reading source and compiling.
    """
    mdir = _manifest_dir(manifest_path)
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Normalize ABI: allow 'abi' directly or nested
    abi = manifest.get("abi") or manifest.get("ABI") or {}

    # Prefer loader.compile_manifest if present
    try:
        from vm_py.runtime import loader
        for name in ("compile_manifest", "compile_from_manifest", "build_ir_from_manifest"):
            fn = getattr(loader, name, None)
            if callable(fn):
                out = fn(manifest) if fn.__code__.co_argcount >= 1 else fn()  # type: ignore[misc]
                # Accept flexible returns
                if isinstance(out, tuple):
                    if len(out) == 3 and isinstance(out[0], (bytes, bytearray)) and isinstance(out[1], dict):
                        return bytes(out[0]), dict(out[1]), dict(out[2]) if isinstance(out[2], dict) else {}
                    if len(out) == 2 and isinstance(out[0], (bytes, bytearray)):
                        if isinstance(out[1], dict) and "abi" in out[1] and not abi:
                            abi = out[1]["abi"]
                        return bytes(out[0]), abi if isinstance(abi, dict) else {}, {}
                if isinstance(out, (bytes, bytearray)):
                    return bytes(out), abi if isinstance(abi, dict) else {}, {}
    except Exception as e:
        eprint(f"[vm-run] loader compile path unavailable: {e}")

    # Else: compile from 'source' field
    source_rel = manifest.get("source") or manifest.get("code") or manifest.get("path")
    if not source_rel:
        raise RuntimeError("Manifest missing 'source' (path to .py) and no loader.compile path available.")
    source_path = os.path.join(mdir, source_rel)

    with open(source_path, "r", encoding="utf-8") as f:
        src = f.read()

    # Try high-level loader text compile first
    try:
        from vm_py.runtime import loader as rloader
        for name in ("compile_source", "compile_text"):
            fn = getattr(rloader, name, None)
            if callable(fn):
                res = fn(src) if fn.__code__.co_argcount <= 1 else fn(src, filename=source_path)  # type: ignore[misc]
                if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], (bytes, bytearray)):
                    return bytes(res[0]), abi if isinstance(abi, dict) else {}, dict(res[1]) if isinstance(res[1], dict) else {}
                if isinstance(res, (bytes, bytearray)):
                    return bytes(res), abi if isinstance(abi, dict) else {}, {}
                # If object, try to encode below
                ir_obj = res
                ir_bytes = _encode_ir_bytes(ir_obj)
                return ir_bytes, abi if isinstance(abi, dict) else {}, {}
    except Exception:
        pass

    # Fallback: lower pipeline encode
    ir_bytes = _compile_lower_pipeline(src, source_path)
    return ir_bytes, abi if isinstance(abi, dict) else {}, {}

def _encode_ir_bytes(ir_obj: Any) -> bytes:
    from importlib import import_module
    enc = import_module("vm_py.compiler.encode")
    for name in ("dumps", "encode", "encode_module", "to_bytes"):
        fn = getattr(enc, name, None)
        if callable(fn):
            out = fn(ir_obj)  # type: ignore[misc]
            if isinstance(out, (bytes, bytearray)):
                return bytes(out)
            if isinstance(out, tuple) and len(out) >= 1 and isinstance(out[0], (bytes, bytearray)):
                return bytes(out[0])
    raise RuntimeError("Could not encode IR object to bytes")

def _compile_lower_pipeline(src: str, filename: str) -> bytes:
    import ast
    from importlib import import_module
    lower = import_module("vm_py.compiler.ast_lower")
    ir_mod = getattr(lower, "lower", None)
    if not callable(ir_mod):
        for alt in ("lower_module", "lower_from_ast", "lower_from_source"):
            ir_mod = getattr(lower, alt, None)
            if callable(ir_mod):
                break
    if ir_mod is None:
        raise RuntimeError("No lower() function found in vm_py.compiler.ast_lower")
    ir = ir_mod(ast.parse(src, filename=filename), filename)  # type: ignore[misc]
    return _encode_ir_bytes(ir)

# ---------------------- engine fallback runner ---------------------- #

def run_via_engine(ir_bytes: bytes, abi: Dict[str, Any], func: str, args: List[Any]) -> Tuple[Any, Dict[str, Any]]:
    """
    Execute a call against the interpreter engine.
    """
    from importlib import import_module

    # Build default Block/Tx envs if available
    block_env = {"height": 1, "timestamp": 1, "coinbase": b"\x00" * 20}
    tx_env = {"sender": b"\x01" * 20, "value": 0, "gas_limit": 5_000_000}

    try:
        ctx = import_module("vm_py.runtime.context")
        be_cls = getattr(ctx, "BlockEnv", None)
        te_cls = getattr(ctx, "TxEnv", None)
        if be_cls and te_cls:
            block_env = be_cls(height=1, timestamp=1, coinbase=b"\x00" * 20)  # type: ignore[call-arg]
            tx_env = te_cls(sender=b"\x01" * 20, value=0, gas_limit=5_000_000, chain_id=None)  # type: ignore[call-arg]
    except Exception:
        pass

    eng = import_module("vm_py.runtime.engine")
    # Prefer an Engine class with a run_call/call method
    engine_obj = None
    for ctor_name in ("Engine", "Interpreter", "VM"):
        ctor = getattr(eng, ctor_name, None)
        if callable(ctor):
            try:
                engine_obj = ctor(ir_bytes=ir_bytes, abi=abi)  # type: ignore[call-arg]
            except TypeError:
                try:
                    engine_obj = ctor(ir=ir_bytes, abi=abi)  # type: ignore[call-arg]
                except Exception:
                    continue
            break

    # If no object-oriented engine, try module-level run helpers
    if engine_obj is None:
        for fn_name in ("run_call", "call_function", "execute_call"):
            fn = getattr(eng, fn_name, None)
            if callable(fn):
                out = fn(ir_bytes, func, args, block_env, tx_env)  # type: ignore[misc]
                return _normalize_engine_result(out)

    if engine_obj is None:
        raise RuntimeError("Could not construct engine or find a run function in vm_py.runtime.engine")

    # Try common instance methods
    for meth_name in ("run_call", "call", "execute_call"):
        meth = getattr(engine_obj, meth_name, None)
        if callable(meth):
            out = meth(func, args, block_env, tx_env)  # type: ignore[misc]
            return _normalize_engine_result(out)

    # As a very last resort, try a generic 'run'
    run_any = getattr(engine_obj, "run", None)
    if callable(run_any):
        out = run_any(func, args, block_env, tx_env)  # type: ignore[misc]
        return _normalize_engine_result(out)

    raise RuntimeError("Engine constructed, but no runnable method (run_call/call/execute_call/run) was found")

def _normalize_engine_result(out: Any) -> Tuple[Any, Dict[str, Any]]:
    """
    Accept flexible engine returns:
      - result
      - (result, gas_used)
      - (result, meta)
      - {'result': X, 'gas_used': Y, 'events': [...]}  (preferred)
    """
    if isinstance(out, tuple) and len(out) == 2:
        meta = out[1]
        if isinstance(meta, int):
            return out[0], {"gas_used": meta}
        return out[0], dict(meta) if isinstance(meta, dict) else {}
    if isinstance(out, dict) and "result" in out:
        return out["result"], out
    return out, {}

# ---------------------- CLI ---------------------- #

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="omni-vm-run", description="Run a contract call using the Animica Python VM.")
    p.add_argument("--manifest", "-m", required=True, help="Path to contract manifest.json")
    p.add_argument("--call", "-c", required=True, help="Function name to call")
    p.add_argument("--args", help="JSON array of arguments, e.g. --args '[1, \"0xdead\"]'")
    p.add_argument("--hex-as-bytes", action="store_true", default=True, help="Interpret '0x..' strings as bytes (default ON)")
    p.add_argument("--no-hex-as-bytes", dest="hex_as_bytes", action="store_false", help="Disable hex→bytes conversion")
    p.add_argument("--format", choices=("text", "json"), default="json", help="Output format")
    p.add_argument("--quiet", action="store_true", help="Silence informational logs on stderr")
    return p.parse_args(argv)

def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _CTX["quiet"] = bool(args.quiet)

    call_args_raw = _parse_args_json(args.args)
    call_args = _maybe_hex_to_bytes(call_args_raw) if args.hex_as_bytes else call_args_raw

    # 1) Try direct loader-run
    try:
        eprint("[vm-run] trying vm_py.runtime.loader.run_call …")
        result, meta = try_run_via_loader(args.manifest, args.call, call_args)
    except Exception as e_loader:
        eprint(f"[vm-run] loader path failed: {e_loader}")
        # 2) Compile + run via engine
        eprint("[vm-run] compiling manifest → IR …")
        ir_bytes, abi, cmeta = compile_manifest_to_ir(args.manifest)
        code_hash = "0x" + sha3_256(ir_bytes).hexdigest()
        eprint(f"[vm-run] code hash: {code_hash}")
        eprint("[vm-run] executing via vm_py.runtime.engine …")
        result, emeta = run_via_engine(ir_bytes, abi, args.call, call_args)
        meta = {"code_hash": code_hash, **cmeta, **emeta}

    # Output
    out = {"ok": True, "result": _safe_json(result), "meta": _safe_json(meta)}
    if args.format == "json":
        print(json.dumps(out, indent=2))
    else:
        print(f"Result: {out['result']}")
        if meta:
            print("Meta:")
            for k, v in meta.items():
                print(f"  - {k}: {_safe_json(v)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
