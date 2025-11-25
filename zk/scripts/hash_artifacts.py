#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hash_artifacts.py — SHA3/SHA-256 checksums for zk artifacts (with canonical JSON hashing)

What this does
--------------
1) Computes stable cryptographic hashes for:
   - Verifying keys: vk.json  (both raw-file hash and canonical-JSON hash)
   - Proofs:         proof.json (raw + canonical)
   - Public inputs:  public.json (raw + canonical)
   - Circuits:       *.circom (raw)
   - Binaries:       *.zkey, *.r1cs, *.wasm (raw)
2) Canonical JSON hashing uses sorted keys and compact separators to avoid
   incidental whitespace / formatting differences between tools.
3) Emits:
   - CHECKSUMS.txt   (human-readable, sha256sum-style lines + (canon-json) markers)
   - hashes.json     (machine-readable manifest with sizes, types, and both hash kinds)

Why canonical JSON?
-------------------
snarkjs and other toolchains can serialize the *same* VK/Proof with different spacing
or key order. Canonical dumps (sorted keys, compact separators) produce a stable
digest you can safely pin in repos, specs, CI, and on-chain metadata.

Exit codes
----------
- Returns non-zero on argument errors or unreadable files.

"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- hashing backends (stdlib) -------------------------------------------------
# Python 3.6+ includes NIST SHA3 (sha3_256). We also support sha256 for parity.
import hashlib

# --- constants & types ---------------------------------------------------------

JSON_NAMES = {"vk.json", "proof.json", "public.json"}
BIN_EXTS = {".zkey", ".r1cs", ".wasm"}
CODE_EXTS = {".circom"}
JSON_EXTS = {".json"}

DEFAULT_INCLUDE = {"vk", "proof", "public", "code", "bin"}

@dataclass
class ArtifactHash:
    path: str
    rel: str
    type: str                  # vk|proof|public|code|bin|json
    size: int
    algo: str                  # sha3_256|sha256
    raw_hash: str              # 0x-prefixed hex over raw file bytes
    canonical_json_hash: Optional[str] = None  # for JSON kinds (vk/proof/public/other .json)
    details: Dict[str, Any] = None             # optional extra details (e.g., protocol/curve from vk)

# --- helpers -------------------------------------------------------------------

