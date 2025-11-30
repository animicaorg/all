#!/usr/bin/env python3
"""
@animica/studio-wasm — sync_vm_py.py

Copies a minimal, browser-safe subset of the main `vm_py/` package into
`studio-wasm/py/vm_pkg/` for use with Pyodide. It optionally rewrites imports
(`vm_py.*` -> `vm_pkg.*`), strips unsupported imports, and writes a lock/manifest.

Usage:
  python studio-wasm/scripts/sync_vm_py.py
  python studio-wasm/scripts/sync_vm_py.py --src ../../vm_py
  python studio-wasm/scripts/sync_vm_py.py --dest studio-wasm/py/vm_pkg --clean --verbose
  python studio-wasm/scripts/sync_vm_py.py --dry-run

Exit codes:
  0 on success; non-zero on errors.

Requirements:
  Python 3.9+
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

# --------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------

# Files we want to copy from vm_py into vm_pkg (relative to vm_py root).
# Keep this list in sync with studio-wasm/py/vm_pkg/**/* from the design doc.
INCLUDE_FILES: List[str] = [
    # runtime (browser-safe subset)
    "runtime/engine.py",
    "runtime/gasmeter.py",
    "runtime/context.py",
    "runtime/storage_api.py",
    "runtime/events_api.py",
    "runtime/hash_api.py",
    "runtime/abi.py",
    "runtime/random_api.py",
    "loader.py",  # trimmed loader; must not import sandbox/syscalls/host state
    # stdlib (contract-facing surface for local sim)
    "stdlib/__init__.py",
    "stdlib/storage.py",
    "stdlib/events.py",
    "stdlib/hash.py",
    "stdlib/abi.py",
    "stdlib/treasury.py",  # inert in local sim
    # compiler (minimal)
    "compiler/__init__.py",
    "compiler/ir.py",
    "compiler/encode.py",
    "compiler/typecheck.py",
    "compiler/gas_estimator.py",
    # common errors
    "errors.py",
]

# Modules we want to proactively remove imports for in the subset
STRIP_IMPORT_SUFFIXES: Tuple[str, ...] = (
    "runtime/sandbox",
    "runtime/syscalls_api",
    "runtime/treasury_api",  # runtime variant (stdlib/treasury.py stays)
    "runtime/state_adapter",
)

# Destination package name (within studio-wasm)
DEST_PKG_NAME = "vm_pkg"

# Manifest/lock file written into destination root
LOCK_FILENAME = "_sync_manifest.json"

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def repo_root_from_here() -> Path:
    """Resolve repo root assuming this script lives under studio-wasm/scripts/."""
    here = Path(__file__).resolve()
    # repo/
    # ├─ vm_py/
    # └─ studio-wasm/
    return here.parents[2]


def compute_sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write_text(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def copy_binary(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def rel(path: Path, base: Path) -> str:
    return str(path.resolve().relative_to(base.resolve()))


def detect_vm_version(vm_py_root: Path) -> str:
    ver_file = vm_py_root / "version.py"
    if not ver_file.exists():
        return "0.0.0"
    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", read_text(ver_file))
    return m.group(1) if m else "0.0.0"


def rewrite_imports(text: str) -> str:
    """
    Rewrite 'from vm_py...' → 'from vm_pkg...' and 'import vm_py...' → 'import vm_pkg...'.
    Also strip lines that import undesired modules listed in STRIP_IMPORT_SUFFIXES.
    """
    # 1) Prefix rewrite (exact module name 'vm_py' → 'vm_pkg')
    text = re.sub(r"\bfrom\s+vm_py(\.| import )", r"from vm_pkg\1", text)
    text = re.sub(r"\bimport\s+vm_py\b", "import vm_pkg", text)
    text = re.sub(r"\bvm_py\.", "vm_pkg.", text)

    # 2) Strip problematic imports (sandbox / syscalls / adapters not shipped)
    lines = text.splitlines()
    keep: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        drop = False
        if stripped.startswith("from ") or stripped.startswith("import "):
            for suf in STRIP_IMPORT_SUFFIXES:
                # match either 'from vm_pkg.runtime.sandbox import ...' or 'import vm_pkg.runtime.sandbox'
                if f"vm_pkg.{suf}" in stripped or f"vm_py.{suf}" in stripped:
                    drop = True
                    break
        keep.append(f"# [sync] stripped: {ln}" if drop else ln)
    return "\n".join(keep) + ("\n" if not text.endswith("\n") else "")


def create_dest_init_py(dest_pkg_root: Path, src_version: str) -> None:
    """
    Write a minimal __init__.py for vm_pkg with a version banner and friendly exports.
    """
    content = f'''"""Animica Python VM (browser subset) — vm_pkg

This package is **auto-generated** by studio-wasm/scripts/sync_vm_py.py from the
source vm_py/ tree. It contains a reduced, Pyodide-compatible subset suitable for
in-browser simulation and compilation.

