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

import logging
from pathlib import Path
from typing import Any, Optional

from . import datasets as ds
from .models import now_ts

log = logging.getLogger("animica.ena.curriculum")

DEFAULT_ROWS_PER_ROUND = 16

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
        # Future backends ('retrieve' RAG self-instruct, 'scrape') land in later
        # increments; until then fall back to the safe deterministic template so
        # the flywheel still turns.
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
