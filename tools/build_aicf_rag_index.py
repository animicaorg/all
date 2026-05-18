#!/usr/bin/env python3
"""Build the AICF RAG index over Animica docs.

One-shot: walks a curated set of markdown sources, chunks them
paragraph-wise (~700 chars with overlap on boundaries), embeds each
chunk with sentence-transformers, and writes the index files consumed
by ``animica.stratum_pool.aicf_rag``:

  python/animica/stratum_pool/_data/aicf_rag/
    index.npy     — (N, D) float32 L2-normalized matrix
    chunks.json   — [{"text": str, "source": str}, ...] aligned with rows
    meta.json     — {"encoder", "dim", "built_at", "n_chunks"}

Re-run when docs change. The runtime never builds — it only loads
+ queries. Requires ``sentence-transformers`` installed in the build
environment (not at miner runtime — if the miner doesn't have it,
RAG is skipped and the base system prompt still grounds replies).

Usage:
    python3 tools/build_aicf_rag_index.py
    python3 tools/build_aicf_rag_index.py --encoder sentence-transformers/all-MiniLM-L6-v2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "python" / "animica" / "stratum_pool" / "_data" / "aicf_rag"

# Sources to index. The README is the headline doc; docs/ holds the
# operator-facing guides. We skip "*OLD*" and "*_old*" — those exist
# precisely because they were superseded and would otherwise pull the
# model toward stale answers.
SOURCE_GLOBS: list[tuple[Path, str]] = [
    (REPO_ROOT, "README.md"),
    (REPO_ROOT / "docs", "*.md"),
]
SKIP_NAME_RE = re.compile(r"(?:^|[_-])(old|deprecated|archive)(?:[._-]|$)", re.IGNORECASE)

CHUNK_CHARS = 700
CHUNK_OVERLAP = 120
MIN_CHUNK_CHARS = 80  # drop micro-chunks (headings alone, etc.)


def _iter_sources() -> list[Path]:
    files: list[Path] = []
    for root, pattern in SOURCE_GLOBS:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            if SKIP_NAME_RE.search(p.stem):
                continue
            files.append(p)
    # Dedup while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for p in files:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def _normalize(text: str) -> str:
    # Collapse runs of blank lines; keep paragraph breaks.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _chunk(text: str, *, source: str) -> list[dict]:
    """Paragraph-aware sliding window. Keeps headings stuck to the
    paragraph immediately below them so retrieval returns context
    that's intelligible on its own."""
    text = _normalize(_strip_frontmatter(text))
    if len(text) <= CHUNK_CHARS:
        if len(text) >= MIN_CHUNK_CHARS:
            return [{"text": text, "source": source}]
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[dict] = []
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) + 2 <= CHUNK_CHARS:
            buf = (buf + "\n\n" + para) if buf else para
            continue
        if buf:
            chunks.append({"text": buf, "source": source})
            # Carry tail of buf as overlap so concepts that span the
            # boundary remain searchable from either side.
            tail = buf[-CHUNK_OVERLAP:] if CHUNK_OVERLAP > 0 else ""
            buf = (tail + "\n\n" + para) if tail else para
        else:
            buf = para
        # Long single paragraph — hard-split.
        while len(buf) > CHUNK_CHARS:
            chunks.append({"text": buf[:CHUNK_CHARS], "source": source})
            buf = buf[CHUNK_CHARS - CHUNK_OVERLAP :]
    if buf and len(buf) >= MIN_CHUNK_CHARS:
        chunks.append({"text": buf, "source": source})
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--encoder",
        default="sentence-transformers/all-MiniLM-L6-v2",
        help="HuggingFace sentence-transformers model id (default: MiniLM-L6)",
    )
    ap.add_argument(
        "--batch-size", type=int, default=64,
        help="Embedding batch size (default: 64)",
    )
    args = ap.parse_args()

    try:
        import numpy as np  # type: ignore
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as exc:
        print(f"missing build dep: {exc}\n  pip install sentence-transformers numpy", file=sys.stderr)
        return 2

    sources = _iter_sources()
    if not sources:
        print("no source docs found — check SOURCE_GLOBS", file=sys.stderr)
        return 1

    all_chunks: list[dict] = []
    for path in sources:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        all_chunks.extend(_chunk(raw, source=rel))

    if not all_chunks:
        print("no chunks produced — check chunking thresholds", file=sys.stderr)
        return 1

    print(f"loaded {len(sources)} files → {len(all_chunks)} chunks")
    print(f"encoding with {args.encoder} …")
    model = SentenceTransformer(args.encoder)
    texts = [c["text"] for c in all_chunks]
    matrix = model.encode(
        texts,
        batch_size=args.batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype("float32")
    assert matrix.shape[0] == len(all_chunks), "matrix/chunks mismatch"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.save(OUT_DIR / "index.npy", matrix)
    (OUT_DIR / "chunks.json").write_text(
        json.dumps(all_chunks, ensure_ascii=False, indent=0), encoding="utf-8"
    )
    meta = {
        "encoder": args.encoder,
        "dim": int(matrix.shape[1]),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_chunks": len(all_chunks),
        "sources": sorted({c["source"] for c in all_chunks}),
    }
    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {OUT_DIR}/index.npy  shape={matrix.shape}")
    print(f"wrote {OUT_DIR}/chunks.json  ({len(all_chunks)} entries)")
    print(f"wrote {OUT_DIR}/meta.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
