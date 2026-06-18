"""Curriculum Flywheel (Increment 1) tests — GPU-free, deterministic.

Covers: deterministic dataset generation (replayable sha256), the full
promote→rotate→train-next-round loop (round 2 trains a NEW file), the regression
guard (a pool without curriculum is byte-for-byte unchanged), and the
best-effort hook (a curriculum failure never breaks promotion).
"""

from __future__ import annotations

import json

import pytest

from animica.ena import ENA
from animica.ena import datasets as ds
from animica.ena.config import load_config


def _write_dataset(path, n=20) -> str:
    rows = [{"prompt": f"orig-Q{i}", "response": f"orig-A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def _make_ena(home, monkeypatch) -> ENA:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(home))
    return ENA(cfg=load_config())


def _enable_curriculum(ena, pid, *, topics, rows_per_round=8):
    pool = ena.store.get_pool(pid)
    meta = dict(pool.get("metadata") or {})
    meta["curriculum"] = {"enabled": True, "source": "synthetic",
                          "topics_seed": topics, "rows_per_round": rows_per_round}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)


def _run_round(ena, pid):
    """Claim + submit every shard of the current round (auto-promote fires)."""
    while True:
        s = ena.pool.claim_shard(pid, "trainer")
        if s is None:
            break
        ena.pool.submit_shard(pid, s["shard_id"], worker_id="trainer",
                              metrics={"samples": 5})
        if ena.store.get_pool(pid)["round"] != s["round"]:
            break  # round advanced (promoted) — stop draining


def _round_total_rows(ena, pid, rnd) -> int:
    return sum(int(s.get("row_count") or 0)
               for s in ena.store.list_shards(pid, round=rnd))


def _set_active_workers(ena, pid, n):
    from animica.ena.models import now_ts
    pool = ena.store.get_pool(pid)
    meta = dict(pool.get("metadata") or {})
    meta["active_workers"] = {f"m{i}": now_ts() for i in range(n)}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)
    return ena.store.get_pool(pid)


def test_rows_per_round_scales_with_active_miners(tmp_path, monkeypatch):
    """rows_per_round scales to active miners (target rows_per_shard each),
    floored at the configured rows_per_round and capped at max_rows_per_round."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="rscale", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["a", "b"], rows_per_round=24)
    pool = ena.store.get_pool(pid)
    pool["metadata"]["curriculum"].update({"rows_per_shard": 8,
                                           "max_rows_per_round": 512})
    ena.store.upsert_pool(pool)
    c = (ena.store.get_pool(pid).get("metadata") or {})["curriculum"]

    # no active miners -> floor (configured rows_per_round)
    assert ena.curriculum._rows_per_round(c, ena.store.get_pool(pid)) == 24
    # 10 miners -> 10*8 = 80 (above floor, below cap)
    pool = _set_active_workers(ena, pid, 10)
    assert ena.curriculum._rows_per_round(c, pool) == 80
    # 100 miners -> 800 clamped to the 512 cap
    pool = _set_active_workers(ena, pid, 100)
    assert ena.curriculum._rows_per_round(c, pool) == 512
    # 1 miner -> 8 target, but floored at the configured 24
    pool = _set_active_workers(ena, pid, 1)
    assert ena.curriculum._rows_per_round(c, pool) == 24


def test_rows_per_round_floor_when_no_curriculum_rows_set(tmp_path, monkeypatch):
    """With no rows_per_round configured it uses DEFAULT_ROWS_PER_ROUND as floor."""
    from animica.ena.curriculum import DEFAULT_ROWS_PER_ROUND
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="rfloor", num_shards=2)
    pid = p["pool_id"]
    pool = ena.store.get_pool(pid)
    pool["metadata"] = {"curriculum": {"enabled": True}}
    ena.store.upsert_pool(pool)
    c = {"enabled": True}
    assert ena.curriculum._rows_per_round(c, ena.store.get_pool(pid)) == DEFAULT_ROWS_PER_ROUND


def test_next_dataset_is_deterministic(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="det", num_shards=2)
    _enable_curriculum(ena, p["pool_id"], topics=["alpha", "beta", "gamma", "delta"])
    pool = ena.store.get_pool(p["pool_id"])
    a = ena.curriculum.next_dataset(pool, 2, None)
    b = ena.curriculum.next_dataset(pool, 2, None)
    assert a and b
    assert a["sha256"] == b["sha256"], "generation must be replayable"
    assert a["rows"] >= 1 and a["topics"]
    # canonical sft shape, fresh content (not the original rows)
    first = next(ds.read_jsonl(a["path"]))
    assert "prompt" in first and "response" in first
    assert "orig-Q" not in first["prompt"]


def test_full_loop_rotates_next_round_to_fresh_data(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="loop", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["alpha", "beta", "gamma", "delta"],
                       rows_per_round=8)

    _run_round(ena, pid)  # round 1 completes → auto-promote → rotation hook

    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2, "round did not auto-promote"
    rd = (pool.get("metadata") or {}).get("round_datasets", {}).get("2")
    assert rd and rd.get("path") and rd.get("sha256"), "no round-2 dataset recorded"
    assert rd["sha256"] != ds.sha256_file(data), "round 2 reused the original data"
    assert rd["topics"], "round-2 topics not recorded"

    # round 2 trains the curriculum file, NOT the original dataset
    ena.pool.claim_shard(pid, "trainer")  # materialises round-2 shards
    r2_rows = _round_total_rows(ena, pid, 2)
    assert r2_rows == rd["rows"] == ds.row_count(rd["path"])
    assert r2_rows != 20, "round 2 sharded the original 20-row dataset, not fresh data"


def test_pool_without_curriculum_is_unchanged(tmp_path, monkeypatch):
    """Regression guard: the live seed pool path (no curriculum) is untouched —
    round 2 re-shards the original dataset and records no round_datasets."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="plain", num_shards=2)
    pid = p["pool_id"]

    _run_round(ena, pid)

    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2
    assert "round_datasets" not in (pool.get("metadata") or {})
    ena.pool.claim_shard(pid, "trainer")  # round-2 shards from the ORIGINAL file
    assert _round_total_rows(ena, pid, 2) == 20


