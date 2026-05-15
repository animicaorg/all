#!/usr/bin/env python3
"""Stage: chunk files from the inventory into training-ready corpora.

Reads:  runs/<run_id>/inventory/files.jsonl + ai/configs/datasets.yaml
Writes: runs/<run_id>/corpora/raw.jsonl + summary.json
Each raw.jsonl record: {chunk_id, path, label, tags, text, n_tokens_approx, sha256}
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"
                       / "agent_runtime" / "src"))
from agent_runtime.config import load_config

_RUN_ID = os.environ["FLAGSHIP_RUN_ID"]
_PKG_DIR = Path(os.environ["FLAGSHIP_PKG_DIR"])
_RUN_DIR = _PKG_DIR / "runs" / _RUN_ID
_OUT_DIR = _RUN_DIR / "corpora"
_OUT_DIR.mkdir(parents=True, exist_ok=True)


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _chunk_whole_file(text: str) -> list[str]:
    return [text] if text.strip() else []


def _chunk_lines(text: str, max_tokens: int) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    cur = 0
    for line in text.splitlines(keepends=True):
        lt = _approx_tokens(line)
        if cur + lt > max_tokens and buf:
            out.append("".join(buf))
            buf, cur = [], 0
        buf.append(line)
        cur += lt
    if buf:
        out.append("".join(buf))
    return out


def _chunk_python_ast(text: str, max_tokens: int) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _chunk_lines(text, max_tokens)
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    last_end = 0
    for node in tree.body:
        start = getattr(node, "lineno", 1) - 1
        end = getattr(node, "end_lineno", start + 1)
        if start > last_end:
            preamble = "".join(lines[last_end:start])
            if preamble.strip():
                out.extend(_chunk_lines(preamble, max_tokens))
        block = "".join(lines[start:end])
        if _approx_tokens(block) > max_tokens:
            out.extend(_chunk_lines(block, max_tokens))
        else:
            out.append(block)
        last_end = end
    if last_end < len(lines):
        tail = "".join(lines[last_end:])
        if tail.strip():
            out.extend(_chunk_lines(tail, max_tokens))
    return [c for c in out if c.strip()]


_MD_HEADING = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def _chunk_markdown(text: str, max_tokens: int) -> list[str]:
    headings = [m.start() for m in _MD_HEADING.finditer(text)]
    if not headings:
        return _chunk_lines(text, max_tokens)
    headings.append(len(text))
    chunks: list[str] = []
    for i in range(len(headings) - 1):
        block = text[headings[i]:headings[i + 1]]
        if _approx_tokens(block) > max_tokens:
            chunks.extend(_chunk_lines(block, max_tokens))
        else:
            chunks.append(block)
    return [c for c in chunks if c.strip()]


_BLOCK_HINT = re.compile(
    r"^(?:export\s+)?(?:async\s+)?"
    r"(?:fn|func|class|struct|enum|trait|interface|type|impl|mod|"
    r"function|const|let|var|pub)\b",
    re.MULTILINE,
)


def _chunk_regex_blocks(text: str, max_tokens: int) -> list[str]:
    hits = [m.start() for m in _BLOCK_HINT.finditer(text)]
    if not hits:
        return _chunk_lines(text, max_tokens)
    hits.append(len(text))
    out: list[str] = []
    for i in range(len(hits) - 1):
        block = text[hits[i]:hits[i + 1]]
        if _approx_tokens(block) > max_tokens:
            out.extend(_chunk_lines(block, max_tokens))
        else:
            out.append(block)
    return [c for c in out if c.strip()]


_STRATEGIES = {
    "whole_file": lambda t, mx: _chunk_whole_file(t),
    "line_groups": _chunk_lines,
    "ast_top_level": _chunk_python_ast,
    "markdown_heading": _chunk_markdown,
    "regex_blocks": _chunk_regex_blocks,
    "statement_split": _chunk_lines,
}


def main() -> int:
    cfg = load_config()
    dataset_cfg = cfg.datasets
    repo_root = Path(os.environ.get("ANIMICA_REPO_ROOT") or cfg.repo_root)
    inv_files = _RUN_DIR / "inventory" / "files.jsonl"
    if not inv_files.is_file():
        print(f"[corpora] missing {inv_files}", file=sys.stderr)
        return 1
    strategy_map = dataset_cfg["corpora"]["strategy_by_label"]
    max_tokens = int(dataset_cfg["corpora"]["chunk_max_tokens"])
    out_path = _OUT_DIR / "raw.jsonl"

    n_files = 0
    n_chunks = 0
    by_label: dict[str, int] = {}
    next_id = 0
    with inv_files.open("r", encoding="utf-8") as ifh, \
         out_path.open("w", encoding="utf-8") as ofh:
        for line in ifh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("oversize"):
                continue
            label = rec.get("label", "other")
            strat = _STRATEGIES.get(strategy_map.get(label, "whole_file"),
                                     _chunk_whole_file)
            path = repo_root / rec["path"]
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n_files += 1
            chunks = strat(text, max_tokens) if strat.__code__.co_argcount > 1 \
                else strat(text)   # type: ignore[arg-type]
            for c in chunks:
                cid = next_id
                next_id += 1
                ofh.write(json.dumps({
                    "chunk_id": cid,
                    "path": rec["path"],
                    "label": label,
                    "tags": rec.get("tags", []),
                    "text": c,
                    "n_tokens_approx": _approx_tokens(c),
                    "sha256": hashlib.sha256(c.encode("utf-8")).hexdigest(),
                }) + "\n")
                n_chunks += 1
                by_label[label] = by_label.get(label, 0) + 1

    (_OUT_DIR / "summary.json").write_text(json.dumps({
        "schema": 1,
        "run_id": _RUN_ID,
        "n_files_chunked": n_files,
        "n_chunks": n_chunks,
        "by_label": by_label,
        "out": str(out_path.relative_to(_PKG_DIR)),
    }, indent=2), encoding="utf-8")
    print(f"[corpora] {n_chunks} chunks from {n_files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
