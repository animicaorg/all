"""
animica.ena.retrieval
=====================

Chunking, embedding-backed index builds, and keyword / semantic / hybrid
search over the ENA store. Embeddings come from a pluggable
:class:`~animica.ena.providers.EmbeddingAdapter`; the legacy hashing provider
remains as an offline fallback.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import Chunk, IndexMeta, new_uuid, now_ts, sha3_hex
from .providers import EmbeddingAdapter

_TEXT_SUFFIXES = {".txt", ".md", ".rst", ".py", ".ts", ".js", ".json", ".toml",
                  ".yaml", ".yml", ".cfg", ".ini", ".go", ".rs", ""}


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, *, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    text = text.replace("\r\n", "\n")
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        # prefer to break on a paragraph/sentence boundary
        if end < n:
            window = text[start:end]
            for sep in ("\n\n", "\n", ". "):
                idx = window.rfind(sep)
                if idx > max_chars // 2:
                    end = start + idx + len(sep)
                    break
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _iter_source_files(paths: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file() and child.suffix.lower() in _TEXT_SUFFIXES \
                        and ".git" not in child.parts:
                    out.append(child)
    return out


def _read_records(path: Path) -> list[tuple[str, str]]:
    """Return (source, text) records. JSONL emits one record per text-y row."""
    if path.suffix.lower() == ".jsonl":
        out = []
        from .datasets import read_jsonl
        for i, rec in enumerate(read_jsonl(path)):
            text = rec.get("text") or rec.get("content") or rec.get("response") or ""
            if text:
                out.append((f"{path}#{i}", str(text)))
        return out
    try:
        return [(str(path), path.read_text(encoding="utf-8", errors="replace"))]
    except OSError:
        return []


# ---------------------------------------------------------------------------
# Index build
# ---------------------------------------------------------------------------

def build_index(store, paths: list[str], *, index_name: str,
                embedder: EmbeddingAdapter, embedding_provider: str,
                embedding_model: str, max_chars: int = 1200,
                overlap: int = 150) -> dict[str, Any]:
    files = _iter_source_files(paths)
    chunk_rows: list[dict[str, Any]] = []
    ordinal = 0
    texts: list[str] = []
    pending: list[dict[str, Any]] = []
    for f in files:
        for source, text in _read_records(f):
            for piece in chunk_text(text, max_chars=max_chars, overlap=overlap):
                ch = Chunk(chunk_id="ck-" + new_uuid()[:16], index_name=index_name,
                           source=source, ordinal=ordinal, text=piece,
                           text_hash=sha3_hex(piece)).to_dict()
                pending.append(ch)
                texts.append(piece)
                ordinal += 1
    dims = embedder.dimensions
    if texts:
        try:
            vectors = embedder.embed(texts)
            dims = len(vectors[0]) if vectors else dims
            for ch, vec in zip(pending, vectors):
                ch["embedding"] = vec
                ch["embedding_dim"] = len(vec)
        except Exception:  # noqa: BLE001 - keep keyword-only index on embed failure
            for ch in pending:
                ch["embedding"] = None
    chunk_rows = pending
    store.add_chunks(chunk_rows)
    meta = IndexMeta(name=index_name, embedding_provider=embedding_provider,
                     embedding_model=embedding_model, dimensions=dims,
                     chunk_count=len(chunk_rows), created_at=now_ts()).to_dict()
    store.upsert_index(meta)
    return {"index": index_name, "files": len(files), "chunks": len(chunk_rows),
            "dimensions": dims, "embedded": bool(chunk_rows and chunk_rows[0].get("embedding"))}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _keyword_score(query: str, text: str) -> float:
    q_tokens = [t for t in query.lower().split() if t]
    if not q_tokens:
        return 0.0
    low = text.lower()
    hits = sum(low.count(tok) for tok in q_tokens)
    return hits / (1.0 + math.log(1 + len(text)))


def search(store, query: str, *, mode: str = "hybrid",
           index: Optional[str] = None, embedder: Optional[EmbeddingAdapter] = None,
           limit: int = 8) -> list[dict[str, Any]]:
    chunks = store.iter_chunks(index)
    if not chunks:
        return []
    results: list[dict[str, Any]] = []

    q_vec: Optional[list[float]] = None
    if mode in ("semantic", "hybrid") and embedder is not None:
        try:
            q_vec = embedder.embed([query])[0]
        except Exception:  # noqa: BLE001 - degrade to keyword
            q_vec = None

    for ch in chunks:
        kw = _keyword_score(query, ch["text"])
        sem = 0.0
        if q_vec is not None and ch.get("embedding"):
            sem = _cosine(q_vec, ch["embedding"])
        if mode == "keyword":
            score = kw
        elif mode == "semantic":
            score = sem
        else:  # hybrid
            score = 0.6 * sem + 0.4 * kw
        if score <= 0:
            continue
        results.append({"chunk_id": ch["chunk_id"], "source": ch["source"],
                        "text": ch["text"], "score": round(score, 6), "mode": mode,
                        "metadata": ch.get("metadata", {})})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]
