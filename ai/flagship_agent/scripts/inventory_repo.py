#!/usr/bin/env python3
"""Stage: scan the Animica repo and produce an inventory of ingestible files.

Reads:  ai/configs/datasets.yaml::inventory
Writes:
    runs/<run_id>/inventory/files.jsonl       one record per file
    runs/<run_id>/inventory/summary.json      counts + size stats
    runs/<run_id>/inventory/licenses.json     per-license counts (Phase 6+)

Each files.jsonl record:
    {path, size, mtime, label, tags, oversize, sha256_head}
where sha256_head is the SHA-256 of the first 64 KiB (used by dedupe).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

# Allow direct invocation and as a pipeline subprocess.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))

from agent_runtime.config import load_config


_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_INV_DIR = _RUN_DIR / "inventory"
_INV_DIR.mkdir(parents=True, exist_ok=True)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pat) or pat in path
                for pat in patterns)


def _classify(rel_path: str, label_by_ext: dict[str, str],
              label_by_name: dict[str, str]) -> str:
    name = Path(rel_path).name
    if name in label_by_name:
        return label_by_name[name]
    suffix = Path(rel_path).suffix.lower()
    return label_by_ext.get(suffix, "other")


def _tag(rel_path: str, rules: list[dict[str, str]]) -> list[str]:
    out: list[str] = []
    for r in rules:
        try:
            if re.search(r["pattern"], rel_path):
                out.append(r["tag"])
        except re.error:
            pass
    return out


def _sha256_head(path: Path, *, bytes_cap: int = 65536) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            h.update(fh.read(bytes_cap))
    except OSError:
        return ""
    return h.hexdigest()


def _walk(root: Path, includes: list[str], excludes: list[str],
          max_bytes: int) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs.
        relroot = os.path.relpath(dirpath, root)
        rel = "" if relroot == "." else relroot
        for ex in excludes:
            if "/**" in ex:
                base = ex.split("/**")[0]
                if rel == base or rel.startswith(base + "/"):
                    dirnames[:] = []
                    break
        # Don't descend into hidden dirs except top level.
        dirnames[:] = [d for d in dirnames
                        if not d.startswith(".") or rel == ""]
        for fname in filenames:
            relfile = os.path.join(rel, fname) if rel else fname
            if _matches_any(relfile, excludes):
                continue
            if not _matches_any(relfile, includes):
                # Match against bare filename (Dockerfile, Makefile).
                if not _matches_any(fname, includes):
                    continue
            p = Path(dirpath) / fname
            try:
                if p.stat().st_size > max_bytes:
                    # still recorded but marked oversize below
                    yield p
                    continue
            except OSError:
                continue
            yield p


def main() -> int:
    cfg = load_config()
    inv = cfg.datasets["inventory"]
    root = Path(os.environ.get("ANIMICA_REPO_ROOT") or cfg.repo_root)
    includes = list(inv["include"])
    excludes = list(inv["exclude"])
    max_bytes = int(inv["max_file_bytes"])
    label_by_ext = dict(cfg.datasets["classification"]["by_ext"])
    label_by_name = dict(cfg.datasets["classification"].get("by_name", {}))
    tag_rules = list(cfg.datasets["classification"].get("path_tags", []))

    files_path = _INV_DIR / "files.jsonl"
    n_total = 0
    n_oversize = 0
    by_label: dict[str, int] = {}
    by_tag: dict[str, int] = {}
    started = time.time()

    with files_path.open("w", encoding="utf-8") as out_fh:
        for p in _walk(root, includes, excludes, max_bytes):
            try:
                st = p.stat()
            except OSError:
                continue
            rel = str(p.relative_to(root))
            oversize = st.st_size > max_bytes
            label = _classify(rel, label_by_ext, label_by_name)
            tags = _tag(rel, tag_rules)
            rec: dict[str, Any] = {
                "path": rel,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "label": label,
                "tags": tags,
                "oversize": oversize,
                "sha256_head": "" if oversize else _sha256_head(p),
            }
            out_fh.write(json.dumps(rec) + "\n")
            n_total += 1
            n_oversize += int(oversize)
            by_label[label] = by_label.get(label, 0) + 1
            for t in tags:
                by_tag[t] = by_tag.get(t, 0) + 1

    summary = {
        "schema": 1,
        "run_id": _RUN_ID,
        "root": str(root),
        "scanned_at": int(started),
        "duration_sec": round(time.time() - started, 3),
        "n_files": n_total,
        "n_oversize": n_oversize,
        "by_label": by_label,
        "by_tag": by_tag,
        "files_path": str(files_path.relative_to(_PKG_DIR)),
    }
    (_INV_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=False), encoding="utf-8")
    print(f"[inventory] {n_total} files; oversize={n_oversize}")
    print(f"[inventory] by_label: {by_label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
