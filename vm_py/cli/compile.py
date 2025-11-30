#!/usr/bin/env python3
"""
omni vm compile

Compile a deterministic Python contract into Animica VM IR bytes.

Usage:
  python -m vm_py.cli.compile path/to/contract.py --out out.ir
  # or, if installed as a console script:
  omni-vm-compile path/to/contract.py --out out.ir

Options:
  --format {cbor,json}   Output encoding for the IR (default: cbor).
  --meta META.json       Write compile metadata (gas estimate, code hash) to this JSON file.
  --stdin                Read source from stdin instead of a file.
  --quiet                Suppress informational stderr logs.

This tool is resilient: it first tries the high-level runtime loader (vm_py.runtime.loader),
and falls back to the lower-level compiler pipeline (ast_lower → typecheck → encode → gas_estimator)
if needed. It never reaches out to the network.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from hashlib import sha3_256
from typing import Any, Dict, Tuple

# ---------------------- small utils ---------------------- #


def eprint(*a: Any, **k: Any) -> None:
    if not _CTX.get("quiet", False):
        print(*a, file=sys.stderr, **k)


_CTX: Dict[str, Any] = {}


def _safe_json(obj: Any) -> Any:
    """Make objects JSON-serializable for metadata."""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": obj.hex()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    return obj


def _call_first(module, names, *args, **kwargs):
    last_err = None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 (surface best effort)
                last_err = e
                continue
    if last_err:
        raise last_err
    raise AttributeError(f"None of {names!r} found in {module!r}")


# ---------------------- compilation paths ---------------------- #


def compile_via_runtime_loader(
    src: str, filename: str = "<stdin>"
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Preferred path: use vm_py.runtime.loader if available.

    Expected flexible signatures (we try a few):
      - compile_source(source: str) -> (ir_bytes: bytes, meta: dict)
      - compile_text(source: str, filename: str = ...) -> ...
      - compile_file(path: str) -> ...
    """
    try:
        from vm_py.runtime import loader
    except Exception as e:
        raise ImportError(f"runtime.loader not available: {e}") from e

    # Try text-based compile first
    for names in (("compile_source",), ("compile_text",)):
        fn = getattr(loader, names[0], None)
        if callable(fn):
            res = fn(src) if fn.__code__.co_argcount <= 1 else fn(src, filename=filename)  # type: ignore[arg-type]
            if (
                isinstance(res, tuple)
                and len(res) == 2
                and isinstance(res[0], (bytes, bytearray))
            ):
                return bytes(res[0]), dict(res[1])
            if isinstance(res, (bytes, bytearray)):
                return bytes(res), {}
            # Fallback: maybe it returned (ir_obj, meta) and we need to encode
            ir_obj = res
            ir_bytes, meta2 = encode_ir_flex(ir_obj)
            return ir_bytes, meta2

    # If text compile not present, try file-based helper by temporarily writing to a temp file
    import tempfile

    with tempfile.NamedTemporaryFile(
        "w", suffix=os.path.splitext(filename)[1] or ".py", delete=True
    ) as tf:
        tf.write(src)
        tf.flush()
        if hasattr(loader, "compile_file"):
            res = loader.compile_file(tf.name)  # type: ignore[attr-defined]
            if (
                isinstance(res, tuple)
                and len(res) == 2
                and isinstance(res[0], (bytes, bytearray))
            ):
                return bytes(res[0]), dict(res[1])
            if isinstance(res, (bytes, bytearray)):
                return bytes(res), {}
            ir_obj = res
            ir_bytes, meta2 = encode_ir_flex(ir_obj)
            return ir_bytes, meta2

    raise RuntimeError("No suitable compile_* entry found in vm_py.runtime.loader")


def encode_ir_flex(ir_obj: Any) -> Tuple[bytes, Dict[str, Any]]:
    """
    Try a variety of encoder functions to turn an IR object into bytes.
    """
    from importlib import import_module

    enc = import_module("vm_py.compiler.encode")
    # Try common function names
    for name in ("dumps", "encode", "encode_module", "to_bytes"):
        fn = getattr(enc, name, None)
        if callable(fn):
            out = fn(ir_obj)  # type: ignore[misc]
            if isinstance(out, (bytes, bytearray)):
                return bytes(out), {}
            if isinstance(out, tuple) and len(out) >= 1:
                b = out[0]
                meta = out[1] if len(out) > 1 and isinstance(out[1], dict) else {}
                if isinstance(b, (bytes, bytearray)):
                    return bytes(b), meta
    raise RuntimeError(
        "Could not encode IR object; vm_py.compiler.encode lacks known functions"
    )


