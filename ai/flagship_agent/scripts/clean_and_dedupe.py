#!/usr/bin/env python3
"""Stage: secret-scan + exact-dedupe + near-dedupe corpora.

Reads:  runs/<run_id>/corpora/raw.jsonl + ai/configs/safety.yaml
Writes: runs/<run_id>/corpora/clean.jsonl
        runs/<run_id>/corpora/quarantine.jsonl
        runs/<run_id>/corpora/dedupe_stats.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_OUT_DIR = _RUN_DIR / "corpora"


def _normalize(text: str, ops: list[str]) -> str:
    out = text
    if "strip_trailing_ws" in ops:
        out = "\n".join(line.rstrip() for line in out.splitlines())
    if "collapse_blank_lines" in ops:
        out = re.sub(r"\n{3,}", "\n\n", out)
    return out


def _scan_secrets(text: str, patterns: list[dict]) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        try:
            if re.search(pat["regex"], text):
                hits.append(pat["name"])
        except re.error:
            continue
    return hits


def _minhash(text: str, num_perm: int) -> list[int]:
    # Lightweight homegrown MinHash to avoid heavy deps. Word-shingles of size 5.
    words = text.split()
    if len(words) < 5:
        return [0] * num_perm
    shingles = set()
    for i in range(len(words) - 4):
        shingles.add(" ".join(words[i:i + 5]))
    sigs: list[int] = []
    for seed in range(num_perm):
        seed_bytes = seed.to_bytes(4, "little")
        best = (1 << 64) - 1
        for sh in shingles:
            h = hashlib.blake2b(sh.encode("utf-8") + seed_bytes,
                                digest_size=8).digest()
            v = int.from_bytes(h, "little")
            if v < best:
                best = v
        sigs.append(best)
    return sigs


def _jaccard(a: list[int], b: list[int]) -> float:
    if not a or not b:
        return 0.0
    match = sum(1 for x, y in zip(a, b) if x == y)
    return match / len(a)


def main() -> int:
    cfg = load_config()
    safety = cfg.safety
    dedupe_cfg = cfg.datasets["dedupe"]
    raw = _OUT_DIR / "raw.jsonl"
    if not raw.is_file():
        print(f"[dedupe] missing {raw}", file=sys.stderr)
        return 1
    clean = _OUT_DIR / "clean.jsonl"
    quar = _OUT_DIR / "quarantine.jsonl"

    norm_ops = list(dedupe_cfg.get("exact", {}).get("normalize", []))
    near = dedupe_cfg.get("near", {})
    near_enabled = bool(near.get("enabled", False)) and cfg.mode != "simulate"
    near_threshold = float(near.get("jaccard_threshold", 0.85))
    num_perm = int(near.get("num_perm", 128))

    seen_sha: set[str] = set()
    minhash_index: list[tuple[int, list[int]]] = []
    secret_patterns = list(safety["secret_scan"].get("patterns", []))

    n_in = n_quarantine = n_exact_dup = n_near_dup = n_clean = 0
    with raw.open("r", encoding="utf-8") as ifh, \
         clean.open("w", encoding="utf-8") as cfh, \
         quar.open("w", encoding="utf-8") as qfh:
        for line in ifh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_in += 1
            text = rec["text"]
            hits = _scan_secrets(text, secret_patterns) \
                if safety["secret_scan"].get("enabled", True) else []
            if hits:
                rec["quarantine_reason"] = "secret_scan"
                rec["matched_patterns"] = hits
                qfh.write(json.dumps(rec) + "\n")
                n_quarantine += 1
                continue
            norm = _normalize(text, norm_ops)
            sha = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            if sha in seen_sha:
                n_exact_dup += 1
                continue
            seen_sha.add(sha)
            if near_enabled:
                sig = _minhash(norm, num_perm)
                dup = False
                for _, other in minhash_index[-2000:]:   # rolling window
                    if _jaccard(sig, other) >= near_threshold:
                        dup = True
                        break
                if dup:
                    n_near_dup += 1
                    continue
                minhash_index.append((rec["chunk_id"], sig))
            rec["text"] = norm
            rec["sha256"] = sha
            cfh.write(json.dumps(rec) + "\n")
            n_clean += 1

    stats = {
        "schema": 1, "run_id": _RUN_ID,
        "n_in": n_in, "n_clean": n_clean,
        "n_quarantine": n_quarantine,
        "n_exact_dup": n_exact_dup,
        "n_near_dup": n_near_dup,
        "near_enabled": near_enabled,
        "near_threshold": near_threshold,
    }
    (_OUT_DIR / "dedupe_stats.json").write_text(
        json.dumps(stats, indent=2), encoding="utf-8")
    print(f"[dedupe] in={n_in} clean={n_clean} quarantine={n_quarantine} "
          f"exact_dup={n_exact_dup} near_dup={n_near_dup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
