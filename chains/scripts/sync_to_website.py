#!/usr/bin/env python3
"""
chains/scripts/sync_to_website.py — copy & minify chain metadata into website/chains/

What it does
------------
• Minifies JSON files from chains/ (registry.json + top-level chain JSONs) into website/chains/
• Copies checksums.txt (verbatim) so clients can verify integrity
• Optionally copies bootstrap/ (seeds & bootnodes), icons/, and signatures/
• (Optional) Validates JSONs against chains/schemas/*.json if jsonschema is installed
• Preserves filenames; JSON output is compact (no indentation) with a trailing newline

Usage
-----
python chains/scripts/sync_to_website.py
python chains/scripts/sync_to_website.py --dst website/chains --include-icons --include-bootstrap --include-signatures
python chains/scripts/sync_to_website.py --validate --dry-run
python chains/scripts/sync_to_website.py --src chains --dst path/to/site/chains

Exit codes: 0 on success, 1 on error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[2]  # repo root default
DEFAULT_SRC = ROOT / "chains"
DEFAULT_DST = ROOT / "website" / "chains"

# Optional jsonschema validation
try:
    import jsonschema  # type: ignore
    _HAS_JSONSCHEMA = True
except Exception:
    _HAS_JSONSCHEMA = False


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def write_minified_json(obj: object, out_path: Path, dry_run: bool) -> None:
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    if dry_run:
        print(f"[dry-run] write {out_path} ({len(payload)} bytes)")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")


def copy_file(src: Path, dst: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] copy {src} -> {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def validate_with_schema(chains_dir: Path, files: List[Path]) -> None:
    """Validate selected JSON files against chains/schemas/ if jsonschema is available."""
    if not _HAS_JSONSCHEMA:
        print("[info] jsonschema not installed; skipping schema validation")
        return
    chain_schema = read_json(chains_dir / "schemas" / "chain.schema.json")
    registry_schema = read_json(chains_dir / "schemas" / "registry.schema.json")
    chain_validator = jsonschema.Draft202012Validator(chain_schema)  # type: ignore
    registry_validator = jsonschema.Draft202012Validator(registry_schema)  # type: ignore

    for p in files:
        data = read_json(p)
        if p.name == "registry.json":
            errs = list(registry_validator.iter_errors(data))
        else:
            errs = list(chain_validator.iter_errors(data))
        if errs:
            pretty = "\n".join(f" - {p}: {'/'.join(map(str,e.path)) or '(root)'}: {e.message}" for e in errs)
            raise SystemExit(f"[error] schema validation failed:\n{pretty}")


def discover_jsons(src_dir: Path) -> List[Path]:
    """Return [registry.json, other top-level *.json] in deterministic order."""
    files = []
    reg = src_dir / "registry.json"
    if reg.exists():
        files.append(reg)
    for p in sorted(src_dir.glob("*.json")):
        if p.name == "registry.json":
            continue
        files.append(p)
    return files


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Sync & minify chains/* into website/chains/")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="Source chains dir (default: chains)")
    ap.add_argument("--dst", default=str(DEFAULT_DST), help="Destination dir (default: website/chains)")
    ap.add_argument("--include-icons", action="store_true", help="Copy chains/icons/*")
    ap.add_argument("--include-bootstrap", action="store_true", help="Copy chains/bootstrap/*")
    ap.add_argument("--include-signatures", action="store_true", help="Copy chains/signatures/*")
    ap.add_argument("--validate", action="store_true", help="Validate JSONs against schemas (requires jsonschema)")
    ap.add_argument("--dry-run", action="store_true", help="Print actions but do not write/copy")
    args = ap.parse_args(argv)

    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()

    if not src.exists():
        print(f"[error] src not found: {src}")
        return 1

    # Discover JSONs
    json_paths = discover_jsons(src)
    if not json_paths:
        print(f"[warn] no top-level JSON files found in {src}")
    else:
        print(f"[info] found {len(json_paths)} JSON files")

    # Validate (optional)
    if args.validate:
        validate_with_schema(src, json_paths)

    # Ensure destination base exists
    if not args.dry_run:
        dst.mkdir(parents=True, exist_ok=True)

    # Write minified JSONs
    for p in json_paths:
        obj = read_json(p)
        out = dst / p.name
        write_minified_json(obj, out, args.dry_run)

    # Copy checksums.txt verbatim if present
    checksums = src / "checksums.txt"
    if checksums.exists():
        copy_file(checksums, dst / "checksums.txt", args.dry_run)
    else:
        print(f"[warn] {checksums} not found; skipping")

    # Optional folders
    def sync_dir(rel: str, patterns: Iterable[str]) -> None:
        src_dir = src / rel
        if not src_dir.exists():
            print(f"[info] {src_dir} missing; skip")
            return
        for pat in patterns:
            for path in src_dir.glob(pat):
                if path.is_file():
                    target = dst / rel / path.name
                    copy_file(path, target, args.dry_run)

    if args.include_icons:
        sync_dir("icons", ("*.svg", "*.png"))

    if args.include_bootstrap:
        sync_dir("bootstrap", ("*.txt", "*.json"))

    if args.include_signatures:
        # registry.sig is needed; maintainers.asc is helpful
        sync_dir("signatures", ("registry.sig", "maintainers.asc"))

    print(f"[done] synced to {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
