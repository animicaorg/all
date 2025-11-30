#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_all.py — compile and package all contracts in this workspace.

What it does
------------
- Discovers contract subdirectories under ./contracts (token/, escrow/, ai_agent/, …)
- For each, compiles the Python contract to IR using vm_py (prefers Python API; falls back to CLI)
- Computes a deterministic code hash (SHA3-256 of IR bytes)
- Emits per-contract artifacts in ./build/<name>/:
    - code.ir           (compiled IR bytes)
    - manifest.json     (copied from source)
    - package.json      (bundle metadata: name, version, code_hash, gas_upper_bound if available)

Usage
-----
$ python3 scripts/build_all.py
$ python3 scripts/build_all.py --contracts-dir ./contracts --out-dir ./build --clean
$ python3 scripts/build_all.py --summary json

Requirements
------------
- Python 3.9+
- vm_py installed in the current environment (provided by the template's requirements)
- No network access needed

Notes
-----
- The script is deterministic: output paths and code hashes only depend on inputs.
- If vm_py is missing, a graceful "naive" fallback packages the source as-is; this is
  only for scaffolding and will warn you to install vm_py for real builds.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from hashlib import sha3_256
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ---------- Data structures ---------------------------------------------------


@dataclass
class BuildResult:
    name: str
    src_dir: Path
    out_dir: Path
    manifest: Dict
    ir_path: Optional[Path]
    code_hash_hex: Optional[str]
    gas_upper_bound: Optional[int]
    warnings: List[str]
    errors: List[str]

    @property
    def ok(self) -> bool:
        return (
            self.ir_path is not None
            and self.code_hash_hex is not None
            and not self.errors
        )


# ---------- Discovery ---------------------------------------------------------


def discover_contracts(contracts_dir: Path) -> List[Tuple[str, Path]]:
    """
    A contract is any direct child directory of contracts_dir containing both:
      - contract.py
      - manifest.json
    """
    pairs: List[Tuple[str, Path]] = []
    if not contracts_dir.is_dir():
        return pairs

    for child in sorted(p for p in contracts_dir.iterdir() if p.is_dir()):
        if (child / "contract.py").is_file() and (child / "manifest.json").is_file():
            pairs.append((child.name, child))
    return pairs


# ---------- vm_py compilation helpers ----------------------------------------


def _compile_via_api(src_dir: Path) -> Tuple[Optional[bytes], Optional[int], List[str]]:
    """
    Try to use vm_py's Python API for compilation.
    Returns: (ir_bytes, gas_upper_bound, warnings)
    """
    warnings: List[str] = []
    try:
        # Prefer the high-level loader if available
        # Expected to expose something like: loader.compile(manifest_path, source_path) -> (ir_bytes, meta)
        from vm_py.compiler import gas_estimator  # type: ignore
        from vm_py.runtime import loader as vm_loader  # type: ignore

        manifest_path = src_dir / "manifest.json"
        source_path = src_dir / "contract.py"

        # The loader interface may vary across versions; attempt a few forms.
        ir_bytes: Optional[bytes] = None
        meta_gas: Optional[int] = None

        if hasattr(vm_loader, "compile"):
            # Newer API: returns IR bytes directly
            out = vm_loader.compile(
                manifest_path=str(manifest_path),
                source_path=str(source_path),
            )
            if isinstance(out, tuple) and len(out) >= 1:
                ir_bytes = out[0]
            elif isinstance(out, (bytes, bytearray)):
                ir_bytes = bytes(out)
        elif hasattr(vm_loader, "load"):
            # Older API: load returns module-like object with .ir or .encode()
            mod = vm_loader.load(
                manifest_path=str(manifest_path),
                source_path=str(source_path),
            )
            if hasattr(mod, "ir"):
                ir = getattr(mod, "ir")
                if isinstance(ir, (bytes, bytearray)):
                    ir_bytes = bytes(ir)
            if ir_bytes is None and hasattr(mod, "encode"):
                enc = mod.encode()
                if isinstance(enc, (bytes, bytearray)):
                    ir_bytes = bytes(enc)

        # Optional static gas upper bound if available
        if hasattr(gas_estimator, "estimate_upper_bound"):
            try:
                meta_gas = int(
                    gas_estimator.estimate_upper_bound(source_path.read_text("utf-8"))
                )
            except Exception:
                meta_gas = None

        return ir_bytes, meta_gas, warnings
    except ImportError as e:
        warnings.append(f"vm_py import failed (will try CLI): {e}")
        return None, None, warnings
    except (
        Exception
    ) as e:  # pragma: no cover - defensive paths for varied vm_py versions
        warnings.append(f"vm_py API compile failed: {e}")
        return None, None, warnings


def _compile_via_cli(src_dir: Path) -> Tuple[Optional[bytes], List[str]]:
    """
    Try to invoke the vm_py CLI to compile the contract.
    Returns: (ir_bytes, warnings)
    """
    warnings: List[str] = []
    manifest_path = src_dir / "manifest.json"
    source_path = src_dir / "contract.py"

    candidates = [
        # Preferred: explicit manifest
        [
            sys.executable,
            "-m",
            "vm_py.cli.compile",
            "--manifest",
            str(manifest_path),
            "--out",
        ],
        # Fallback: direct source path
        [sys.executable, "-m", "vm_py.cli.compile", str(source_path), "--out"],
        # Alternate executable name (if installed as console script)
        ["omni", "vm", "compile", "--manifest", str(manifest_path), "--out"],
    ]

    for base in candidates:
        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "out.ir"
            try:
                cmd = base + [str(out_path)]
                proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
                if proc.returncode != 0:
                    warnings.append(
                        f"CLI compile attempt failed: {' '.join(base)}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
                    )
                    continue
                data = out_path.read_bytes()
                if data:
                    return data, warnings
            except FileNotFoundError:
                warnings.append(f"CLI not found for: {' '.join(base)}")
            except Exception as e:  # pragma: no cover
                warnings.append(f"CLI compile error ({' '.join(base)}): {e}")

    return None, warnings


def compile_contract(src_dir: Path) -> Tuple[Optional[bytes], Optional[int], List[str]]:
    """
    Compile contract to IR, returning (ir_bytes, gas_upper_bound, warnings).
    Strategy: try API → try CLI → give up.
    """
    ir_bytes, gas_upper, warns_api = _compile_via_api(src_dir)
    warnings = list(warns_api)
    if ir_bytes is not None:
        return ir_bytes, gas_upper, warnings

    ir_bytes_cli, warns_cli = _compile_via_cli(src_dir)
    warnings.extend(warns_cli)
    if ir_bytes_cli is not None:
        return ir_bytes_cli, None, warnings

    # Final fallback: package the raw source (NOT a real build)
    try:
        raw = (src_dir / "contract.py").read_bytes()
        warnings.append(
            "FALLBACK used: vm_py not available; packaged raw source bytes. "
            "Install vm_py for real IR builds."
        )
        return raw, None, warnings
    except Exception as e:
        warnings.append(f"Failed to read source in fallback: {e}")
        return None, None, warnings


# ---------- Packaging ---------------------------------------------------------


def compute_code_hash(ir_bytes: bytes) -> str:
    return sha3_256(ir_bytes).hexdigest()


def write_artifacts(
    name: str,
    src_dir: Path,
    out_root: Path,
    ir_bytes: bytes,
    manifest: Dict,
    gas_upper_bound: Optional[int],
) -> Tuple[Path, Path, Path, str]:
    """
    Writes:
      - build/<name>/code.ir
      - build/<name>/manifest.json
      - build/<name>/package.json
    Returns: (out_dir, ir_path, manifest_out, code_hash_hex)
    """
    out_dir = out_root / name
    out_dir.mkdir(parents=True, exist_ok=True)

    ir_path = out_dir / "code.ir"
    ir_path.write_bytes(ir_bytes)

    manifest_out = out_dir / "manifest.json"
    manifest_out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    code_hash_hex = compute_code_hash(ir_bytes)

    pkg = {
        "name": manifest.get("name", name),
        "version": manifest.get("version", "0.1.0"),
        "language": manifest.get("language", "python-vm"),
        "entry": manifest.get("entry", "contract.py"),
        "code_hash": f"0x{code_hash_hex}",
        "artifacts": {
            "ir": str(ir_path),
            "manifest": str(manifest_out),
        },
        "gas_upper_bound": gas_upper_bound,
        "metadata": manifest.get("metadata", {}),
    }

    pkg_out = out_dir / "package.json"
    pkg_out.write_text(
        json.dumps(pkg, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return out_dir, ir_path, manifest_out, code_hash_hex


# ---------- CLI ---------------------------------------------------------------


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compile and package all contracts in the workspace."
    )
    p.add_argument(
        "--contracts-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "contracts",
        help="Directory containing per-contract subdirs (default: ./contracts)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "build",
        help="Output directory for per-contract artifacts (default: ./build)",
    )
    p.add_argument(
        "--clean",
        action="store_true",
        help="Remove the output directory before building.",
    )
    p.add_argument(
        "--summary",
        choices=("table", "json"),
        default="table",
        help="How to print the final summary.",
    )
    return p.parse_args(argv)


def load_manifest(src_dir: Path) -> Dict:
    manifest_path = src_dir / "manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(
            f"Failed to read/parse manifest at {manifest_path}: {e}"
        ) from e


def print_summary(results: List[BuildResult], mode: str = "table") -> None:
    if mode == "json":
        as_json = [
            {
                "name": r.name,
                "ok": r.ok,
                "out_dir": str(r.out_dir),
                "code_hash": (f"0x{r.code_hash_hex}" if r.code_hash_hex else None),
                "gas_upper_bound": r.gas_upper_bound,
                "warnings": r.warnings,
                "errors": r.errors,
            }
            for r in results
        ]
        print(json.dumps(as_json, indent=2))
        return

    # table (plain text)
    from shutil import get_terminal_size

    cols = get_terminal_size((100, 20)).columns
    print("=" * cols)
    print("Build Summary".center(cols))
    print("=" * cols)
    header = (
        f"{'CONTRACT':20}  {'STATUS':8}  {'CODE_HASH (sha3-256)':66}  {'GAS_UPPER'}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        status = "OK" if r.ok else "FAIL"
        ch = f"0x{r.code_hash_hex}" if r.code_hash_hex else "-"
        gas = str(r.gas_upper_bound) if r.gas_upper_bound is not None else "-"
        print(f"{r.name:20}  {status:8}  {ch:66}  {gas}")
        for w in r.warnings:
            print(f"  ⚠ {w}")
        for e in r.errors:
            print(f"  ✖ {e}")
    print("-" * len(header))
    failures = [r for r in results if not r.ok]
    if failures:
        print(f"Result: {len(failures)} failure(s).", file=sys.stderr)
    else:
        print("Result: all contracts built successfully.")


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    contracts_dir: Path = args.contracts_dir
    out_dir: Path = args.out_dir

    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)

    pairs = discover_contracts(contracts_dir)
    if not pairs:
        print(f"No contracts found under {contracts_dir}", file=sys.stderr)
        return 1

    results: List[BuildResult] = []

    for name, src_dir in pairs:
        manifest: Dict
        errors: List[str] = []
        warnings: List[str] = []

        try:
            manifest = load_manifest(src_dir)
        except Exception as e:
            results.append(
                BuildResult(
                    name=name,
                    src_dir=src_dir,
                    out_dir=out_dir / name,
                    manifest={},
                    ir_path=None,
                    code_hash_hex=None,
                    gas_upper_bound=None,
                    warnings=[],
                    errors=[str(e)],
                )
            )
            continue

        ir_bytes: Optional[bytes]
        gas_upper: Optional[int]
        ir_bytes, gas_upper, warns = compile_contract(src_dir)
        warnings.extend(warns)

        if ir_bytes is None:
            errors.append("Compilation did not produce IR bytes.")
            results.append(
                BuildResult(
                    name=name,
                    src_dir=src_dir,
                    out_dir=out_dir / name,
                    manifest=manifest,
                    ir_path=None,
                    code_hash_hex=None,
                    gas_upper_bound=None,
                    warnings=warnings,
                    errors=errors,
                )
            )
            continue

        try:
            out_d, ir_path, manifest_out, code_hash = write_artifacts(
                name=name,
                src_dir=src_dir,
                out_root=out_dir,
                ir_bytes=ir_bytes,
                manifest=manifest,
                gas_upper_bound=gas_upper,
            )
            results.append(
                BuildResult(
                    name=name,
                    src_dir=src_dir,
                    out_dir=out_d,
                    manifest=manifest,
                    ir_path=ir_path,
                    code_hash_hex=code_hash,
                    gas_upper_bound=gas_upper,
                    warnings=warnings,
                    errors=errors,
                )
            )
        except Exception as e:
            results.append(
                BuildResult(
                    name=name,
                    src_dir=src_dir,
                    out_dir=out_dir / name,
                    manifest=manifest,
                    ir_path=None,
                    code_hash_hex=None,
                    gas_upper_bound=gas_upper,
                    warnings=warnings,
                    errors=[f"Failed to write artifacts: {e}"],
                )
            )

    print_summary(results, mode=args.summary)

    # Non-zero if any failed
    return 0 if all(r.ok for r in results) else 2


if __name__ == "__main__":
    # Encourage deterministic hashing across runs
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
