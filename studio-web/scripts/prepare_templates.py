#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_templates.py
--------------------
Sanity-checks contract templates and generates the canonical
studio-web/src/fixtures/templates/index.json file.

What it does:
  • Scans each directory in src/fixtures/templates/* for a manifest.json
    and a contract.py (or a custom source file declared in the manifest).
  • Light validation of the manifest's key fields.
  • Resolves any relative ABI path in the manifest and validates it exists.
  • Computes a deterministic SHA3-256 code_hash of the contract source.
  • Emits an index.json containing an array of template descriptors:
        [
          {
            "id": "counter",
            "name": "Counter",
            "description": "Deterministic counter example",
            "manifest": "counter/manifest.json",
            "source": "counter/contract.py",
            "abi": "counter/manifest.json#abi",   // or "counter/abi.json" if separate
            "code_hash": "0x…",
            "bytes": 1234
          },
          …
        ]

Usage:
  python studio-web/scripts/prepare_templates.py \
    --templates-dir studio-web/src/fixtures/templates \
    --out studio-web/src/fixtures/templates/index.json \
    --pretty

Note:
  This script deliberately has no external dependencies beyond the stdlib.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TEMPLATES_DIR = Path("studio-web/src/fixtures/templates")
DEFAULT_OUT = DEFAULT_TEMPLATES_DIR / "index.json"


@dataclass
class TemplateEntry:
    id: str
    name: str
    description: str
    manifest: str  # relative path from templates root
    source: str  # relative path from templates root
    abi: str  # relative path, or "manifest.json#abi" marker
    code_hash: str  # 0x-prefixed sha3_256 hex of source
    bytes: int  # size of source file in bytes


class ValidationError(Exception):
    pass


def sha3_256_hex(data: bytes) -> str:
    return "0x" + hashlib.sha3_256(data).hexdigest()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValidationError(f"Invalid JSON at {path}: {e}") from e
    except OSError as e:
        raise ValidationError(f"Failed to read {path}: {e}") from e


def detect_source_file(root: Path, manifest: Dict[str, Any]) -> Path:
    # Priority: explicit "source" in manifest → contract.py → first *.py file
    if isinstance(manifest.get("source"), str):
        cand = root / manifest["source"]
        if cand.is_file():
            return cand
        raise ValidationError(
            f'specified source "{manifest["source"]}" not found in {root}'
        )
    if (root / "contract.py").is_file():
        return root / "contract.py"
    py_files = sorted(root.glob("*.py"))
    if py_files:
        return py_files[0]
    raise ValidationError(
        f"No Python source found in {root} (looked for contract.py or *.py)"
    )


def resolve_abi_reference(
    root: Path, manifest: Dict[str, Any]
) -> Tuple[str, Optional[Path]]:
    """
    Returns (abi_ref, abi_path_if_file).
    If ABI is embedded in manifest as array/object, we point to "manifest.json#abi".
    If ABI is a relative string path, we validate it exists and return that path.
    """
    # Common shapes:
    #  - {"abi": [...]}            -> embedded ABI
    #  - {"abi": "abi.json"}       -> file at path
    #  - {"ABI": [...]}            -> tolerate case variant
    abi_key = "abi" if "abi" in manifest else ("ABI" if "ABI" in manifest else None)
    if abi_key is None:
        # Some manifests might omit ABI (e.g., deploy-time only). Treat as embedded-empty.
        return "manifest.json#abi", None

    abi_val = manifest.get(abi_key)
    if isinstance(abi_val, (list, dict)):
        return "manifest.json#abi", None
    if isinstance(abi_val, str):
        p = root / abi_val
        if not p.is_file():
            raise ValidationError(f'ABI file "{abi_val}" does not exist under {root}')
        return abi_val, p
    raise ValidationError(
        f'Unexpected type for "{abi_key}" in manifest: {type(abi_val).__name__}'
    )


def light_validate_manifest(manifest: Dict[str, Any], location: Path) -> None:
    # Minimal required fields: name (string). Optional: description (string), version (string), abi (any of accepted forms)
    if (
        "name" not in manifest
        or not isinstance(manifest["name"], str)
        or not manifest["name"].strip()
    ):
        raise ValidationError(f'Manifest at {location} must include a non-empty "name"')
    if "description" in manifest and not isinstance(manifest["description"], str):
        raise ValidationError(f'"description" must be a string in {location}')
    if "version" in manifest and not isinstance(manifest["version"], (str, int)):
        raise ValidationError(f'"version" must be a string/number in {location}')
    # ABI is validated by resolve_abi_reference


def build_entry(templates_root: Path, dir_path: Path) -> TemplateEntry:
    manifest_path = dir_path / "manifest.json"
    if not manifest_path.is_file():
        raise ValidationError(f"Missing manifest.json in {dir_path}")

    manifest = load_json(manifest_path)
    light_validate_manifest(manifest, manifest_path)

    src_path = detect_source_file(dir_path, manifest)
    abi_ref, abi_file = resolve_abi_reference(dir_path, manifest)

    # Compute code hash and size
    source_bytes = src_path.read_bytes()
    code_hash = sha3_256_hex(source_bytes)

    # Relative paths in index (relative to templates root)
    manifest_rel = str(manifest_path.relative_to(templates_root).as_posix())
    source_rel = str(src_path.relative_to(templates_root).as_posix())
    abi_rel = (
        abi_ref
        if abi_ref == "manifest.json#abi"
        else str((dir_path / abi_ref).relative_to(templates_root).as_posix())
    )

    name = manifest.get("name") or dir_path.name
    description = manifest.get("description") or ""

    return TemplateEntry(
        id=dir_path.name,
        name=name,
        description=description,
        manifest=manifest_rel,
        source=source_rel,
        abi=abi_rel,
        code_hash=code_hash,
        bytes=len(source_bytes),
    )


def scan_templates(
    templates_root: Path, verbose: bool = False
) -> Tuple[List[TemplateEntry], List[str]]:
    entries: List[TemplateEntry] = []
    warnings: List[str] = []

    if not templates_root.is_dir():
        raise ValidationError(f"Templates directory not found: {templates_root}")

    for child in sorted(templates_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in ("__pycache__", ".DS_Store"):
            continue
        try:
            entry = build_entry(templates_root, child)
            entries.append(entry)
            if verbose:
                print(f"✓ {child.name}: {entry.name} ({entry.code_hash})")
        except ValidationError as e:
            msg = f"Skipped {child.name}: {e}"
            warnings.append(msg)
            print(f"⚠ {msg}", file=sys.stderr)
    return entries, warnings


def write_index(out_path: Path, entries: List[TemplateEntry], pretty: bool) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic ordering by id
    payload = [asdict(e) for e in sorted(entries, key=lambda x: x.id.lower())]
    with out_path.open("w", encoding="utf-8") as f:
        if pretty:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        else:
            json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate studio template index.json with sanity checks."
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR,
        help=f"Path to templates root (default: {DEFAULT_TEMPLATES_DIR})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output index.json path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--pretty", action="store_true", help="Pretty-print JSON with indentation."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any template is skipped.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output.")
    args = parser.parse_args()

    try:
        entries, warnings = scan_templates(args.templates_dir, verbose=args.verbose)
        if not entries:
            raise ValidationError("No valid templates found. Aborting.")
        write_index(args.out, entries, pretty=args.pretty)
        print(f"Wrote {len(entries)} template entries → {args.out}")
        if warnings:
            print(f"{len(warnings)} warning(s):", file=sys.stderr)
            for w in warnings:
                print("  - " + w, file=sys.stderr)
            if args.strict:
                return 2
        return 0
    except ValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
