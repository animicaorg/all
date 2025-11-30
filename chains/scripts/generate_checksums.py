#!/usr/bin/env python3
"""
chains/scripts/generate_checksums.py — compute SHA-256s for chain metadata and write chains/checksums.txt

By default it:
  • Scans top-level JSON files under chains/ (e.g., animica.*.json, registry.json)
  • Computes sha256 over raw file bytes
  • Writes chains/checksums.txt in deterministic, sorted order: "<sha256>  <relative/path>"

Optional:
  --update-embedded   Also update each JSON's "checksum" field to match the computed value (in-place).
  --dry-run           Show what would be written without modifying files.
  --include path ...  Extra files to include (relative to repo root).
  --only path ...     Only hash these paths (skips auto-discovery).

Examples:
  python chains/scripts/generate_checksums.py
  python chains/scripts/generate_checksums.py --update-embedded
  python chains/scripts/generate_checksums.py --only chains/animica.testnet.json chains/registry.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]  # repo root
CHAINS_DIR = ROOT / "chains"
OUTFILE = CHAINS_DIR / "checksums.txt"


def sha256_hex(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_targets() -> List[Path]:
    """
    Find the JSON files we sign:
      • All *.json directly under chains/ (not in subdirs)
      • Excludes schema files (live under chains/schemas/*), signatures, bootstrap, etc.
    """
    out: List[Path] = []
    for p in sorted(CHAINS_DIR.glob("*.json")):
        out.append(p)
    return out


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT).as_posix())


def update_embedded_checksum(path: Path, digest: str) -> bool:
    """
    In-place update of the "checksum" field inside the JSON, if present.
    Returns True if a change was written.
    Note: This makes the file's content change, so its hash changes.
    We therefore compute hashes BEFORE updating embedded fields, and write the list
    as the source of truth, matching our README guidance.
    """
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False

    prev = obj.get("checksum")
    if prev == digest:
        return False

    obj["checksum"] = digest
    # Write compact but stable formatting (indent=2 keeps diffs readable)
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return True


def write_checksums(pairs: List[Tuple[str, str]], dest: Path, dry_run: bool) -> None:
    """
    pairs: list of (hash, relpath) already sorted by relpath
    Writes with LF endings, no trailing spaces.
    """
    lines = [f"{h}  {rp}" for h, rp in pairs]
    content = "\n".join(lines) + ("\n" if lines else "")
    if dry_run:
        print("---- chains/checksums.txt (dry-run) ----")
        print(content, end="")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        print(f"Wrote {rel(dest)} ({len(pairs)} entries)")


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Generate chains/checksums.txt for Animica chain metadata."
    )
    ap.add_argument(
        "--update-embedded",
        action="store_true",
        help="Also update each JSON's 'checksum' field to match computed digest (in-place).",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="Show actions without writing files."
    )
    ap.add_argument(
        "--include",
        nargs="*",
        default=[],
        help="Extra file paths to include (relative to repo root).",
    )
    ap.add_argument(
        "--only", nargs="*", default=[], help="Only hash these paths (skip discovery)."
    )
    args = ap.parse_args(argv)

    # Collect targets
    if args.only:
        targets = [ROOT / p for p in args.only]
    else:
        targets = discover_targets()
        targets += [ROOT / p for p in args.include]

    # De-duplicate and ensure existence
    unique = []
    seen = set()
    for t in targets:
        if not t.exists():
            print(f"warning: {rel(t)} does not exist, skipping")
            continue
        key = t.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(t)

    # Compute digests BEFORE any embedded-field updates
    records: List[Tuple[str, str, Path]] = []
    for p in unique:
        digest = sha256_hex(p)
        records.append((digest, rel(p), p))

    # Sort by relative path for determinism
    records.sort(key=lambda x: x[1])

    # Write checksums.txt
    pairs = [(h, rp) for (h, rp, _p) in records]
    write_checksums(pairs, OUTFILE, args.dry_run)

    # Optionally update embedded checksums now
    if args.update_embedded:
        changed = 0
        for digest, _rp, p in records:
            if p.suffix.lower() == ".json":
                if update_embedded_checksum(p, digest):
                    changed += 1
        if changed:
            print(f"Updated embedded checksum field in {changed} file(s).")
        else:
            print("No embedded checksum updates were necessary.")

        # After touching files, remind the user to re-run to refresh checksums.txt
        print(
            "NOTE: Re-run this script to refresh chains/checksums.txt after embedded updates."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
