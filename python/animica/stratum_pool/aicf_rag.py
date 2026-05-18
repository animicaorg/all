"""Tiny retrieval-augmented-generation layer for AICF miners.

Loads a pre-built embedding index over Animica documentation, retrieves
the top-k relevant chunks per query, and returns them so the inference
engine can prepend them to its system prompt. With this, off-the-shelf
small models stop hallucinating Animica-specific facts and answer
grounded in the actual docs.

Index files (shipped under ``animica/_data/aicf_rag/``):

- ``index.npy``    — ``(N, D)`` float32 matrix of L2-normalized chunk
                     embeddings, computed with the encoder named in
                     ``meta.json``.
- ``chunks.json``  — list of ``{"text": str, "source": str}`` aligned
                     with the rows of ``index.npy``.
- ``meta.json``    — ``{"encoder": str, "dim": int, "built_at": str,
                     "n_chunks": int}``.

Build the index with ``tools/build_aicf_rag_index.py`` (one-shot,
re-run when the docs change). The runtime never builds — it only
loads + queries.

Override the encoder model with ``ANIMICA_AICF_RAG_ENCODER`` (must
match what produced ``index.npy``). Disable retrieval entirely with
``ANIMICA_AICF_RAG_DISABLE=1``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

log = logging.getLogger("animica.stratum_pool.aicf_rag")

_DEFAULT_ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
_INDEX_DIR = Path(__file__).resolve().parent / "_data" / "aicf_rag"


@dataclass
class Retrieved:
    text: str
    source: str
    score: float


def _disabled() -> bool:
    return os.environ.get("ANIMICA_AICF_RAG_DISABLE", "").strip() not in ("", "0", "false", "no")


class _RagIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: Optional[List[dict]] = None
        self._matrix: Any = None  # np.ndarray once loaded
        self._encoder: Any = None
        self._encoder_name: str = ""
        self._data_attempted = False
        self._encoder_attempted = False

    def _try_load_data(self) -> bool:
        if self._data_attempted:
            return self._chunks is not None
        self._data_attempted = True
        index_path = _INDEX_DIR / "index.npy"
        chunks_path = _INDEX_DIR / "chunks.json"
        meta_path = _INDEX_DIR / "meta.json"
        if not (index_path.exists() and chunks_path.exists()):
            log.info("aicf-rag: no index bundled at %s; retrieval disabled", _INDEX_DIR)
            return False
        try:
            import numpy as np
            self._matrix = np.load(index_path)
            self._chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                self._encoder_name = str(meta.get("encoder") or _DEFAULT_ENCODER)
            else:
                self._encoder_name = _DEFAULT_ENCODER
            if len(self._chunks) != self._matrix.shape[0]:
                log.warning(
                    "aicf-rag: chunks/matrix length mismatch (%d vs %d); disabling",
                    len(self._chunks), self._matrix.shape[0],
                )
                self._chunks = None
                self._matrix = None
                return False
            log.info(
                "aicf-rag: index loaded — %d chunks, dim=%d, encoder=%s",
                len(self._chunks), int(self._matrix.shape[1]), self._encoder_name,
            )
            return True
        except Exception as exc:
            log.warning("aicf-rag: failed to load index: %s", exc)
            self._chunks = None
            self._matrix = None
            return False

    def _try_load_encoder(self) -> bool:
        if self._encoder is not None:
            return True
        if self._encoder_attempted:
            return False
        self._encoder_attempted = True
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:
            log.info(
                "aicf-rag: sentence-transformers not installed (%s); "
                "retrieval skipped — install animica[ml] to enable",
                exc,
            )
            return False
        encoder_name = os.environ.get("ANIMICA_AICF_RAG_ENCODER") or self._encoder_name or _DEFAULT_ENCODER
        try:
            self._encoder = SentenceTransformer(encoder_name)
            self._encoder_name = encoder_name
            return True
        except Exception as exc:
            log.warning("aicf-rag: encoder load failed for %s: %s", encoder_name, exc)
            return False

    def retrieve(self, query: str, *, top_k: int = 3, min_score: float = 0.20) -> List[Retrieved]:
        if _disabled() or not query:
            return []
        with self._lock:
            if not self._try_load_data():
                return []
            if not self._try_load_encoder():
                return []
            try:
                import numpy as np
                q = self._encoder.encode([query], normalize_embeddings=True)
                q_vec = np.asarray(q[0], dtype=self._matrix.dtype)
                sims = self._matrix @ q_vec
                top_k = max(1, int(top_k))
                if top_k >= sims.shape[0]:
                    order = np.argsort(-sims)
                else:
                    # argpartition is O(N); then sort just the top slice.
                    part = np.argpartition(-sims, top_k)[:top_k]
                    order = part[np.argsort(-sims[part])]
                hits: List[Retrieved] = []
                for i in order:
                    score = float(sims[i])
                    if score < min_score:
                        continue
                    ch = self._chunks[int(i)]
                    hits.append(Retrieved(
                        text=str(ch.get("text", "")),
                        source=str(ch.get("source", "")),
                        score=score,
                    ))
                return hits
            except Exception as exc:
                log.warning("aicf-rag: retrieval failed: %s", exc)
                return []


_GLOBAL_INDEX: Optional[_RagIndex] = None
_GLOBAL_LOCK = threading.Lock()


def get_index() -> _RagIndex:
    """Process-singleton retriever. Lazy so importing this module doesn't
    load 80MB of encoder weights until the first AICF job arrives."""
    global _GLOBAL_INDEX
    with _GLOBAL_LOCK:
        if _GLOBAL_INDEX is None:
            _GLOBAL_INDEX = _RagIndex()
        return _GLOBAL_INDEX


def retrieve_context(query: str, *, top_k: int = 3, max_chars: int = 2400) -> str:
    """Convenience: return a formatted context block ready to splice
    into a chat system prompt. Returns "" when retrieval is unavailable
    or no chunks pass the relevance floor.

    Caps total context length so we don't blow past the model's window.
    """
    hits = get_index().retrieve(query, top_k=top_k)
    if not hits:
        return ""
    parts: List[str] = []
    total = 0
    for h in hits:
        body = h.text.strip()
        if not body:
            continue
        # Trim long chunks so one verbose page doesn't crowd out others.
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "…"
        block = f"[source: {h.source}]\n{body}"
        if total + len(block) + 2 > max_chars:
            remaining = max_chars - total
            if remaining > 200:  # only include if meaningfully sized
                parts.append(block[:remaining].rstrip() + "…")
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


__all__ = ["Retrieved", "get_index", "retrieve_context"]
