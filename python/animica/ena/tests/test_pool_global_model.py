"""Tests for the one global model registry (Phase 6): many pools, one model head."""

from __future__ import annotations

import json

import pytest

from animica.ena import ENA
from animica.ena.config import load_config
from animica.ena.errors import PoolError


def _write_dataset(path, n=12):
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return ENA(cfg=load_config())


def _promote(ena, pid):
    # Promote explicitly; pin off auto-promote so the round doesn't advance
    # under the drain loop (which would otherwise never terminate).
    pool = ena.store.get_pool(pid)
    pool["auto_promote"] = False
    ena.store.upsert_pool(pool)
    while True:
        s = ena.pool.claim_shard(pid, "trainer")
        if s is None:
            break
        ena.pool.submit_shard(pid, s["shard_id"], worker_id="trainer",
                              metrics={"samples": 8})
    return ena.pool.aggregate(pid)


def test_pool_attaches_to_global_model_and_advances_head(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="g1", num_shards=2, model_id="ena-global")
    pid = p["pool_id"]
    assert p["model_id"] == "ena-global"

    gm = ena.pool.get_global_model("ena-global")
    assert gm["base_model"] == "tiny" and gm["head"] is None

    agg = _promote(ena, pid)
    assert agg["promoted"] and agg["model_id"] == "ena-global"

    gm = ena.pool.get_global_model("ena-global")
    assert gm["head"]["pool_id"] == pid
    assert gm["head"]["checkpoint_hash"] == agg["served_checkpoint"]["checkpoint_hash"]
    assert gm["head"]["round"] == 1
    assert pid in gm["pools"]


def test_ensure_global_model_is_idempotent(ena, tmp_path):
    m1 = ena.pool.ensure_global_model("ena-global", "tiny")
    m2 = ena.pool.ensure_global_model("ena-global", "other")  # base_model not clobbered
    assert m1["created_at"] == m2["created_at"] and m2["base_model"] == "tiny"
    assert any(m["model_id"] == "ena-global" for m in ena.pool.list_models())


def test_get_global_model_missing_raises(ena):
    with pytest.raises(PoolError):
        ena.pool.get_global_model("nope")


def test_pool_without_model_id_does_not_touch_registry(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="solo", num_shards=2)  # no model_id
    assert p["model_id"] is None
    _promote(ena, p["pool_id"])
    assert ena.pool.list_models() == []