Upstream vm_py version synced: {src_version}
"""

from . import runtime as runtime
from . import stdlib as stdlib
from . import compiler as compiler
from . import errors as errors

__all__ = ["runtime", "stdlib", "compiler", "errors"]
__version__ = "{src_version}"
'''
    write_text(dest_pkg_root / "__init__.py", content)


def write_requirements(dest_root: Path) -> None:
    """
    Ensure py/requirements.txt exists (typically empty or a tiny list of pure-Python deps).
    """
    req = dest_root.parent / "requirements.txt"
    if not req.exists():
        write_text(
            req,
            "# Pyodide-friendly pure-Python requirements for vm_pkg (usually empty)\n",
        )


def write_lock(
    dest_pkg_root: Path, records: List[dict], src_version: str, src_root: Path
) -> None:
    lock = {
        "generatedBy": "studio-wasm/scripts/sync_vm_py.py",
        "srcRoot": str(src_root),
        "destPkg": str(dest_pkg_root),
        "vmVersion": src_version,
        "createdAt": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "files": records,
    }
    write_text(dest_pkg_root / LOCK_FILENAME, json.dumps(lock, indent=2) + "\n")


# --------------------------------------------------------------------------------------
# Core sync
# --------------------------------------------------------------------------------------


def plan_paths(src_root: Path, dest_pkg_root: Path) -> List[Tuple[Path, Path]]:
    pairs: List[Tuple[Path, Path]] = []
    for rel_path in INCLUDE_FILES:
        src = src_root / rel_path
        dst = dest_pkg_root / rel_path  # keep same internal layout
        pairs.append((src, dst))
    return pairs


def sync(
    src_root: Path,
    dest_pkg_root: Path,
    clean: bool,
    dry_run: bool,
    rewrite: bool,
    verbose: bool,
) -> None:
    if not src_root.exists():
        raise FileNotFoundError(f"vm_py source folder not found: {src_root}")

    if clean and not dry_run:
        # Remove everything under vm_pkg except the lock/manifest (we will overwrite it anyway)
        if dest_pkg_root.exists():
            for p in dest_pkg_root.rglob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                except Exception:
                    pass

    pairs = plan_paths(src_root, dest_pkg_root)

    # Sanity check that all source files exist
    missing = [p for p, _ in pairs if not p.exists()]
    if missing:
        rels = "\n  - ".join(str(p) for p in missing[:10])
        raise FileNotFoundError(
            f"Missing expected files under vm_py/ (first 10 shown):\n  - {rels}"
        )

    src_version = detect_vm_version(src_root)

    if verbose:
        print(f"[sync] vm_py version: {src_version}")
        print(f"[sync] Copying {len(pairs)} files → {DEST_PKG_NAME}/")

    records: List[dict] = []

    for src, dst in pairs:
        if dry_run:
            print(
                f"[dry-run] would copy {rel(src, src_root)} → {rel(dst, dest_pkg_root)}"
            )
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".py":
            text = read_text(src)
            text = rewrite_imports(text) if rewrite else text
            write_text(dst, text)
        else:
            copy_binary(src, dst)

        sha = compute_sha256(dst)
        size = dst.stat().st_size
        records.append(
            {
                "src": rel(src, src_root),
                "dst": rel(dst, dest_pkg_root),
                "size": size,
                "sha256": sha,
            }
        )
        if verbose:
            print(
                f"[sync] {rel(src, src_root)} → {rel(dst, dest_pkg_root)}  ({size} bytes)"
            )

    if not dry_run:
        # Ensure package init + requirements + manifest
        create_dest_init_py(dest_pkg_root, src_version)
        write_requirements(
            dest_pkg_root.parent
        )  # dest_pkg_root=.../py/vm_pkg → parent=.../py
        write_lock(dest_pkg_root, records, src_version, src_root)
        if verbose:
            print(f"[sync] Wrote {LOCK_FILENAME} with {len(records)} entries")


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    root = repo_root_from_here()
    default_src = (root / "vm_py").resolve()
    default_dest = (root / "studio-wasm" / "py" / DEST_PKG_NAME).resolve()

    ap = argparse.ArgumentParser(
        description="Sync minimal vm_py subset into studio-wasm/py/vm_pkg"
    )
    ap.add_argument(
        "--src", type=Path, default=Path(os.environ.get("SYNC_VM_PY_SRC", default_src))
    )
    ap.add_argument(
        "--dest",
        type=Path,
        default=Path(os.environ.get("SYNC_VM_PY_DEST", default_dest)),
    )
    ap.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing files in destination before copying",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing files",
    )
    ap.add_argument(
        "--no-rewrite-imports",
        action="store_true",
        help="Do not rewrite 'vm_py' → 'vm_pkg' imports",
    )
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return ap.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)

    src_root = args.src.resolve()
    dest_pkg_root = (
        args.dest if args.dest.name == DEST_PKG_NAME else args.dest / DEST_PKG_NAME
    ).resolve()

    if args.verbose:
        print(f"[sync] src : {src_root}")
        print(f"[sync] dest: {dest_pkg_root}")
        print(
            f"[sync] options: clean={args.clean} dry_run={args.dry_run} rewrite_imports={not args.no_rewrite_imports}"
        )

    try:
        sync(
            src_root=src_root,
            dest_pkg_root=dest_pkg_root,
            clean=args.clean,
            dry_run=args.dry_run,
            rewrite=not args.no_rewrite_imports,
            verbose=args.verbose,
        )
    except Exception as e:
        print(f"[sync] ERROR: {e}", file=sys.stderr)
        return 1

    if args.verbose:
        print("[sync] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
