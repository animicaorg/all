"""Shard reclaim + active-miner scaling + release tests (GPU-free)."""

from __future__ import annotations

import json

from animica.ena import ENA
from animica.ena.config import load_config
from animica.ena.models import now_ts


def _write_dataset(path, n=60) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def _ena(home, monkeypatch) -> ENA:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(home))
    return ENA(cfg=load_config())


def test_round_scales_to_active_miners(tmp_path, monkeypatch):
    """With many active miners, a round materialises one shard per miner
    (bounded by [min_shards, max_shards])."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="scale", num_shards=4)
    pid = p["pool_id"]
    # 8 distinct miners register (heartbeat) before the round materialises.
    for w in [f"miner-{i}" for i in range(8)]:
        e.pool.heartbeat(pid, w)
    e.pool.claim_shard(pid, "miner-0")  # materialises the round, sized to 8
    shards = e.store.list_shards(pid, round=1)
    assert len(shards) == 8, f"expected 8 shards for 8 miners, got {len(shards)}"


def test_shard_count_floors_and_caps(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="bounds", num_shards=4)
    pid = p["pool_id"]
    pool = e.store.get_pool(pid)
    pool["metadata"] = {"min_shards": 4, "max_shards": 5}
    e.store.upsert_pool(pool)
    for w in [f"m{i}" for i in range(20)]:        # 20 miners but cap is 5
        e.pool.heartbeat(pid, w)
    e.pool.claim_shard(pid, "m0")
    assert len(e.store.list_shards(pid, round=1)) == 5  # capped


def test_stale_claim_is_reclaimed_not_deadlocked(tmp_path, monkeypatch):
    """The bug: all shards claimed but never submitted → 'no shard available
    forever'. A stale claim must be reclaimable."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = e.pool.create("tiny", data, name="stuck", num_shards=2)
    pid = p["pool_id"]
    # one worker claims BOTH shards and never submits
    a = e.pool.claim_shard(pid, "deadbeat")
    b = e.pool.claim_shard(pid, "deadbeat")
    assert a and b
    assert e.pool.claim_shard(pid, "fresh") is None  # nothing available now
    # age out the claims, then a fresh worker reclaims one
    for s in e.store.list_shards(pid, round=1):
        s["updated_at"] = now_ts() - 100_000
        e.store.upsert_shard(s)
    reclaimed = e.pool.claim_shard(pid, "fresh")
    assert reclaimed is not None and reclaimed["worker_id"] == "fresh"


def test_release_reopens_shard(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = e.pool.create("tiny", data, name="rel", num_shards=2)
    pid = p["pool_id"]
    s = e.pool.claim_shard(pid, "w1")
    assert e.store.get_shard(s["shard_id"])["status"] == "claimed"
    res = e.pool.release_shard(pid, s["shard_id"], worker_id="w1")
    assert res["reopened"] is True
    assert e.store.get_shard(s["shard_id"])["status"] == "open"
    # a different worker can now immediately claim it
    again = e.pool.claim_shard(pid, "w2")
    assert again and again["shard_id"] == s["shard_id"]


def test_scaled_round_completes_only_when_all_shards_submitted(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="complete", num_shards=4)
    pid = p["pool_id"]
    for w in [f"m{i}" for i in range(6)]:
        e.pool.heartbeat(pid, w)
    e.pool.claim_shard(pid, "m0")  # materialise 6 shards
    shards = e.store.list_shards(pid, round=1)
    assert len(shards) == 6
    # submit 5 of 6 → not complete; the 6th → completes (round advances)
    for s in shards[:5]:
        e.pool.submit_shard(pid, s["shard_id"], worker_id=s["worker_id"],
                            metrics={"samples": 1})
    assert e.store.get_pool(pid)["round"] == 1
    e.pool.submit_shard(pid, shards[5]["shard_id"], worker_id=shards[5]["worker_id"],
                        metrics={"samples": 1})
    assert e.store.get_pool(pid)["round"] == 2
