#!/usr/bin/env python3
"""Stage: build a lexical (BM25) retrieval index over the clean corpus.

Reads:  runs/<run_id>/corpora/clean.jsonl + ai/configs/retrieval.yaml
Writes: runs/<run_id>/index/lexical.idx (pickle of BM25 + doc store)
        runs/<run_id>/index/metadata.jsonl (chunk_id, path, label, tags)
"""

from __future__ import annotations

import json
import os
import pickle
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_OUT_DIR = _RUN_DIR / "index"
_OUT_DIR.mkdir(parents=True, exist_ok=True)

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


class BM25Index:
    """Tiny in-memory BM25 over a static corpus. Pure stdlib."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: list[dict] = []
        self.doc_len: list[int] = []
        self.term_docs: dict[str, list[tuple[int, int]]] = {}
        self.df: Counter[str] = Counter()
        self.avgdl: float = 0.0

    def add(self, doc_id: int, text: str, meta: dict) -> None:
        tokens = _tokenize(text)
        self.docs.append({"id": doc_id, "meta": meta})
        self.doc_len.append(len(tokens))
        tf: Counter[str] = Counter(tokens)
        for term, c in tf.items():
            self.term_docs.setdefault(term, []).append((doc_id, c))
            self.df[term] += 1

    def finalize(self) -> None:
        self.avgdl = sum(self.doc_len) / max(1, len(self.doc_len))


def main() -> int:
    cfg = load_config()
    lex = cfg.retrieval["lexical"]
    if not lex.get("enabled", True):
        print("[indices] lexical disabled by config; skipping")
        (_OUT_DIR / "lexical.idx").write_bytes(b"")
        (_OUT_DIR / "metadata.jsonl").write_text("", encoding="utf-8")
        return 0
    clean = _RUN_DIR / "corpora" / "clean.jsonl"
    if not clean.is_file():
        print(f"[indices] missing {clean}", file=sys.stderr)
        return 1
    bm = BM25Index(k1=float(lex["bm25"]["k1"]),
                   b=float(lex["bm25"]["b"]))
    meta_out = _OUT_DIR / "metadata.jsonl"
    n = 0
    with clean.open("r", encoding="utf-8") as ifh, \
         meta_out.open("w", encoding="utf-8") as mfh:
        for line in ifh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            bm.add(rec["chunk_id"], rec["text"],
                   {"path": rec["path"], "label": rec.get("label", ""),
                    "tags": rec.get("tags", []),
                    "sha256": rec.get("sha256", "")})
            mfh.write(json.dumps({
                "chunk_id": rec["chunk_id"],
                "path": rec["path"],
                "label": rec.get("label", ""),
                "tags": rec.get("tags", []),
            }) + "\n")
            n += 1
    bm.finalize()
    with (_OUT_DIR / "lexical.idx").open("wb") as fh:
        pickle.dump(bm, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[indices] BM25 over {n} chunks (vocab={len(bm.df)}, "
          f"avgdl={bm.avgdl:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
