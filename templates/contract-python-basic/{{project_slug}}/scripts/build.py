#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic builder for the contract-python-basic template.

Features
- Reads manifest (contracts/manifest.json)
- Compiles source to IR via vm_py CLI when available
- Computes canonical SHA3-512 code hash
- Emits IR + package bundle into ./build/
- Reproducible by normalizing newlines and trimming trailing spaces

Usage
  python scripts/build.py
  python scripts/build.py --manifest contracts/manifest.json --out build
  python scripts/build.py --no-compile   # only hash+package (skip vm compile)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# ------------------------------ helpers -------------------------------------


def _sha3_512_hex(data: bytes) -> str:
    h = hashlib.sha3_512()
    h.update(data)
    return "0x" + h.hexdigest()


def _read_text_bytes(p: Path) -> bytes:
    # Normalize to Unix newlines for deterministic hashing across platforms.
    txt = p.read_text(encoding="utf-8", errors="strict")
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    # Strip trailing whitespace lines (non-semantic), preserve EOF newline if present.
    lines = [ln.rstrip(" \t") for ln in txt.split("\n")]
    norm = "\n".join(lines)
    return norm.encode("utf-8")


def _mkdirp(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise SystemExit(f"[build] Invalid JSON in {p}: {exc}")


def _dump_json(p: Path, obj: Dict[str, Any]) -> None:
    # Canonical JSON: sorted keys, no trailing spaces, \n ending.
    enc = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    p.write_text(enc + "\n", encoding="utf-8")


def _rel_to(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ------------------------------ vm_py compile -------------------------------


@dataclass
class CompileResult:
    ir_path: Path
    used_vm_cli: bool
    notes: str = ""


def _vm_cli_available() -> bool:
    """
    Check if vm_py is importable or invokable as a module.
    We prefer subprocess to keep the builder light and decoupled.
    """
    try:
        # Quick import check (fast-path); we still use subprocess for the compile call.
        __import__("vm_py")
        return True
    except Exception:
        return False


def _try_vm_compile(src_py: Path, ir_out: Path) -> Tuple[bool, str]:
    """
    Invoke vm_py CLI to compile Python source → IR.
    Falls back to a tiny IR stub if vm_py is not installed.
    """
    cmd = [
        sys.executable,
        "-m",
        "vm_py.cli.compile",
        str(src_py),
        "--out",
        str(ir_out),
    ]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        if completed.returncode == 0 and ir_out.is_file():
            return True, completed.stdout.strip()
        else:
            msg = completed.stderr.strip() or completed.stdout.strip()
            return False, f"vm_py compile failed (rc={completed.returncode}): {msg}"
    except FileNotFoundError as exc:
        return False, f"Python not found for subprocess: {exc}"
    except Exception as exc:  # pragma: no cover
        return False, f"vm_py compile exception: {exc}"


def _write_stub_ir(ir_out: Path, source_bytes: bytes) -> None:
    """
    Write a minimal, deterministic IR envelope so downstream tools can continue.
    This is NOT a production IR; it exists to keep template projects functional
    even without vm_py installed.
    """
    # Tiny JSON IR with the normalized source embedded as base64.
    stub = {
        "format": "vm_py.ir.stub",
        "version": 1,
        "encoding": "json+base64",
        "source_b64": base64.b64encode(source_bytes).decode("ascii"),
        "notes": "Stub IR emitted because vm_py CLI was unavailable.",
    }
    _dump_json(ir_out, stub)


# ------------------------------ package build -------------------------------


def _build_package(
    project_root: Path,
    manifest: Dict[str, Any],
    src_path: Path,
    ir_path: Path,
    out_pkg: Path,
    code_sha512: str,
    vm_cli_used: bool,
) -> Dict[str, Any]:
    """
    Create a deterministic package bundle with code hash & ABI/metadata.
    """
    abi = manifest.get("abi", {})
    meta = manifest.get("metadata", {})
    name = manifest.get("name", "contract")
    version = manifest.get("version", "0.0.0")

    package = {
        "name": name,
        "version": version,
        "tool": "vm_py",
        "code_hash_sha3_512": code_sha512,
        "paths": {
            "source": _rel_to(project_root, src_path),
            "ir": _rel_to(project_root, ir_path),
            "manifest": _rel_to(
                project_root, project_root / "contracts" / "manifest.json"
            ),
        },
        "abi": abi,
        "metadata": meta,
        "build": {
            "vm_cli_used": vm_cli_used,
        },
    }
    _mkdirp(out_pkg.parent)
    _dump_json(out_pkg, package)
    return package


def _write_lockfile(out_dir: Path, package: Dict[str, Any]) -> None:
    lock = {
        "package": {
            "name": package.get("name"),
            "version": package.get("version"),
            "code_hash_sha3_512": package.get("code_hash_sha3_512"),
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "tools": {
            "vm_py_present": _vm_cli_available(),
        },
    }
    _dump_json(out_dir / "build.lock.json", lock)


# ------------------------------ main ----------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build contract package (deterministic)."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("contracts/manifest.json"),
        help="Path to manifest.json (default: contracts/manifest.json)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("build"),
        help="Output directory (default: ./build)",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip vm_py compile step (still hashes source and emits package).",
    )
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="Print the computed SHA3-512 code hash to stdout.",
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    manifest_path = args.manifest
    if not manifest_path.is_file():
        raise SystemExit(f"[build] manifest not found: {manifest_path}")

    manifest = _load_json(manifest_path)

    entry_rel = manifest.get("entry") or "contracts/contract.py"
    src_path = (manifest_path.parent / entry_rel).resolve()
    if not src_path.is_file():
        raise SystemExit(f"[build] source not found: {src_path}")

    # Resolve outputs (allow overrides from manifest.build, but CLI --out wins)
    out_dir = args.out.resolve()
    _mkdirp(out_dir)

    build_section = manifest.get("build", {})
    ir_rel = build_section.get("ir_out", "build/contract.ir")
    pkg_rel = build_section.get("package_out", "build/package.json")

    ir_path = (project_root / ir_rel).resolve()
    pkg_out = (project_root / pkg_rel).resolve()

    # Ensure target dirs are present (respect when user points outside ./build)
    _mkdirp(ir_path.parent)
    _mkdirp(pkg_out.parent)

    # Read & normalize source, compute canonical hash
    source_bytes = _read_text_bytes(src_path)
    code_sha512 = _sha3_512_hex(source_bytes)

    if args.print_hash:
        print(code_sha512)

    # Compile to IR (preferred) or emit stub IR
    vm_cli_used = False
    notes = ""
    if not args.no - compile:
        if _vm_cli_available():
            ok, notes = _try_vm_compile(src_path, ir_path)
            vm_cli_used = ok
            if not ok:
                print(
                    f"[build] vm_py compile not available/failed; emitting stub IR. Details:\n{notes}"
                )
                _write_stub_ir(ir_path, source_bytes)
        else:
            print("[build] vm_py not available; emitting stub IR.")
            _write_stub_ir(ir_path, source_bytes)
    else:
        print("[build] --no-compile set; emitting stub IR only.")
        _write_stub_ir(ir_path, source_bytes)

    # Package
    package = _build_package(
        project_root=project_root,
        manifest=manifest,
        src_path=src_path,
        ir_path=ir_path,
        out_pkg=pkg_out,
        code_sha512=code_sha512,
        vm_cli_used=vm_cli_used,
    )

    # Copy helpful artifacts into ./build when outputs point elsewhere
    if pkg_out.parent != out_dir:
        try:
            shutil.copy2(pkg_out, out_dir / "package.json")
        except Exception:
            pass
    if ir_path.parent != out_dir:
        try:
            shutil.copy2(ir_path, out_dir / Path(ir_path.name))
        except Exception:
            pass

    _write_lockfile(out_dir, package)

    print("[build] OK")
    print(f"       code_sha3_512: {code_sha512}")
    print(f"       IR:            {ir_path}")
    print(f"       package:       {pkg_out}")
    if notes:
        print(f"       notes:         {notes}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
