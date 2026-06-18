"""
animica.ena.curriculum
======================

The Curriculum Flywheel — Increment 1.

A self-adapting training pool keeps studying its weakest topics by rotating the
NEXT round's dataset to freshly-generated data, instead of re-sharding the same
static file forever. This rides the auto-promote substrate: when a round is
promoted, :class:`~animica.ena.pool.PoolService` fires a best-effort hook
(``_maybe_rotate_dataset``) that asks this service for the next round's dataset
and records it in the pool's ``metadata`` — so the next round's trainers shard
fresh data and the model perpetually re-examines its current weakest spot.

Safety properties (Increment 1):

* **Opt-in** — only fires when ``pool.metadata['curriculum']['enabled']`` is true.
  The live seed pool is byte-for-byte unchanged until explicitly enabled.
* **Best-effort** — ``next_dataset`` never raises; a curriculum failure can never
  break a promotion or a trainer's submit.
* **Deterministic / GPU-free** — the default ``synthetic`` backend expands topics
  into template Q/A rows offline, so the same inputs yield the same sha256
  (replayable, no torch, no network).

Per-sample eval-driven discovery, self-tasking objectives, RAG self-instruct,
hard-example mining and tool-use rows land in later increments; the data shapes
here leave room for them (``topic_match_rate`` in ``last_eval``, ``source`` on
each round dataset, the ``round_datasets`` audit map).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from . import datasets as ds
from .models import now_ts

log = logging.getLogger("animica.ena.curriculum")

DEFAULT_ROWS_PER_ROUND = 16


def _tokenset(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


def evaluate_detailed(generate, eval_rows: list[dict], topics: list[str], *,
                      max_rows: int = 100) -> dict[str, Any]:
    """Run ``generate(prompt) -> str`` over the eval rows, loose-match each
    against its gold answer, and attribute every row to the seed topics it
    overlaps. Returns ``{match_rate, topic_match_rate, failures, evaluated}``.

    Pure w.r.t. the model — ``generate`` is injected, so this is GPU-free
    testable. The trainer wires in a real checkpoint runner; tests pass a stub.
    """
    topic_tokens = {t: _tokenset(t) for t in (topics or [])}
    per: dict[str, list[int]] = {t: [0, 0] for t in topic_tokens}  # [matched, total]
    total = matched = 0
    failures: list[dict] = []
    for r in (eval_rows or [])[:max_rows]:
        prompt = str(r.get("prompt") or r.get("text") or "")
        if not prompt:
            continue
        total += 1
        try:
            out = generate(prompt) or ""
        except Exception:  # noqa: BLE001
            out = ""
        gold = str(r.get("response") or r.get("chosen") or "")
        ok = bool(gold and gold.strip().lower()[:40] in out.strip().lower())
        if ok:
            matched += 1
        elif len(failures) < 50:
            failures.append({"prompt": prompt[:300], "gold": gold[:300],
                             "generated": out[:300]})
        row_tokens = _tokenset(prompt + " " + gold)
        for t, tt in topic_tokens.items():
            if tt & row_tokens:
                per[t][1] += 1
                if ok:
                    per[t][0] += 1
    topic_rate = {t: round(m / n, 4) for t, (m, n) in per.items() if n}
    return {"match_rate": round(matched / total, 4) if total else None,
            "topic_match_rate": topic_rate, "failures": failures,
            "evaluated": total}

# Deterministic Q/A templates. Offline + byte-stable: same topics -> same rows.
_TEMPLATES = [
    ("Explain {t}.",
     "{t}: a concept in the Animica knowledge curriculum. The key idea behind "
     "{t} is how it works and why it matters in practice."),
    ("What is {t}?",
     "{t} is studied here because the model previously struggled with it. "
     "Define {t} and describe where it applies."),
    ("Give a worked example involving {t}.",
     "Example for {t}: consider a case where {t} applies; the correct approach "
     "is to reason about {t} step by step."),
    ("Why does {t} matter?",
     "{t} matters because it underpins correct behaviour; ignoring {t} leads to "
     "mistakes the model should learn to avoid."),
]


class CurriculumService:
    """Produces the next round's fresh dataset for self-adapting pools."""

    def __init__(self, cfg, store, jobs=None, agent=None) -> None:
        self.cfg = cfg
        self.store = store
        self.jobs = jobs
        self.agent = agent

    # -- public -----------------------------------------------------------
    def next_dataset(self, pool: dict[str, Any], next_round: int,
                     last_eval: Optional[dict[str, Any]] = None
                     ) -> Optional[dict[str, Any]]:
        """Generate + curate the dataset for ``next_round``.

        Returns ``{path, sha256, topics, rows, prev_eval_score, source,
        created_at}`` or ``None`` on any failure. Never raises.
        """
        try:
            cfg = (pool.get("metadata") or {}).get("curriculum") or {}
            topics = self._discover_topics(pool, last_eval)
            if not topics:
                return None
            source = str(cfg.get("source") or "synthetic")
            rows_per_round = int(cfg.get("rows_per_round") or DEFAULT_ROWS_PER_ROUND)
            rows = self._generate_rows(topics, source=source,
                                       rows_per_round=rows_per_round, pool=pool)
            if not rows:
                return None
            curated = self._curate(pool, next_round, rows)
            if curated is None:
                return None
            path, sha = curated
            return {
                "path": str(path), "sha256": sha, "topics": topics,
                "rows": ds.row_count(path),
                "prev_eval_score": (last_eval or {}).get("match_rate"),
                "source": source, "created_at": now_ts(),
            }
        except Exception as exc:  # noqa: BLE001 - curriculum is best-effort
            log.warning("[curriculum] next_dataset failed for %s: %s",
                        pool.get("pool_id"), exc)
            return None

    # -- topic discovery (Increment 1: deterministic, seed-ranked) --------
    def _discover_topics(self, pool: dict[str, Any],
                         last_eval: Optional[dict[str, Any]]) -> list[str]:
        meta = pool.get("metadata") or {}
        cfg = meta.get("curriculum") or {}
        # de-dup the seed list, preserving first-seen order
        seen: set[str] = set()
        ordered: list[str] = []
        for raw in (cfg.get("topics_seed") or []):
            t = str(raw).strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                ordered.append(t)
        if not ordered:
            return []
        # topics already covered by a prior round dataset are "seen"
        studied: set[str] = set()
        for rd in (meta.get("round_datasets") or {}).values():
            for t in (rd or {}).get("topics") or []:
                studied.add(str(t).lower())
        # rank: lowest prior per-topic match_rate first (Increment 2 fills real
        # rates), then not-yet-studied before studied, then stable seed order.
        rates = (last_eval or {}).get("topic_match_rate") or {}
        index = {t: i for i, t in enumerate(ordered)}
        return sorted(ordered, key=lambda t: (
            float(rates.get(t, 1.0)),
            1 if t.lower() in studied else 0,
            index[t],
        ))

    # -- generation backends ----------------------------------------------
    def _generate_rows(self, topics: list[str], *, source: str,
                       rows_per_round: int, pool: dict[str, Any]) -> list[dict]:
        if source == "retrieve":
            return self._generate_retrieve(topics, pool, rows_per_round)
        # 'synthetic' (default) and any unknown/unsupported backend fall back to
        # the safe deterministic template so the flywheel still turns.
        return self._generate_synthetic(topics, rows_per_round)

    @staticmethod
    def _generate_synthetic(topics: list[str], rows_per_round: int) -> list[dict]:
        """Deterministic template Q/A — offline, GPU-free, byte-stable."""
        rows: list[dict] = []
        i = 0
        cap = max(1, rows_per_round) * len(_TEMPLATES) * max(1, len(topics))
        while len(rows) < rows_per_round and i < cap:
            t = topics[i % len(topics)]
            q, a = _TEMPLATES[(i // len(topics)) % len(_TEMPLATES)]
            rows.append({"prompt": q.format(t=t), "response": a.format(t=t),
                         "topic": t})
            i += 1
        return rows[:rows_per_round]

    # -- curation (normalize -> dedupe -> validate) -----------------------
    def _curate(self, pool: dict[str, Any], next_round: int,
                rows: list[dict]) -> Optional[tuple[Path, str]]:
        pool_id = pool["pool_id"]
        cdir = Path(self.cfg.artifacts_dir()) / "pools" / pool_id / "curriculum"
        cdir.mkdir(parents=True, exist_ok=True)
        raw = cdir / f"round-{next_round}-raw.jsonl"
        norm = cdir / f"round-{next_round}-norm.jsonl"
        out = cdir / f"round-{next_round}.jsonl"
        ds.write_jsonl(raw, rows)
        ds.normalize(raw, norm)
        ds.dedupe(norm, out)
        report = ds.validate(out)
        if not report.get("valid", True) or ds.row_count(out) < 1:
            return None
        return out, ds.sha256_file(out)

    # -- RAG-grounded generation ('retrieve' backend) ---------------------
    @staticmethod
    def _tokens(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", str(s).lower()))

    def _load_corpus(self, pool: dict[str, Any], cap: int = 600) -> list[dict]:
        """The pool's grounding corpus: its source dataset + everything it has
        studied so far (accumulated round datasets)."""
        rows: list[dict] = []
        for p in [pool.get("dataset_path")] + [
                (rd or {}).get("path") for rd in
                ((pool.get("metadata") or {}).get("round_datasets") or {}).values()]:
            try:
                if p and Path(p).is_file():
                    rows.extend(ds.read_jsonl(p))
            except Exception:  # noqa: BLE001
                continue
        return rows[:cap]

    def _retrieve(self, corpus: list[dict], topic: str, k: int = 3) -> list[dict]:
        """Token-overlap retrieval — deterministic, GPU-free, no index needed."""
        qt = self._tokens(topic)
        if not qt:
            return corpus[:k]
        scored = []
        for i, row in enumerate(corpus):
            text = f"{row.get('prompt','')} {row.get('response') or row.get('text') or ''}"
            overlap = len(qt & self._tokens(text))
            if overlap:
                scored.append((-overlap, i, row))
        scored.sort(key=lambda x: (x[0], x[1]))  # most overlap first, stable
        return [r for _, _, r in scored[:k]]

    def _model_adapter(self, pool: dict[str, Any]):
        try:
            from .providers import build_model_adapter
            prov = ((pool.get("metadata") or {}).get("curriculum") or {}
                    ).get("model_provider")
            return build_model_adapter(self.cfg.model_provider(prov))
        except Exception:  # noqa: BLE001 - no provider configured / unreachable
            return None

    def _self_instruct(self, adapter, topic: str, hits: list[dict]) -> Optional[dict]:
        """Ask the model for ONE fresh Q/A grounded in the retrieved context.
        Returns None on any failure (caller uses the grounded fallback)."""
        ctx = "\n".join(
            f"- {str(h.get('prompt') or '')}: "
            f"{str(h.get('response') or h.get('text') or '')}" for h in hits)[:2000]
        if not ctx.strip():
            return None
        prompt = (
            f"Using ONLY the context below, write ONE new question about "
            f"'{topic}' and its correct, concise answer. Reply as strict JSON "
            f'{{"prompt": "...", "response": "..."}} and nothing else.\n\n'
            f"Context:\n{ctx}")
        try:
            out = adapter.generate(prompt, max_tokens=300, temperature=0.2)
            obj = json.loads(out[out.find("{"): out.rfind("}") + 1])
            p, r = str(obj.get("prompt", "")).strip(), str(obj.get("response", "")).strip()
            # Reject junk: empty, the literal "..." placeholders a weak model
            # echoes from the instruction, too-short answers, or an echo of the
            # context block. On any of these we fall back to the grounded row.
            if (not p or not r or "..." in p or "..." in r
                    or len(r) < 20 or r in ctx or "Using ONLY the context" in r):
                return None
            return {"prompt": p, "response": r}
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _grounded_row(topic: str, hits: list[dict]) -> Optional[dict]:
        """A training row whose ANSWER is real corpus content relevant to the
        weak topic (grounded, not template filler). Deterministic."""
        ctx = " ".join(str(h.get("response") or h.get("text") or "").strip()
                       for h in hits).strip()
        if not ctx:
            return None
        return {"prompt": f"What should I know about {topic}?",
                "response": ctx[:1200]}

    def _generate_retrieve(self, topics: list[str], pool: dict[str, Any],
                           rows_per_round: int) -> list[dict]:
        corpus = self._load_corpus(pool)
        if not corpus:  # nothing to ground on — fall back to the safe template
            return self._generate_synthetic(topics, rows_per_round)
        adapter = self._model_adapter(pool)  # may be None / a stub
        rows: list[dict] = []
        i = 0
        cap = max(1, rows_per_round) * 4
        while len(rows) < rows_per_round and i < cap:
            topic = topics[i % len(topics)]
            hits = self._retrieve(corpus, topic, k=3)
            row = self._self_instruct(adapter, topic, hits) if adapter else None
            if row is None:
                row = self._grounded_row(topic, hits)
            if row:
                rows.append({**row, "topic": topic})
            i += 1
        # If grounding produced nothing usable, never stall the flywheel.
        return rows[:rows_per_round] or self._generate_synthetic(topics, rows_per_round)

    # -- candidate evaluation (drives the eval gate) ----------------------
    def _eval_rows(self, pool: dict[str, Any]) -> list[dict]:
        """A held-out eval split of the pool's source corpus. For curriculum
        rounds (which train on generated data) this is genuinely held out;
        created once and recorded in metadata['eval_split']."""
        meta = pool.get("metadata") or {}
        es = (meta.get("eval_split") or {}).get("path")
        if es and Path(es).is_file():
            return list(ds.read_jsonl(es))
        dp = pool.get("dataset_path")
        if not (dp and Path(dp).is_file()):
            return []
        out_dir = Path(self.cfg.artifacts_dir()) / "pools" / pool["pool_id"] / "eval"
        try:
            res = ds.split(dp, out_dir, ratios=(0.85, 0.15, 0.0))
            ep = res["eval"]["path"]
            meta2 = dict(pool.get("metadata") or {})
            meta2["eval_split"] = {"path": ep, "sha256": res["eval"]["sha256"],
                                   "rows": res["eval"]["row_count"]}
            pool["metadata"] = meta2
            self.store.upsert_pool(pool)
            return list(ds.read_jsonl(ep))
        except Exception:  # noqa: BLE001
            return []

    def evaluate_candidate(self, pool: dict[str, Any],
                           candidate: dict[str, Any]) -> Optional[float]:
        """Loose-match accuracy of a just-merged candidate checkpoint on the
        held-out eval split, in [0, 1]. Returns None on any failure (best-effort;
        the gate then treats the round as unevaluated)."""
        # Running the candidate means loading the base model — only do it where
        # that's cheap (a GPU host). Off by default so an eval-gated pool on a
        # GPU-less coordinator fails OPEN (promotes) rather than blocking each
        # submit on a multi-minute CPU model load. GPU deployments set
        # ANIMICA_ENA_CURRICULUM_EVAL=1 (or point eval at a GPU worker later).
        import os
        if os.environ.get("ANIMICA_ENA_CURRICULUM_EVAL", "").lower() not in (
                "1", "true", "yes", "on"):
            return None
        try:
            rows = self._eval_rows(pool)
            if not rows:
                return None
            from .serving import PoolModelRunner
            runner = PoolModelRunner(pool.get("base_model", ""),
                                     adapter_path=candidate.get("path"))
            total = matched = 0
            for r in rows[:100]:
                prompt = str(r.get("prompt") or r.get("text") or "")
                if not prompt:
                    continue
                total += 1
                try:
                    out = runner.generate(prompt, max_tokens=128)
                except Exception:  # noqa: BLE001
                    out = ""
                gold = str(r.get("response") or r.get("chosen") or "")
                if gold and gold.strip().lower()[:40] in out.strip().lower():
                    matched += 1
            return round(matched / total, 4) if total else None
        except Exception as exc:  # noqa: BLE001
            log.warning("[curriculum] evaluate_candidate failed: %s", exc)
            return None

    def evaluate_checkpoint_detailed(self, base_model: str, checkpoint_path: str,
                                     eval_rows: list[dict],
                                     topics: list[str]) -> dict[str, Any]:
        """Trainer-side eval of a freshly-trained checkpoint: overall + per-topic
        match rate over the shared eval rows. Runs the model (the trainer has a
        GPU), so it is NOT env-gated. Best-effort: returns {} on any failure."""
        try:
            from .serving import PoolModelRunner
            runner = PoolModelRunner(base_model or "", adapter_path=checkpoint_path)
            return evaluate_detailed(
                lambda p: runner.generate(p, max_tokens=128), eval_rows, topics)
        except Exception as exc:  # noqa: BLE001
            log.warning("[curriculum] evaluate_checkpoint_detailed failed: %s", exc)
            return {}