def test_retrieve_backend_grounds_in_corpus(tmp_path, monkeypatch):
    """The 'retrieve' backend generates rows whose ANSWERS are real corpus
    content for the weak topic (grounded), not synthetic templates — and is
    deterministic."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    rows = ([{"prompt": f"About alpha #{i}",
              "response": f"alpha-fact-{i}: alpha is explained clearly here"}
             for i in range(6)]
            + [{"prompt": f"About beta #{i}",
                "response": f"beta-fact-{i}: beta is explained clearly here"}
               for i in range(6)])
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    p = ena.pool.create("tiny", str(path), name="rag", num_shards=2)
    pool = ena.store.get_pool(p["pool_id"])
    meta = dict(pool.get("metadata") or {})
    meta["curriculum"] = {"enabled": True, "source": "retrieve",
                          "topics_seed": ["alpha", "beta"], "rows_per_round": 4}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)
    pool = ena.store.get_pool(p["pool_id"])
    # force the deterministic grounded path (no model adapter)
    monkeypatch.setattr(ena.curriculum, "_model_adapter", lambda pool: None)

    a = ena.curriculum.next_dataset(pool, 2, None)
    b = ena.curriculum.next_dataset(pool, 2, None)
    assert a and a["sha256"] == b["sha256"], "retrieve generation must be replayable"
    gen = list(ds.read_jsonl(a["path"]))
    blob = " ".join(str(r.get("response", "")) for r in gen).lower()
    assert "fact" in blob, "answers should be grounded in real corpus content"
    assert "concept in the animica knowledge curriculum" not in blob  # not synthetic


def test_retrieve_rejects_placeholder_model_and_grounds(tmp_path, monkeypatch):
    """A weak model that echoes the JSON example must NOT produce '...' rows —
    the backend rejects junk self-instruct output and uses the grounded row."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    rows = [{"prompt": f"About gamma #{i}",
             "response": f"gamma-fact-{i}: gamma is a real documented concept"}
            for i in range(6)]
    path = tmp_path / "corpus.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    p = ena.pool.create("tiny", str(path), name="echo", num_shards=2)
    pool = ena.store.get_pool(p["pool_id"])
    meta = dict(pool.get("metadata") or {})
    meta["curriculum"] = {"enabled": True, "source": "retrieve",
                          "topics_seed": ["gamma"], "rows_per_round": 3}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)
    pool = ena.store.get_pool(p["pool_id"])

    class _Echo:
        def generate(self, prompt, **kw):
            return 'Sure: {"prompt": "...", "response": "..."}'
    monkeypatch.setattr(ena.curriculum, "_model_adapter", lambda pool: _Echo())

    out = ena.curriculum.next_dataset(pool, 2, None)
    assert out and out["rows"] >= 1
    gen = list(ds.read_jsonl(out["path"]))
    blob = " ".join(str(r.get("response", "")) for r in gen)
    assert "..." not in blob and "gamma-fact" in blob.lower()