def canonical_dumps(obj: Any) -> str:
    """Deterministic, compact JSON with sorted keys + trailing newline."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"

def h_init(algo: str):
    a = algo.lower()
    if a == "sha3_256":
        return hashlib.sha3_256()
    if a == "sha256":
        return hashlib.sha256()
    raise SystemExit(f"Unsupported --algo {algo}. Use sha3_256 or sha256.")

def hash_bytes(data: bytes, algo: str) -> str:
    h = h_init(algo)
    h.update(data)
    return "0x" + h.hexdigest()

def hash_file_stream(path: Path, algo: str, chunk_size: int = 1 << 20) -> str:
    h = h_init(algo)
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return "0x" + h.hexdigest()

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def infer_kind(p: Path) -> str:
    name = p.name
    if name in JSON_NAMES:
        return name.replace(".json", "")  # vk|proof|public
    if p.suffix in CODE_EXTS:
        return "code"
    if p.suffix in BIN_EXTS:
        return "bin"
    if p.suffix in JSON_EXTS:
        return "json"
    return "bin"  # default fallback: treat as raw

def discover(paths: List[Path], include: Iterable[str]) -> List[Path]:
    include = set(include)
    out: List[Path] = []
    for p in paths:
        if p.is_file():
            kind = infer_kind(p)
            if kind in include or (kind not in {"vk","proof","public","code","bin"} and "json" in include):
                out.append(p)
            continue
        if p.is_dir():
            # Walk a directory; pick known names first for stability
            # 1) named files
            for name in ("vk.json", "proof.json", "public.json"):
                q = p / name
                if q.exists() and "vk" in include and name == "vk.json":
                    out.append(q)
                elif q.exists() and "proof" in include and name == "proof.json":
                    out.append(q)
                elif q.exists() and "public" in include and name == "public.json":
                    out.append(q)
            # 2) code (*.circom)
            if "code" in include:
                out.extend(sorted(p.rglob("*.circom")))
            # 3) bins
            if "bin" in include:
                for ext in BIN_EXTS:
                    out.extend(sorted(p.rglob(f"*{ext}")))
            # 4) any other JSON files (e.g., extra manifests)
            if "json" in include:
                for j in sorted(p.rglob("*.json")):
                    if j.name not in JSON_NAMES:
                        out.append(j)
    # Deduplicate while preserving order
    seen = set()
    deduped: List[Path] = []
    for q in out:
        if q.resolve() not in seen:
            deduped.append(q)
            seen.add(q.resolve())
    return deduped

def vk_metadata(vk_obj: Dict[str, Any]) -> Dict[str, Any]:
    # Common snarkjs vk fields (best-effort)
    proto = vk_obj.get("protocol") or vk_obj.get("vk", {}).get("protocol")
    curve = vk_obj.get("curve") or vk_obj.get("vk", {}).get("curve")
    return {"protocol": proto, "curve": curve}

# --- main hashing --------------------------------------------------------------

def compute_hash_record(path: Path, root: Path, algo: str) -> ArtifactHash:
    kind = infer_kind(path)
    raw = hash_file_stream(path, algo)
    size = path.stat().st_size
    rel = str(path.relative_to(root))
    details = None
    canon = None

    # JSON: compute canonical JSON hash too
    if path.suffix in JSON_EXTS:
        try:
            obj = load_json(path)
            if kind == "vk" and isinstance(obj, dict):
                details = vk_metadata(obj)
            canon = hash_bytes(canonical_dumps(obj).encode("utf-8"), algo)
        except Exception as e:
            # If JSON fails to parse, we still return raw hash to not interrupt batch.
            details = {"json_error": str(e)}

    return ArtifactHash(
        path=str(path),
        rel=rel,
        type=kind,
        size=size,
        algo=algo,
        raw_hash=raw,
        canonical_json_hash=canon,
        details=details or {},
    )

def write_checksums_txt(out_path: Path, records: List[ArtifactHash]) -> None:
    """
    Format:
      <hex>  <relpath>
      <hex>  <relpath> (canon-json)
    """
    lines: List[str] = []
    for r in records:
        lines.append(f"{r.raw_hash}  {r.rel}")
        if r.canonical_json_hash is not None:
            lines.append(f"{r.canonical_json_hash}  {r.rel} (canon-json)")
    payload = "\n".join(lines) + ("\n" if lines else "")
    out_path.write_text(payload, encoding="utf-8")

def write_hashes_json(out_path: Path, root: Path, records: List[ArtifactHash]) -> None:
    data = {
        "root": str(root),
        "algo": records[0].algo if records else "sha3_256",
        "artifacts": [asdict(r) for r in records],
    }
    out_path.write_text(canonical_dumps(data), encoding="utf-8")

# --- CLI -----------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute SHA3/SHA-256 checksums for zk artifacts (canonical JSON for vk/proof/public)."
    )
    p.add_argument(
        "paths",
        nargs="+",
        help="One or more circuit directories and/or files (vk.json, public.json, proof.json, *.circom, *.zkey, *.r1cs, *.wasm).",
    )
    p.add_argument(
        "--algo",
        default="sha3_256",
        choices=["sha3_256", "sha256"],
        help="Hash algorithm to use (default: sha3_256)",
    )
    p.add_argument(
        "--include",
        default="vk,proof,public,code,bin",
        help="Comma-separated set among {vk,proof,public,code,bin,json}. Files passed explicitly are always included.",
    )
    p.add_argument(
        "--out-prefix",
        default=None,
        help="Optional output prefix (e.g., /path/to/dir/out). Writes <prefix>.CHECKSUMS.txt and <prefix>.hashes.json. "
             "If not set and a single directory is provided, outputs are written inside that directory. "
             "If multiple roots or files are provided, defaults to current directory prefix './hash_artifacts'.",
    )
    return p.parse_args()

def main() -> None:
    args = parse_args()
    algo = args.algo
    include = {x.strip() for x in args.include.split(",") if x.strip()}

    # Resolve input paths
    in_paths = [Path(x).resolve() for x in args.paths]
    # Compute a common root for relative paths if possible, otherwise use the first parent.
    try:
        common_root = Path(*Path().resolve().parts)  # start with cwd
        # If there is a single directory path, prefer it as root.
        dir_candidates = [p for p in in_paths if p.is_dir()]
        if len(in_paths) == 1 and in_paths[0].is_dir():
            common_root = in_paths[0]
        elif dir_candidates:
            # If multiple, find a minimal common parent
            common_root = Path(*Path().resolve().parts)  # reset to cwd
            parents_sets = [set(p.parents) | {p} for p in in_paths]
            intersect = set.intersection(*parents_sets) if parents_sets else set()
            if intersect:
                # Choose the deepest common path
                common_root = max(intersect, key=lambda x: len(x.parts))
        else:
            # Only files — choose their common parent
            parents = [p.parent for p in in_paths]
            if parents:
                common_root = parents[0]
                for pr in parents[1:]:
                    # ascend until common
                    while not str(pr).startswith(str(common_root)):
                        common_root = common_root.parent
    except Exception:
        common_root = Path.cwd().resolve()

    # Discover files
    discovered = []
    for p in in_paths:
        if p.is_file():
            discovered.append(p)
        else:
            discovered.extend(discover([p], include))

    if not discovered:
        print("No artifacts found to hash (check --include filters or paths).", file=sys.stderr)
        sys.exit(2)

    # Compute records
    records: List[ArtifactHash] = []
    for fp in sorted(discovered):
        try:
            rec = compute_hash_record(fp, common_root, algo)
            records.append(rec)
        except Exception as e:
            print(f"Error hashing {fp}: {e}", file=sys.stderr)
            sys.exit(3)

    # Decide output prefix and paths
    if args.out_prefix:
        prefix = Path(args.out_prefix).resolve()
        out_txt = prefix.with_suffix(".CHECKSUMS.txt")
        out_json = prefix.with_suffix(".hashes.json")
    else:
        # If one directory was passed, write inside it. Else, use ./hash_artifacts.*
        single_dir = len(in_paths) == 1 and in_paths[0].is_dir()
        if single_dir:
            prefix = in_paths[0] / "hash_artifacts"
        else:
            prefix = Path.cwd() / "hash_artifacts"
        out_txt = prefix.with_suffix(".CHECKSUMS.txt")
        out_json = prefix.with_suffix(".hashes.json")

    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

    # Emit files
    write_checksums_txt(out_txt, records)
    write_hashes_json(out_json, common_root, records)

    # Pretty console summary
    print(f"Algorithm: {algo}")
    print(f"Root:      {common_root}")
    print(f"Wrote:     {out_txt}")
    print(f"Wrote:     {out_json}")
    print("")
    # Summarize VK canonical hash prominently (if present)
    vk_recs = [r for r in records if r.type == "vk" and r.canonical_json_hash]
    if vk_recs:
        r = vk_recs[0]
        meta = r.details or {}
        proto = meta.get("protocol") or "?"
        curve = meta.get("curve") or "?"
        print("VK pin:")
        print(f"  protocol: {proto}")
        print(f"  curve:    {curve}")
        print(f"  canon:    {r.canonical_json_hash}")
        print(f"  raw:      {r.raw_hash}")
        print("")
    # Compact list view
    print("Artifacts:")
    for r in records:
        line = f"  [{r.type:<6}] {r.rel}  ({r.size} bytes)"
        print(line)
        if r.canonical_json_hash:
            print(f"          canon: {r.canonical_json_hash}")
        print(f"          raw:   {r.raw_hash}")

if __name__ == "__main__":
    main()
