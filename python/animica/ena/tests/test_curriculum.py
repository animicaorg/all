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