def compile_via_lower_pipeline(
    src: str, filename: str = "<stdin>"
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Fallback path: AST → IR → typecheck → encode.
    """
    from importlib import import_module

    mod_ast = ast.parse(src, filename=filename)

    lower = import_module("vm_py.compiler.ast_lower")
    ir_mod = _call_first(
        lower,
        ("lower", "lower_module", "lower_from_ast", "lower_from_source", "lower_to_ir"),
        mod_ast,
    )

    # Optional typecheck step
    try:
        tc = import_module("vm_py.compiler.typecheck")
        _call_first(tc, ("typecheck", "typecheck_module", "check", "validate"), ir_mod)
    except Exception as e:
        # Surface typecheck errors; otherwise allow proceed if module isn't present
        if not isinstance(e, (ModuleNotFoundError, ImportError, AttributeError)):
            raise

    # Optional static gas estimate
    gas_meta: Dict[str, Any] = {}
    try:
        ge = import_module("vm_py.compiler.gas_estimator")
        estimate = _call_first(
            ge, ("estimate", "estimate_module", "estimate_upper_bound"), ir_mod
        )
        gas_meta["gas_estimate"] = _safe_json(estimate)
    except Exception:
        pass

    ir_bytes, enc_meta = encode_ir_flex(ir_mod)
    meta = {"pipeline": "lower", **gas_meta, **enc_meta}
    return ir_bytes, meta


def compile_source_to_ir(
    src: str, filename: str = "<stdin>"
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Try runtime-loader path first; fall back to lower pipeline.
    """
    # Attempt high-level loader fast path
    try:
        return compile_via_runtime_loader(src, filename=filename)
    except Exception as e1:
        eprint(f"[vm-compile] runtime loader path unavailable: {e1}")

    # Fallback to lower pipeline
    ir_bytes, meta = compile_via_lower_pipeline(src, filename=filename)
    return ir_bytes, meta


def compile_manifest(manifest_path: str) -> Tuple[bytes, Dict[str, Any]]:
    """Compile a contract from its manifest using the same helper as omni-vm-run."""

    try:
        from vm_py.cli.run import compile_manifest_to_ir
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"compile_manifest_to_ir unavailable: {exc}") from exc

    ir_bytes, _abi, meta = compile_manifest_to_ir(manifest_path)
    meta = {"compiled_from": "manifest", **meta}
    return ir_bytes, meta


# ---------------------- CLI ---------------------- #


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="omni-vm-compile", description="Compile Python contract to Animica VM IR."
    )
    src_group = p.add_mutually_exclusive_group(required=True)
    src_group.add_argument(
        "source", nargs="?", help="Path to contract.py (use '-' for stdin)"
    )
    src_group.add_argument(
        "--stdin", action="store_true", help="Read contract source from stdin"
    )
    src_group.add_argument(
        "--manifest",
        help="Compile from a contract manifest.json (uses its entry/source field)",
    )
    p.add_argument(
        "--out",
        "-o",
        required=True,
        help="Output file path for IR (e.g., out.ir or out.json)",
    )
    p.add_argument(
        "--format",
        choices=("cbor", "json"),
        default="cbor",
        help="IR output format (default: cbor)",
    )
    p.add_argument("--meta", help="Write compile metadata to this JSON file")
    p.add_argument(
        "--quiet", action="store_true", help="Silence informational logs on stderr"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    _CTX["quiet"] = bool(args.quiet)

    # Load source or manifest
    if args.manifest:
        filename = os.fspath(args.manifest)
        ir_bytes, meta = compile_manifest(filename)
    else:
        if args.stdin or args.source == "-":
            src = sys.stdin.read()
            filename = "<stdin>"
        else:
            filename = os.fspath(args.source)
            with open(filename, "r", encoding="utf-8") as f:
                src = f.read()

        ir_bytes, meta = compile_source_to_ir(src, filename=filename)
    # Compute code hash (sha3-256 of IR bytes)
    code_hash = "0x" + sha3_256(ir_bytes).hexdigest()
    meta = {"code_hash": code_hash, **meta}

    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    # Write IR
    if args.format == "cbor":
        with open(args.out, "wb") as f:
            f.write(ir_bytes)
        eprint(f"[vm-compile] wrote IR (CBOR) → {args.out}  hash={code_hash}")
    else:
        # JSON: attempt to also dump a JSON view of IR if encode module provides one; otherwise base64
        # Since we only have bytes reliably, emit a small JSON wrapper.
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {"ir_cbor_hex": ir_bytes.hex(), "code_hash": code_hash}, f, indent=2
            )
        eprint(f"[vm-compile] wrote IR (JSON wrapper) → {args.out}  hash={code_hash}")

    # Write metadata if requested
    if args.meta:
        with open(args.meta, "w", encoding="utf-8") as f:
            json.dump(_safe_json(meta), f, indent=2)
        eprint(f"[vm-compile] wrote metadata → {args.meta}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
