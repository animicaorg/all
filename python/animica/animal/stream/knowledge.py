"""Per-character knowledge base (RAG).

End users upload documents to give their character unique knowledge. Embedding +
vector search live server-side (the marketplace's pgvector store); this worker-side
helper just queries the character's collection over the localhost-only internal API and
returns the retrieved text for the brain to ground its replies on. Returns None when the
character has no knowledge base, so the brain simply runs without RAG.
"""
from __future__ import annotations

import json
import os
import urllib.request


def rag_for(char):
    ref = getattr(char, "knowledge_ref", "") or ""
    if not ref:
        return None
    mkt = os.environ.get("ANIMICA_MKT_URL", "http://127.0.0.1:4950").rstrip("/")
    token = os.environ.get("ANIMAL_INTERNAL_TOKEN", "")
    if not token:
        return None

    def _query(q: str) -> str:
        try:
            body = json.dumps({"ref": ref, "query": q, "k": 4}).encode()
            req = urllib.request.Request(
                mkt + "/api/mkt/v1/animal/internal/knowledge",
                data=body, headers={"authorization": f"Bearer {token}",
                                    "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read())
            chunks = d.get("chunks", []) or []
            return "\n".join(c.get("text", "") for c in chunks)[:1200]
        except Exception:
            return ""

    return _query
