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
from .models import now_ts, sha3_hex

log = logging.getLogger("animica.ena.curriculum")

DEFAULT_ROWS_PER_ROUND = 32
# Rows-per-round scales with the active miners so there is always enough data to
# shard (one shard per miner needs >= one row per shard). Each round targets
# DEFAULT_ROWS_PER_SHARD rows per active miner, floored at the configured
# rows_per_round and capped at DEFAULT_MAX_ROWS_PER_ROUND so generation cost stays
# bounded. Override per-pool with curriculum.rows_per_shard / max_rows_per_round.
DEFAULT_ROWS_PER_SHARD = 8
DEFAULT_MAX_ROWS_PER_ROUND = 512
# Mastery ledger: a topic is "mastered" after MASTERY_ROUNDS consecutive rounds
# at/above MASTERY_THRESHOLD, and demoted back to study if it drops below
# DEMOTE_THRESHOLD. Mastered topics are skipped so the pool doesn't re-grind
# what it already knows (prevents topic thrash).
MASTERY_THRESHOLD = 0.8
MASTERY_ROUNDS = 2
DEMOTE_THRESHOLD = 0.5
# Fraction of each round's dataset drawn from prior material (replay buffer) to
# guard against catastrophic forgetting.
DEFAULT_REPLAY_RATIO = 0.25

# Tool-use curriculum (increment 6): teach the model to emit the agent_runtime
# [TOOL_CALL] block for EXISTING tools. These reference real tool names + arg
# schemas (agent_runtime.agentic registry). No dynamic tool *registration* and
# no execution here — rows only teach the call format, eval only parses it.
_DEFAULT_TOOLS = [
    {"name": "read_file", "arguments": {"path": "src/config.py"},
     "prompt": "Show me what's in src/config.py.",
     "rationale": "I'll read the file to inspect it."},
    {"name": "list_files", "arguments": {"path": "."},
     "prompt": "What files are in this directory?",
     "rationale": "Listing the directory."},
    {"name": "grep", "arguments": {"pattern": "TODO", "path": "."},
     "prompt": "Find every TODO in the project.",
     "rationale": "Searching the codebase for TODO."},
    {"name": "edit_file",
     "arguments": {"path": "app.py", "old": "port = 8000", "new": "port = 9000"},
     "prompt": "In app.py, change the port from 8000 to 9000.",
     "rationale": "Editing app.py to change the port."},
    {"name": "write_file",
     "arguments": {"path": "README.md", "content": "# Hello\n"},
     "prompt": "Create a README.md that says '# Hello'.",
     "rationale": "Writing the new README.md."},
    {"name": "bash", "arguments": {"command": "pytest -q"},
     "prompt": "Run the test suite.",
     "rationale": "Running the tests via the shell."},
    {"name": "animica_rpc", "arguments": {"method": "chain.getHead"},
     "prompt": "What's the current chain head?",
     "rationale": "Querying the node RPC for the chain head."},
    {"name": "balance", "arguments": {"address": "anim1example"},
     "prompt": "What's the balance of anim1example?",
     "rationale": "Looking up the address balance."},
    {"name": "done", "arguments": {"message": "Task complete."},
     "prompt": "You've finished the task — wrap up.",
     "rationale": "Signalling completion."},
]
# Known tool names — a model output that calls one of these (valid block) counts
# as tool-format mastery even if our example args differ.
_KNOWN_TOOL_NAMES = {t["name"] for t in _DEFAULT_TOOLS} | {
    "glob", "tree", "file_stat", "diff_files", "search_and_replace",
    "append_file", "mkdir", "delete_file", "move_file", "apply_patch",
    "python_eval", "fetch_url", "chain_head"}


def _parse_tool_call(text: str) -> Optional[tuple[str, dict]]:
    """Return (name, arguments) of the first [TOOL_CALL] block, or None. Prefers
    the real agent_runtime parser; falls back to a regex so eval works even where
    agent_runtime isn't importable."""
    try:
        from agent_runtime.agentic import parse_tool_call as _p  # type: ignore
        pc = _p(text or "")
        return (pc.name, pc.arguments) if pc else None
    except Exception:  # noqa: BLE001
        m = re.search(r"\[TOOL_CALL\].*?(\{.*?\}).*?\[/TOOL_CALL\]",
                      text or "", re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(1))
            name = str(obj.get("name") or "").strip()
            args = obj.get("arguments") or {}
            return (name, args) if name and isinstance(args, dict) else None
        except Exception:  # noqa: BLE001
            return None


def _tokenset(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(s).lower()))


