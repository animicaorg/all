# -*- coding: utf-8 -*-
"""
build_package.py
================

Compile a Python smart contract to IR using vm_py, compute its code hash
(SHA3-256), and emit a deterministic package directory containing:

- manifest.json  (canonical JSON with "code_hash" injected)
- code.ir        (compiled IR bytes)
- package.json   (small metadata index for tooling)

This script favors *determinism*:
- Canonical JSON (no whitespace, sorted keys) for manifests/metadata
- Atomic file writes
- Stable hashing (SHA3-256 of raw IR bytes)
- No reliance on system locale or timezones

Usage examples
--------------

# From a manifest that already references an ABI and a source file:
python -m contracts.tools.build_package \
  --manifest contracts/examples/counter/manifest.json \
  --out-dir contracts/build

# From explicit parts (no manifest on disk):
python -m contracts.tools.build_package \
  --source contracts/examples/counter/contract.py \
  --abi contracts/examples/counter/abi.json \
  --name counter \
  --version 0.1.0 \
  --out-dir contracts/build

# Get a machine-readable summary on stdout:
python -m contracts.tools.build_package \
  --manifest contracts/examples/counter/manifest.json \
  --stdout-json

Exit codes
----------
0 on success; non-zero on failure.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

# Local tooling helpers (dependency-free)
from contracts.tools import (  # type: ignore
    __version__ as tools_version,
    atomic_write_bytes,
    atomic_write_text,
    canonical_json_bytes,
    canonical_json_str,
    ensure_dir,
    project_root,
    sha3_256_hex,
)

# ------------------------------ utilities ------------------------------------


def _read_text(path: Union[str, Path]) -> str:
    return Path(path).read_text(encoding="utf-8")


def _read_json(path: Union[str, Path]) -> Any:
    try:
        return json.loads(_read_text(path))
    except Exception as exc:
        raise SystemExit(f"[build_package] Failed to parse JSON: {path} :: {exc}") from exc


def _now_iso() -> str:
    # Use UTC with 'Z' for reproducibility
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _normalize_abi(abi_obj_or_path: Union[str, Path, list, dict]) -> Any:
    """
    Accept an ABI path or already-loaded ABI (list/dict).
    Returns the ABI as a Python object suitable for canonical JSON.
    """
    if isinstance(abi_obj_or_path, (list, dict)):
        return abi_obj_or_path
    return _read_json(abi_obj_or_path)


# ------------------------------ vm_py compile --------------------------------


class CompileError(RuntimeError):
    pass


def _compile_with_cli(source_path: Path) -> bytes:
    """
    Compile using the vm_py CLI:
      python -m vm_py.cli.compile <source> --out -
    Falls back to a tempfile if STDOUT streaming isn't supported.
    """
    exe = sys.executable or "python"
    # First try to write IR to stdout (fast path)
    cmd_stdout = [exe, "-m", "vm_py.cli.compile", str(source_path), "--out", "-"]
    try:
        res = subprocess.run(
            cmd_stdout,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if res.stdout:
            return bytes(res.stdout)
        # Some implementations may print text; treat as fatal
        raise CompileError(
            "vm_py CLI returned empty stdout; try tempfile path fallback."
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace")
        if "unrecognized arguments: --out -" in stderr:
            # Fallback to temp file approach
            pass
        else:
            raise CompileError(
                f"vm_py CLI compile failed (stdout mode):\n{stderr}"
            ) from exc

    # Fallback: write to a temp file and read it back
    tmp_dir = ensure_dir(Path(".tmp_ir"))
    tmp_ir = tmp_dir / "out.ir"
    cmd_file = [exe, "-m", "vm_py.cli.compile", str(source_path), "--out", str(tmp_ir)]
    try:
        res = subprocess.run(
            cmd_file,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not tmp_ir.is_file():
            raise CompileError(
                "vm_py CLI reported success but IR file not found: {tmp_ir}"
            )
        data = tmp_ir.read_bytes()
        # best effort cleanup
        try:
            tmp_ir.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
        try:
            tmp_dir.rmdir()
        except Exception:
            pass
        return data
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace")
        raise CompileError(f"vm_py CLI compile failed (file mode):\n{stderr}") from exc


def _try_compile_with_loader(source_path: Path) -> Optional[bytes]:
    """
    Prefer programmatic API when available for speed and better errors.
    Returns IR bytes or None if loader API is not available.
    """
    try:
        # Expected API surface per vm_py/runtime/loader.py
        from vm_py.runtime import loader as vm_loader  # type: ignore
    except Exception:
        return None

    # Try commonly named functions in priority order
    candidates = [
        "compile_source_to_ir",  # returns bytes
        "compile_source_to_ir_bytes",
        "compile_source",  # may return (ir_bytes, meta) or object with .ir
        "load_and_compile",  # (path) -> object with .ir_bytes
        "load",  # may accept path and return object carrying IR
    ]

    src_text = source_path.read_text(encoding="utf-8")

    for name in candidates:
        func = getattr(vm_loader, name, None)
        if not callable(func):
            continue
        try:
            res = None
            # Try most common call patterns
            try:
                res = func(src_text)  # type: ignore[misc]
            except TypeError:
                res = func(str(source_path))  # type: ignore[misc]
            # Normalize outputs
            if isinstance(res, (bytes, bytearray, memoryview)):
                return bytes(res)
            if isinstance(res, tuple) and res and isinstance(res[0], (bytes, bytearray, memoryview)):
                return bytes(res[0])
            # Object with attribute
            for attr in ("ir_bytes", "ir", "bytes"):
                if hasattr(res, attr):
                    data = getattr(res, attr)
                    if isinstance(data, (bytes, bytearray, memoryview)):
                        return bytes(data)
        except Exception:
            # try next style
            continue

    return None


def compile_to_ir(source_path: Path) -> bytes:
    """
    Compile the Python contract into IR bytes via vm_py.
    Tries a programmatic API first; falls back to the CLI.
    """
    ir = _try_compile_with_loader(source_path)
    if ir is not None:
        return ir
    return _compile_with_cli(source_path)


# ------------------------------ packaging ------------------------------------


def _derive_name_version(
    name: Optional[str],
    version: Optional[str],
    manifest_in: Optional[Dict[str, Any]],
    source_path: Path,
) -> Tuple[str, str]:
    n = (
        name
        or (manifest_in.get("name") if manifest_in else None)
        or source_path.stem
    )
    v = version or (manifest_in.get("version") if manifest_in else None) or "0.1.0"
    return str(n), str(v)


def _manifest_from_parts(
    name: str,
    version: str,
    abi: Any,
    extras: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    m: Dict[str, Any] = {
        "name": name,
        "version": version,
        "abi": abi,
    }
    if extras:
        # Only include JSON-serializable extras
        for k, v in extras.items():
            try:
                json.dumps(v)
                m[k] = v
            except Exception:
                continue
    return m


def build_package(
    *,
    manifest_path: Optional[Path],
    source_path: Path,
    abi: Any,
    name: Optional[str],
    version: Optional[str],
    out_dir: Path,
    stdout_json: bool,
) -> Dict[str, Any]:
    # Load manifest if provided
    manifest_in: Optional[Dict[str, Any]] = None
    if manifest_path:
        manifest_in = _read_json(manifest_path)
        # Allow manifest.abi to be a path
        if "abi" in manifest_in and not isinstance(manifest_in["abi"], (list, dict)):
            manifest_in["abi"] = _normalize_abi(manifest_in["abi"])

    # Resolve name/version
    name_res, ver_res = _derive_name_version(name, version, manifest_in, source_path)

    # Compile → IR
    ir_bytes = compile_to_ir(source_path)

    # Compute code hash (hex, 0x-prefixed)
    code_hash = sha3_256_hex(ir_bytes)

    # Build final manifest (inject code_hash)
    abi_obj = manifest_in["abi"] if (manifest_in and "abi" in manifest_in) else _normalize_abi(abi)
    manifest_out = manifest_in or _manifest_from_parts(name_res, ver_res, abi_obj)
    manifest_out = dict(manifest_out)  # shallow copy
    manifest_out["code_hash"] = code_hash

    # Derive deterministic package dir: <out_dir>/<name>-<hash8>/
    hash8 = code_hash[2:10]
    pkg_dir = out_dir / f"{name_res}-{hash8}"
    ensure_dir(pkg_dir)

    # Write artifacts atomically
    code_ir_path = pkg_dir / "code.ir"
    manifest_json_path = pkg_dir / "manifest.json"
    pkg_index_path = pkg_dir / "package.json"

    atomic_write_bytes(code_ir_path, ir_bytes)
    atomic_write_text(manifest_json_path, canonical_json_str(manifest_out))

    pkg_index = {
        "name": name_res,
        "version": ver_res,
        "code_hash": code_hash,
        "files": {
            "manifest": str(manifest_json_path.name),
            "ir": str(code_ir_path.name),
        },
        "sizes": {
            "ir_bytes": len(ir_bytes),
            "manifest_bytes": len(canonical_json_bytes(manifest_out)),
        },
        "built_at": _now_iso(),
        "tools": {
            "contracts_tools_version": tools_version,
        },
        # Helpful hints for later steps
        "hints": {
            "deploy": {
                "use": "sdk or wallet to sign+send deploy tx with this code hash",
                "code_hash": code_hash,
            },
            "verify": {
                "match": "recompile source and compare code_hash",
            },
        },
    }
    atomic_write_text(pkg_index_path, canonical_json_str(pkg_index))

    summary = {
        "package_dir": str(pkg_dir),
        "code_hash": code_hash,
        "manifest_path": str(manifest_json_path),
        "ir_path": str(code_ir_path),
        "name": name_res,
        "version": ver_res,
        "ir_size": len(ir_bytes),
    }

    if stdout_json:
        print(canonical_json_str(summary))
    else:
        print(
            f"[build_package] OK: {name_res}@{ver_res} "
            f"code_hash={code_hash} dir={pkg_dir}"
        )
    return summary


# ------------------------------ CLI ------------------------------------------


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="contracts.build_package",
        description="Compile a Python contract with vm_py and build a deterministic package.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--manifest",
        type=Path,
        help="Path to manifest.json (may include 'abi' inline or as a path and 'source' optional).",
    )
    g.add_argument(
        "--source",
        type=Path,
        help="Path to contract source .py (use with --abi and --name when no manifest).",
    )

    p.add_argument(
        "--abi",
        type=str,
        default=None,
        help="Path to ABI JSON (required if --source is used without --manifest).",
    )
    p.add_argument("--name", type=str, default=None, help="Contract name (optional).")
    p.add_argument(
        "--version",
        type=str,
        default=None,
        help="Contract version string (optional; default 0.1.0).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=project_root() / "contracts" / "build",
        help="Directory to write the package into (default: contracts/build).",
    )
    p.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print machine-readable JSON summary to stdout.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    manifest_path: Optional[Path] = args.manifest
    source_path: Optional[Path] = None
    abi_obj: Any = None

    if manifest_path:
        # Manifest may specify the source; else infer from alongside manifest
        manifest = _read_json(manifest_path)
        src_field = manifest.get("source")
        if src_field:
            source_path = Path(src_field)
            if not source_path.is_file():
                # allow relative to manifest dir
                cand = manifest_path.parent / src_field
                if cand.is_file():
                    source_path = cand
        if not source_path:
            # Conventional default: contract.py alongside manifest
            default_src = manifest_path.parent / "contract.py"
            if default_src.is_file():
                source_path = default_src

        if not source_path:
            raise SystemExit(
                "[build_package] Manifest does not specify 'source' and no default contract.py found."
            )

        abi_field = manifest.get("abi")
        if abi_field is None and args.abi:
            abi_obj = _normalize_abi(args.abi)
        else:
            abi_obj = _normalize_abi(abi_field) if abi_field is not None else None
            if abi_obj is None:
                raise SystemExit("[build_package] Manifest missing 'abi'.")
    else:
        # --source mode
        if not args.abi:
            raise SystemExit("[build_package] --abi is required when using --source without --manifest.")
        source_path = args.source
        abi_obj = _normalize_abi(args.abi)

    out_dir: Path = ensure_dir(args.out_dir)

    try:
        build_package(
            manifest_path=manifest_path,
            source_path=source_path,  # type: ignore[arg-type]
            abi=abi_obj,
            name=args.name,
            version=args.version,
            out_dir=out_dir,
            stdout_json=args.stdout_json,
        )
        return 0
    except CompileError as ce:
        print(f"[build_package] Compile error: {ce}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[build_package] Failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
