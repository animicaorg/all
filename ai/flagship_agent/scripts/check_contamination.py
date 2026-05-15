#!/usr/bin/env python3
"""Stage: detect train/eval contamination by sha256 and 8-gram overlap.

Reads:  runs/<run_id>/datasets/sft.jsonl + eval.jsonl + ai/configs/eval.yaml
Writes: runs/<run_id>/datasets/contamination_report.json
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
_DS_DIR = _RUN_DIR / "datasets"

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    toks = _WORD.findall(text.lower())
    if len(toks) < n:
        return set()
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def _text_of(rec: dict) -> str:
    msgs = rec.get("messages") or []
    return " ".join(m.get("content", "") for m in msgs)


def main() -> int:
    cfg = load_config()
    contam_cfg = cfg.eval["suites"]["contamination"]
    n = int(contam_cfg.get("ngram", 8))
    threshold = float(contam_cfg.get("near_overlap_threshold", 0.6))
    sft = _DS_DIR / "sft.jsonl"
    evalp = _DS_DIR / "eval.jsonl"
    if not sft.is_file() or not evalp.is_file():
        print(f"[contam] missing inputs ({sft.exists()=}/{evalp.exists()=})",
              file=sys.stderr)
        return 1

    train_sha: set[str] = set()
    train_ngrams: list[set[tuple[str, ...]]] = []
    with sft.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = _text_of(rec)
            train_sha.add(hashlib.sha256(t.encode("utf-8")).hexdigest())
            if len(train_ngrams) < 20000:    # cap memory
                train_ngrams.append(_ngrams(t, n))

    n_eval = 0
    n_exact = 0
    near_hits: list[dict] = []
    with evalp.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_eval += 1
            t = _text_of(rec)
            sha = hashlib.sha256(t.encode("utf-8")).hexdigest()
            if sha in train_sha:
                n_exact += 1
                near_hits.append({"id": rec.get("id", ""),
                                  "kind": "exact_sha256"})
                continue
            ng = _ngrams(t, n)
            if not ng:
                continue
            max_overlap = 0.0
            for tng in train_ngrams:
                if not tng:
                    continue
                inter = len(ng & tng)
                if inter == 0:
                    continue
                j = inter / max(len(ng), 1)
                if j > max_overlap:
                    max_overlap = j
                if max_overlap >= 1.0:
                    break
            if max_overlap >= threshold:
                near_hits.append({"id": rec.get("id", ""),
                                  "kind": "ngram",
                                  "jaccard": round(max_overlap, 3)})

    report = {
        "schema": 1, "run_id": _RUN_ID,
        "n_eval": n_eval, "n_exact_dup": n_exact,
        "n_near_dup": len(near_hits) - n_exact,
        "near_threshold": threshold, "ngram": n,
        "contaminated": n_exact + len(near_hits) > 0,
        "hits": near_hits[:200],
    }
    (_DS_DIR / "contamination_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(f"[contam] eval={n_eval} exact_dup={n_exact} "
          f"near_dup={len(near_hits) - n_exact} contaminated="
          f"{report['contaminated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
