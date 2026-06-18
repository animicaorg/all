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