# Function words / short tokens carry no topical signal; scoring on them would let
# a generic or empty answer "pass". Loose-match on the gold answer's *content*
# tokens only.
_EVAL_STOPWORDS = frozenset(
    "a an the and or but if then else of to in on at for with without from by as is "
    "are was were be been being it its this that these those you your we our they their "
    "i me my he she his her him do does did done can could should would may might will "
    "shall must not no yes so such than too very into out up down over under again more "
    "most some any all each every here there what which who whom how why when where "
    "have has had having get gets got use used using via per".split()
)


def _content_tokens(s: str) -> set[str]:
    return {t for t in _tokenset(s) if len(t) >= 3 and t not in _EVAL_STOPWORDS}


def loose_hit(gold: str, out: str, *, min_recall: float = 0.5) -> bool:
    """Loose semantic match for a *paraphrasing* LLM.

    A generation counts as a hit when it either reproduces the verbatim opening of
    the gold answer OR recalls at least ``min_recall`` of the gold answer's
    distinctive content tokens.

    The original gate used ONLY the "first-40-chars verbatim substring" test, which
    is unsatisfiable for a model that paraphrases (it essentially never reproduces
    40 exact characters), so ``match_rate`` was pinned at 0.0 forever and the pool's
    eval gate could never promote a checkpoint. This is strictly more lenient — the
    old verbatim path is kept as a fast-path hit.
    """
    gold = (gold or "").strip()
    out = (out or "").strip()
    if not gold:
        return False
    if gold.lower()[:40] in out.lower():
        return True
    gt = _content_tokens(gold)
    if not gt:
        # No contentful tokens in gold (e.g. a tiny answer) — fall back to a short
        # verbatim-prefix check rather than crediting an empty overlap.
        return gold.lower()[:24] in out.lower()
    overlap = len(gt & _tokenset(out)) / len(gt)
    return overlap >= min_recall


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
        want = _parse_tool_call(gold) if "[TOOL_CALL]" in gold else None
        if want is not None:
            # Tool-mastery: the output must emit a VALID tool call. Credit it if
            # it names the expected tool (or any known tool) — we score format
            # mastery, not exact arguments.
            got = _parse_tool_call(out)
            ok = bool(got and got[0] and (
                got[0] == want[0] or got[0] in _KNOWN_TOOL_NAMES))
        else:
            ok = loose_hit(gold, out)
        if ok:
            matched += 1
        elif len(failures) < 50:
            failures.append({"prompt": prompt[:300], "gold": gold[:300],
                             "generated": out[:300]})
        # Attribution: a tool row counts ONLY toward its exact tool:<name> topic
        # (the shared "tool" token would otherwise smear it across all tools);
        # a knowledge row counts toward overlapping knowledge topics only.
        if want is not None:
            tt = f"tool:{want[0]}"
            if tt in per:
                per[tt][1] += 1
                if ok:
                    per[tt][0] += 1
        else:
            row_tokens = _tokenset(prompt + " " + gold)
            for t, toks in topic_tokens.items():
                if t.startswith("tool:"):
                    continue
                if toks & row_tokens:
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

    def __init__(self, cfg, store, jobs=None, agent=None, tools=None) -> None:
        self.cfg = cfg
        self.store = store
        self.jobs = jobs
        self.agent = agent
        self.tools = tools  # DynamicToolRegistry — approved tools become teachable

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
            # Ingest the just-finished round's per-topic scores into the mastery
            # ledger + self-tasking objectives (round completed = next_round - 1).
            self._ingest_eval(pool, last_eval, next_round - 1)
            topics = self._discover_topics(pool, last_eval)
            if not topics:
                return None
            source = str(cfg.get("source") or "synthetic")
            rows_per_round = self._rows_per_round(cfg, pool)
            replay_ratio = float(cfg.get("replay_ratio", DEFAULT_REPLAY_RATIO))
            tool_ratio = float(cfg.get("tool_ratio", 0.0))
            tool_specs = self._resolve_tools(cfg)
            n_replay = max(0, int(rows_per_round * replay_ratio))
            n_tool = max(0, int(rows_per_round * tool_ratio)) if tool_specs else 0
            n_fresh = max(1, rows_per_round - n_replay - n_tool)
            fresh = self._generate_rows(topics, source=source,
                                        rows_per_round=n_fresh, pool=pool)
            # Tool-use rows teach the [TOOL_CALL] format; replay buffer is a
            # deterministic sample of prior material to prevent forgetting.
            rows = (list(fresh)
                    + self._generate_tool_rows(tool_specs, n_tool)
                    + self._replay_sample(pool, n_replay))
            if not rows:
                return None
            curated = self._curate(pool, next_round, rows)
            if curated is None:
                return None
            path, sha = curated
            return {
                "path": str(path), "sha256": sha, "topics": topics,
                "rows": ds.row_count(path), "replay": n_replay,
                "prev_eval_score": (last_eval or {}).get("match_rate"),
                "source": source, "created_at": now_ts(),
            }
        except Exception as exc:  # noqa: BLE001 - curriculum is best-effort
            log.warning("[curriculum] next_dataset failed for %s: %s",
                        pool.get("pool_id"), exc)
            return None

    @staticmethod
    def _active_miner_count(pool: dict[str, Any]) -> int:
        """Active miners for this pool (recent heartbeats/claims), used to scale
        rows-per-round. Mirrors PoolService active-worker accounting."""
        meta = pool.get("metadata") or {}
        window = int(meta.get("active_window_secs", 1800))
        now = now_ts()
        return sum(1 for t in (meta.get("active_workers") or {}).values()
                   if now - int(t) <= window)

    def _rows_per_round(self, cfg: dict[str, Any], pool: dict[str, Any]) -> int:
        """Rows to generate for the next round, scaled to the active miners so
        there is always enough data to give each one a (small) shard. Targets
        ``rows_per_shard`` rows per active miner, floored at the configured
        ``rows_per_round`` and capped at ``max_rows_per_round`` so per-round
        generation cost stays bounded. With no active miners it is the floor, so
        idle pools don't over-generate."""
        base_rows = int(cfg.get("rows_per_round") or DEFAULT_ROWS_PER_ROUND)
        rows_per_shard = max(1, int(cfg.get("rows_per_shard")
                                    or DEFAULT_ROWS_PER_SHARD))
        max_rows = max(base_rows, int(cfg.get("max_rows_per_round")
                                      or DEFAULT_MAX_ROWS_PER_ROUND))
        active = self._active_miner_count(pool)
        return max(1, min(max_rows, max(base_rows, active * rows_per_shard)))

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
        stats = meta.get("topic_stats") or {}
        rates = (last_eval or {}).get("topic_match_rate") or {}

        def rate_of(t: str) -> float:
            if t in rates:
                return float(rates[t])
            st = stats.get(t)
            return float(st.get("last_rate", 1.0)) if st else 1.0

        # Skip mastered topics so the pool studies its actual weak spots — unless
        # everything is mastered, in which case revisit all (never go idle).
        unmastered = [t for t in ordered
                      if not (stats.get(t) or {}).get("mastered")]
        pool_topics = unmastered or ordered
        # coverage-gap: how many prior rounds already studied each topic (fewer =
        # under-covered → higher priority).
        studied_count: dict[str, int] = {}
        for rd in (meta.get("round_datasets") or {}).values():
            for t in (rd or {}).get("topics") or []:
                studied_count[t] = studied_count.get(t, 0) + 1
        index = {t: i for i, t in enumerate(ordered)}
        # rank: weakest first, then least-studied (coverage gap), then seed order
        return sorted(pool_topics, key=lambda t: (
            round(rate_of(t), 4), studied_count.get(t, 0), index[t]))

    # -- mastery ledger + self-tasking objectives + replay ----------------
    def _ingest_eval(self, pool: dict[str, Any],
                     last_eval: Optional[dict[str, Any]], rnd: int) -> None:
        """Fold the just-finished round's per-topic scores into the mastery
        ledger (``metadata['topic_stats']``) and the self-tasking objectives
        ledger (the agent memory table). Idempotent per round. Never raises."""
        rates = (last_eval or {}).get("topic_match_rate") or {}
        if not rates:
            return
        try:
            meta = dict(pool.get("metadata") or {})
            stats = dict(meta.get("topic_stats") or {})
            for topic, raw in rates.items():
                try:
                    rate = float(raw)
                except (TypeError, ValueError):
                    continue
                st = dict(stats.get(topic) or {})
                if st.get("updated_round") == rnd:
                    continue  # already ingested this round
                st["last_rate"] = round(rate, 4)
                st["best"] = round(max(float(st.get("best", 0.0)), rate), 4)
                if rate >= MASTERY_THRESHOLD:
                    st["rounds_above"] = int(st.get("rounds_above", 0)) + 1
                    if st["rounds_above"] >= MASTERY_ROUNDS:
                        st["mastered"] = True
                else:
                    st["rounds_above"] = 0
                    if rate < DEMOTE_THRESHOLD:
                        st["mastered"] = False
                st.setdefault("mastered", False)
                st["updated_round"] = rnd
                stats[topic] = st
                self._upsert_objective(pool, topic, st, rnd)
            meta["topic_stats"] = stats
            pool["metadata"] = meta
        except Exception as exc:  # noqa: BLE001 - ledger update is best-effort
            log.warning("[curriculum] ingest_eval failed: %s", exc)

    def _upsert_objective(self, pool: dict[str, Any], topic: str,
                          st: dict[str, Any], rnd: int) -> None:
        """Record a per-topic learning objective in the agent memory ledger
        (INSERT-OR-REPLACE by a stable id, so it de-dups + tracks status). Lets
        the served model see what it has tasked itself to learn."""
        try:
            pid = pool["pool_id"]
            oid = "obj-" + sha3_hex(f"{pid}|{topic}")[:16]
            mastered = bool(st.get("mastered"))
            rate = float(st.get("last_rate", 0.0))
            status = "mastered" if mastered else "open"
            text = (f"[curriculum objective] pool={pid} topic={topic!r} "
                    f"status={status} score={rate} — "
                    + ("mastered." if mastered
                       else "study this; weak on held-out eval."))
            self.store.add_memory({
                "memory_id": oid, "text": text, "source": "curriculum.objective",
                "created_at": now_ts(),
                "metadata": {"pool_id": pid, "topic": topic, "kind": "knowledge",
                             "status": status, "score": rate,
                             "priority": round(1.0 - rate, 4),
                             "updated_round": rnd},
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("[curriculum] upsert_objective failed: %s", exc)

    def _replay_sample(self, pool: dict[str, Any], n: int) -> list[dict]:
        """A deterministic stride sample of prior material (past round datasets,
        else the source corpus) — mixed into each round to prevent forgetting."""
        if n <= 0:
            return []
        prior: list[dict] = []
        for rd in ((pool.get("metadata") or {}).get("round_datasets") or {}).values():
            p = (rd or {}).get("path")
            try:
                if p and Path(p).is_file():
                    prior.extend(ds.read_jsonl(p))
            except Exception:  # noqa: BLE001
                continue
        if not prior:
            dp = pool.get("dataset_path")
            try:
                if dp and Path(dp).is_file():
                    prior = list(ds.read_jsonl(dp))
            except Exception:  # noqa: BLE001
                prior = []
        if not prior:
            return []
        step = max(1, len(prior) // n)
        return [prior[i] for i in range(0, len(prior), step)][:n]

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

    # -- tool-use curriculum (increment 6) --------------------------------
    @staticmethod
    def _tool_teach_spec(t: dict) -> dict:
        """Turn an approved dynamic tool (name + params schema) into a teaching
        spec with placeholder example arguments."""
        params = t.get("parameters") if isinstance(t.get("parameters"), dict) else {}
        args = {k: "example" for k in params}
        return {"name": t["name"], "arguments": args,
                "prompt": (t.get("description") or f"Use the {t['name']} tool."),
                "rationale": f"I'll use the {t['name']} tool."}

    def _resolve_tools(self, cfg: dict) -> list[dict]:
        """Tool specs to teach: explicit ``curriculum['tools']`` else the built-in
        defaults when ``teach_tools`` is on; plus any human-approved dynamic
        tools. Returns none unless tool teaching is enabled on the pool."""
        if not (cfg.get("tools") or cfg.get("teach_tools")):
            return []
        if cfg.get("tools"):
            specs = [t for t in cfg["tools"] if isinstance(t, dict) and t.get("name")]
        else:
            specs = list(_DEFAULT_TOOLS)
        if self.tools is not None:
            try:
                specs = specs + [self._tool_teach_spec(t)
                                 for t in self.tools.approved_tools()]
            except Exception:  # noqa: BLE001
                pass
        return specs

    @staticmethod
    def _tool_row(spec: dict) -> dict:
        """An SFT row teaching the [TOOL_CALL] block for one tool. Deterministic."""
        name = str(spec.get("name") or "")
        args = spec.get("arguments") or {}
        rationale = spec.get("rationale") or f"I'll use the {name} tool."
        call = json.dumps({"name": name, "arguments": args})
        response = f"{rationale}\n[TOOL_CALL]\n{call}\n[/TOOL_CALL]"
        return {"prompt": spec.get("prompt") or f"Use the {name} tool.",
                "response": response, "topic": f"tool:{name}"}

    def _generate_tool_rows(self, tool_specs: list[dict], n: int) -> list[dict]:
        if not tool_specs or n <= 0:
            return []
        return [self._tool_row(tool_specs[i % len(tool_specs)]) for i in range(n)]

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
        """The held-out eval rows: a split of the source corpus PLUS tool-use
        eval rows (so tool-call emission is scored, feeding tool-mastery into the
        same loop)."""
        rows = list(self._corpus_eval_rows(pool))
        tool_specs = self._resolve_tools(
            (pool.get("metadata") or {}).get("curriculum") or {})
        if tool_specs:
            rows = rows + self._generate_tool_rows(tool_specs, min(len(tool_specs), 8))
        return rows

    def _corpus_eval_rows(self, pool: dict[str, Any]) -> list[dict]:
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
                if loose_hit(gold, out):
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
