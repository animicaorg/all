"""
animica.ena.agent
=================

Model-backed agent: a small plan / act / observe loop that grounds answers in
repository/document context via the retrieval layer and persists every step as
a trace. When the configured model provider is ``deterministic`` the loop
degrades to an extractive, offline answer so ``animica ena ask`` always returns
something auditable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import retrieval
from .models import new_uuid, now_ts


class Agent:
    def __init__(self, cfg, store) -> None:
        self.cfg = cfg
        self.store = store

    def _model(self, provider: Optional[str], model: Optional[str]):
        from .providers import build_model_adapter
        pcfg = self.cfg.model_provider(provider)
        if model:
            pcfg.model = model
        return build_model_adapter(pcfg)

    def _gather_context(self, context: Optional[str], query: str,
                        embedding_provider: Optional[str]) -> list[dict[str, Any]]:
        """Build an ephemeral in-memory index over ``context`` and retrieve."""
        if not context:
            return []
        from .providers import build_embedding_adapter
        ecfg = self.cfg.embedding_provider(embedding_provider)
        embedder = build_embedding_adapter(ecfg)
        tmp_index = "ctx-" + new_uuid()[:8]
        retrieval.build_index(self.store, [context], index_name=tmp_index,
                              embedder=embedder, embedding_provider=ecfg.provider,
                              embedding_model=ecfg.model)
        return retrieval.search(self.store, query, mode="hybrid", index=tmp_index,
                                embedder=embedder, limit=6)

    def _gather_from_index(self, query: str, embedding_provider: Optional[str],
                           index_name: str) -> list[dict[str, Any]]:
        """Retrieve from a PERSISTED index — the default grounding source when no
        explicit ``--context`` is passed, so ``ena ask`` answers from the indexed
        corpus instead of ungrounded (which made the base model hallucinate).
        Best-effort: returns [] if the index is absent or search fails, preserving
        the prior ungrounded behavior when nothing is indexed. Disable with
        ANIMICA_ENA_NO_DEFAULT_GROUNDING=1."""
        import os
        if os.environ.get("ANIMICA_ENA_NO_DEFAULT_GROUNDING", "").strip().lower() \
                in {"1", "true", "yes", "on"}:
            return []
        try:
            from .providers import build_embedding_adapter
            ecfg = self.cfg.embedding_provider(embedding_provider)
            embedder = build_embedding_adapter(ecfg)
            return retrieval.search(self.store, query, mode="hybrid",
                                    index=index_name, embedder=embedder, limit=6)
        except Exception:
            return []

    def ask(self, question: str, *, context: Optional[str] = None,
            model_provider: Optional[str] = None, model: Optional[str] = None,
            embedding_provider: Optional[str] = None) -> dict[str, Any]:
        hits = self._gather_context(context, question, embedding_provider)
        if not hits and not context:
            # No explicit context: ground on the default persisted index so a plain
            # `ena ask "..."` is answered from the indexed Animica corpus.
            import os
            default_index = os.environ.get("ANIMICA_ENA_DEFAULT_INDEX", "ena-docs")
            hits = self._gather_from_index(question, embedding_provider, default_index)
        context_block = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in hits)
        adapter = self._model(model_provider, model)
        prompt = question if not context_block else (
            f"Use the context to answer.\n\nContext:\n{context_block}\n\n"
            f"Question: {question}")
        answer = adapter.generate(prompt, system="You are the Animica ENA assistant.",
                                  max_tokens=self.cfg.model_provider(model_provider).max_tokens)
        trace = {"trace_id": "tr-" + new_uuid()[:16], "session_id": "ask",
                 "created_at": now_ts(),
                 "question": question, "sources": [h["source"] for h in hits],
                 "provider": adapter.cfg.provider}
        self.store.add_trace(trace)
        return {"answer": answer, "sources": [h["source"] for h in hits],
                "provider": adapter.cfg.provider, "model": adapter.cfg.model}

    def plan(self, goal: str, *, context: Optional[str] = None,
             model_provider: Optional[str] = None,
             model: Optional[str] = None) -> dict[str, Any]:
        adapter = self._model(model_provider, model)
        prompt = (f"Produce a concise, numbered, step-by-step plan to achieve this "
                  f"goal. Goal: {goal}")
        if context:
            prompt += f"\nWorking directory: {context}"
        text = adapter.generate(prompt, max_tokens=768)
        steps = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return {"goal": goal, "plan": text, "steps": steps,
                "provider": adapter.cfg.provider}

    def run_task(self, task_file: str, *, model_provider: Optional[str] = None,
                 model: Optional[str] = None) -> dict[str, Any]:
        p = Path(task_file)
        if not p.is_file():
            raise FileNotFoundError(f"task file not found: {p}")
        spec = p.read_text(encoding="utf-8")
        goal = spec
        try:
            if p.suffix.lower() in (".yaml", ".yml"):
                import yaml  # type: ignore
                data = yaml.safe_load(spec) or {}
                goal = data.get("goal") or data.get("task") or spec
            elif p.suffix.lower() == ".json":
                import json
                data = json.loads(spec)
                goal = data.get("goal") or data.get("task") or spec
        except Exception:  # noqa: BLE001
            goal = spec
        return self.ask(str(goal), context=str(p.parent),
                        model_provider=model_provider, model=model)
