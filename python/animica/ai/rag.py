"""
animica.ai.rag — a tiny, dependency-free local RAG store for `animica ai rag`.

Chunks text, embeds it through the same ENA embedding adapters the gateway uses,
and persists vectors to ``~/.animica/rag/<name>.json``. Retrieval is brute-force
cosine over the stored vectors — no numpy, no external vector DB — which is the
right size for a CLI's "index a few docs and ask about them" workflow.

The embedding provider is whatever the node/config resolves (hashing works fully
offline; ollama/openai when configured), so this never forces a heavy dependency.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional

from ..cli.paths import ensure_file_dir, get_animica_home


def chunk_text(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into ~`size`-char chunks on paragraph/sentence boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    # Prefer paragraph boundaries; fall back to a sliding window.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= size:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= size:
                buf = p
            else:
                # hard-wrap an oversized paragraph with overlap
                step = max(1, size - overlap)
                for i in range(0, len(p), step):
                    chunks.append(p[i:i + size])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _rag_dir() -> Path:
    return get_animica_home() / "rag"


class RagStore:
    """A named on-disk vector store: {model, items:[{text, source, vector}]}."""

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self.path = _rag_dir() / f"{name}.json"
        self.model: Optional[str] = None
        self.items: list[dict] = []

    def load(self) -> "RagStore":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.model = data.get("model")
            self.items = list(data.get("items") or [])
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            self.model, self.items = None, []
        return self

    def save(self) -> Path:
        ensure_file_dir(self.path)
        self.path.write_text(json.dumps({"model": self.model, "items": self.items}),
                             encoding="utf-8")
        return self.path

    def add(self, text: str, source: str, vector: list[float]) -> None:
        self.items.append({"text": text, "source": source, "vector": vector})

    def search(self, query_vec: list[float], k: int = 4) -> list[dict]:
        scored = [
            {"score": cosine(query_vec, it.get("vector") or []),
             "text": it.get("text", ""), "source": it.get("source", "")}
            for it in self.items
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:max(1, k)]

    @staticmethod
    def list_stores() -> list[str]:
        d = _rag_dir()
        try:
            return sorted(p.stem for p in d.glob("*.json"))
        except OSError:
            return []