def _gated_pool(ena, tmp_path):
    data = _write_dataset(tmp_path / "g.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="gate", num_shards=2,
                        eval_gate={"metric": "match_rate", "min_score": 0.5})
    return p["pool_id"]


def test_autonomous_gate_promotes_on_pass(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    pid = _gated_pool(ena, tmp_path)
    monkeypatch.setattr(ena.curriculum, "evaluate_candidate", lambda pool, c: 0.9)
    _run_round(ena, pid)
    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2 and pool["served_checkpoint"], "gate should promote"


def test_autonomous_gate_rejects_and_advances_on_fail(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    pid = _gated_pool(ena, tmp_path)
    monkeypatch.setattr(ena.curriculum, "evaluate_candidate", lambda pool, c: 0.1)
    _run_round(ena, pid)
    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2, "round should advance even on a failed gate"
    assert pool["served_checkpoint"] is None, "a regressed candidate must NOT serve"
    assert (pool.get("metadata") or {}).get("rejected_rounds"), "rejection not recorded"


def test_autonomous_gate_fails_open_when_unevaluable(tmp_path, monkeypatch):
    """If the candidate can't be scored (e.g. no GPU on the coordinator), the
    gate must not deadlock — promote and let the next round's eval judge."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    pid = _gated_pool(ena, tmp_path)
    monkeypatch.setattr(ena.curriculum, "evaluate_candidate", lambda pool, c: None)
    _run_round(ena, pid)
    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2 and pool["served_checkpoint"], "should fail open"


def _run_round_metrics(ena, pid, metrics):
    """Submit every shard of the current round with the given metrics."""
    while True:
        s = ena.pool.claim_shard(pid, "t")
        if s is None:
            break
        ena.pool.submit_shard(pid, s["shard_id"], worker_id="t",
                              metrics=dict(metrics))
        if ena.store.get_pool(pid)["round"] != s["round"]:
            break


def test_evaluate_detailed_scores_and_attributes_topics():
    from animica.ena.curriculum import evaluate_detailed
    rows = [{"prompt": "tell me about alpha", "response": "alpha is the first"},
            {"prompt": "tell me about beta", "response": "beta is the second"}]

    def gen(p):
        return "alpha is the first thing" if "alpha" in p else "no idea"

    rep = evaluate_detailed(gen, rows, ["alpha", "beta"])
    assert rep["match_rate"] == 0.5
    assert rep["topic_match_rate"]["alpha"] == 1.0
    assert rep["topic_match_rate"]["beta"] == 0.0
    assert len(rep["failures"]) == 1 and rep["failures"][0]["gold"].startswith("beta")


def test_gate_uses_trainer_reported_score_to_promote(tmp_path, monkeypatch):
    """A strong trainer-reported score promotes via the gate with NO coordinator
    model load (evaluate_candidate is env-gated off)."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    pid = _gated_pool(ena, tmp_path)  # min_score 0.5
    _run_round_metrics(ena, pid, {"eval_match_rate": 0.9})
    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2 and pool["served_checkpoint"]


def test_gate_rejects_low_trainer_reported_score(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    pid = _gated_pool(ena, tmp_path)  # min_score 0.5
    _run_round_metrics(ena, pid, {"eval_match_rate": 0.1})
    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2 and pool["served_checkpoint"] is None
    assert (pool.get("metadata") or {}).get("rejected_rounds")


def test_topic_discovery_targets_weakest_reported_topic(tmp_path, monkeypatch):
    """Per-topic scores reported by trainers steer the NEXT round's curriculum to
    the lowest-scoring topic first."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="topics", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["alpha", "beta", "gamma"], rows_per_round=6)
    # alpha is weakest, beta strongest
    _run_round_metrics(ena, pid, {"eval_match_rate": 0.5,
                                  "eval_topic_match_rate":
                                      {"alpha": 0.1, "beta": 0.9, "gamma": 0.5}})
    pool = ena.store.get_pool(pid)
    rd = (pool.get("metadata") or {}).get("round_datasets", {}).get("2")
    assert rd and rd["topics"][0] == "alpha", rd and rd["topics"]


def test_read_eval_data_returns_rows_and_topics(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="ev", num_shards=2)
    _enable_curriculum(ena, p["pool_id"], topics=["alpha", "beta"])
    ed = ena.pool.read_eval_data(p["pool_id"])
    assert ed["rows"] and isinstance(ed["rows"], list)
    assert ed["topics"] == ["alpha", "beta"]


def test_trainer_reports_eval_score_in_submit(tmp_path, monkeypatch):
    """train_shard_once evaluates its checkpoint and folds the score into the
    submit metrics (GPU-free here via stubbed training + eval)."""
    from animica.ena import training
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="tr", num_shards=2)
    pid = p["pool_id"]

    def _stub_run(cfg, store, *, manifest_path, backend=None):
        out = tmp_path / "ckpt"
        out.mkdir(exist_ok=True)
        (out / "adapter_model.safetensors").write_bytes(b"x")
        return {"run_id": "r1", "status": "completed", "output_dir": str(out),
                "metrics": {"samples": 5}}
    monkeypatch.setattr(training, "run", _stub_run)
    monkeypatch.setattr(ena.curriculum, "evaluate_checkpoint_detailed",
                        lambda bm, cp, rows, topics: {"match_rate": 0.7,
                                                      "topic_match_rate": {"x": 0.7}})
    res = ena.train_shard_once(pid, "trainer-A")
    assert res and res["status"] == "submitted"
    shard = ena.store.get_shard(res["shard_id"])
    assert shard["metrics"]["eval_match_rate"] == 0.7
    assert shard["metrics"]["eval_topic_match_rate"] == {"x": 0.7}


def test_topic_mastery_skips_after_k_rounds(tmp_path, monkeypatch):
    """A topic scored >= mastery threshold for K rounds is marked mastered and
    dropped from study; the weak topic keeps being studied."""
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="mastery", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["alpha", "beta", "gamma"], rows_per_round=6)
    rates = {"eval_match_rate": 0.7,
             "eval_topic_match_rate": {"alpha": 0.9, "beta": 0.9, "gamma": 0.2}}
    _run_round_metrics(ena, pid, rates)   # round 1
    _run_round_metrics(ena, pid, rates)   # round 2 → alpha/beta cross mastery (K=2)
    pool = ena.store.get_pool(pid)
    stats = (pool.get("metadata") or {}).get("topic_stats") or {}
    assert stats["alpha"]["mastered"] and stats["beta"]["mastered"]
    assert not stats["gamma"]["mastered"]
    rd3 = (pool.get("metadata") or {}).get("round_datasets", {}).get("3")
    assert rd3 and rd3["topics"] == ["gamma"]  # only the unmastered topic studied


def test_objectives_recorded_in_memory_ledger(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="obj", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["alpha", "beta"], rows_per_round=4)
    _run_round_metrics(ena, pid,
                       {"eval_topic_match_rate": {"alpha": 0.1, "beta": 0.5}})
    objs = ena.store.query_memory("curriculum objective", limit=50)
    texts = " ".join(o["text"] for o in objs)
    assert "alpha" in texts and "open" in texts  # self-tasked a weak topic
    # re-running the same weak score updates (not duplicates) the objective
    _run_round_metrics(ena, pid,
                       {"eval_topic_match_rate": {"alpha": 0.1, "beta": 0.5}})
    objs2 = ena.store.query_memory("curriculum objective", limit=50)
    alpha_objs = [o for o in objs2 if "topic='alpha'" in o["text"]]
    assert len(alpha_objs) == 1  # INSERT-OR-REPLACE de-duped by id


def test_replay_buffer_mixes_prior_rows(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    rows = [{"prompt": f"orig {i}", "response": f"ORIGMARK-{i} documented content"}
            for i in range(8)]
    path = tmp_path / "c.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    p = ena.pool.create("tiny", str(path), name="replay", num_shards=2)
    pool = ena.store.get_pool(p["pool_id"])
    meta = dict(pool.get("metadata") or {})
    meta["curriculum"] = {"enabled": True, "source": "synthetic",
                          "topics_seed": ["x", "y"], "rows_per_round": 8,
                          "replay_ratio": 0.5}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)
    pool = ena.store.get_pool(p["pool_id"])
    out = ena.curriculum.next_dataset(pool, 2, None)
    blob = " ".join(str(r.get("response", "")) for r in ds.read_jsonl(out["path"]))
    assert "ORIGMARK" in blob  # replayed real prior rows are present


def test_tool_rows_emit_valid_tool_calls(tmp_path, monkeypatch):
    from animica.ena.curriculum import _parse_tool_call
    ena = _make_ena(tmp_path / "e", monkeypatch)
    specs = [{"name": "read_file", "arguments": {"path": "a.py"}},
             {"name": "bash", "arguments": {"command": "ls"}}]
    rows = ena.curriculum._generate_tool_rows(specs, 4)
    assert len(rows) == 4
    for r in rows:
        pc = _parse_tool_call(r["response"])
        assert pc and pc[0] in ("read_file", "bash")
        assert r["topic"].startswith("tool:")


def test_evaluate_detailed_scores_tool_mastery():
    from animica.ena.curriculum import evaluate_detailed
    rows = [
        {"prompt": "read app.py",
         "response": 'ok\n[TOOL_CALL]\n{"name":"read_file","arguments":{"path":"app.py"}}\n[/TOOL_CALL]'},
        {"prompt": "list the files",
         "response": 'ok\n[TOOL_CALL]\n{"name":"list_files","arguments":{}}\n[/TOOL_CALL]'},
    ]

    def gen(p):
        # emits a valid tool call only for the read request
        if "read" in p:
            return 'sure\n[TOOL_CALL]\n{"name":"read_file","arguments":{"path":"app.py"}}\n[/TOOL_CALL]'
        return "I cannot do that"

    rep = evaluate_detailed(gen, rows, ["tool:read_file", "tool:list_files"])
    assert rep["match_rate"] == 0.5
    assert rep["topic_match_rate"]["tool:read_file"] == 1.0
    assert rep["topic_match_rate"]["tool:list_files"] == 0.0


def test_tool_curriculum_rows_and_eval(tmp_path, monkeypatch):
    from animica.ena.curriculum import _parse_tool_call
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="tools", num_shards=2)
    pid = p["pool_id"]
    pool = ena.store.get_pool(pid)
    meta = dict(pool.get("metadata") or {})
    meta["curriculum"] = {"enabled": True, "source": "synthetic",
                          "topics_seed": ["alpha"], "rows_per_round": 8,
                          "teach_tools": True, "tool_ratio": 0.5, "replay_ratio": 0.0}
    pool["metadata"] = meta
    ena.store.upsert_pool(pool)
    pool = ena.store.get_pool(pid)
    out = ena.curriculum.next_dataset(pool, 2, None)
    gen = list(ds.read_jsonl(out["path"]))
    assert any(_parse_tool_call(r.get("response", "")) for r in gen)  # tool rows present
    # the held-out eval now scores tool-call emission too
    ev = ena.pool.read_eval_data(pid)
    assert any("[TOOL_CALL]" in str(r.get("response", "")) for r in ev["rows"])


def test_curriculum_failure_never_breaks_promotion(tmp_path, monkeypatch):
    ena = _make_ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="boom", num_shards=2)
    pid = p["pool_id"]
    _enable_curriculum(ena, pid, topics=["alpha", "beta"])

    def _boom(*a, **k):
        raise RuntimeError("curriculum exploded")
    monkeypatch.setattr(ena.curriculum, "next_dataset", _boom)

    _run_round(ena, pid)  # must still promote despite the curriculum blowing up

    pool = ena.store.get_pool(pid)
    assert pool["round"] == 2, "promotion was broken by a curriculum failure"
    assert "curriculum exploded" in (pool.get("metadata") or {}).get(
        "curriculum_last_error", "")
