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


def test_round_shards_unlimited_by_default(tmp_path, monkeypatch):
    """No artificial ceiling: with many active miners (and enough rows) a round
    materialises one shard per miner, well beyond the old 64 cap — so each shard
    is a smaller slice and OOM-prone machines can still train."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=200)  # enough rows to shard
    p = e.pool.create("tiny", data, name="unlimited", num_shards=4)
    pid = p["pool_id"]
    for w in [f"m{i}" for i in range(80)]:        # 80 miners > the old cap of 64
        e.pool.heartbeat(pid, w)
    e.pool.claim_shard(pid, "m0")
    shards = e.store.list_shards(pid, round=1)
    assert len(shards) == 80, f"expected 80 shards (no cap), got {len(shards)}"


def test_shard_count_capped_only_when_max_shards_set(tmp_path, monkeypatch):
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


def test_touch_worker_does_not_clobber_served_checkpoint(tmp_path, monkeypatch):
    """The promote race: a heartbeat/claim carries a stale pool copy and must NOT
    overwrite a served_checkpoint/round that aggregate() just promoted."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = e.pool.create("tiny", data, name="served", num_shards=2)
    pid = p["pool_id"]
    # simulate aggregate() having promoted a checkpoint + advanced the round
    fresh = e.store.get_pool(pid)
    fresh["served_checkpoint"] = {"round": 1, "checkpoint_hash": "deadbeef", "path": "/x"}
    fresh["round"] = 2
    e.store.upsert_pool(fresh)
    # a worker heartbeats holding a STALE pool dict (read before promotion)
    e.pool._touch_worker({"pool_id": pid, "served_checkpoint": None, "round": 1},
                         "worker-x")
    after = e.store.get_pool(pid)
    assert after["served_checkpoint"] is not None          # NOT clobbered
    assert after["served_checkpoint"]["checkpoint_hash"] == "deadbeef"
    assert after["round"] == 2                              # round preserved
    assert "worker-x" in (after.get("metadata") or {}).get("active_workers", {})


def test_status_reports_actual_shard_count(tmp_path, monkeypatch):
    """status().num_shards is the ACTUAL materialised count (what the UI shows as
    'shards (total)'), not the static creation value."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="status", num_shards=4)
    pid = p["pool_id"]
    pool = e.store.get_pool(pid); pool["metadata"] = {"max_rows_per_shard": 8}
    e.store.upsert_pool(pool)
    e.pool.claim_shard(pid, "m")           # materialise -> 8 shards
    st = e.pool.status(pid)
    assert st["num_shards"] == 8           # actual count, not the configured 4
    assert st["configured_num_shards"] == 4
    assert st["shards_total"] == 8


def test_size_driven_sharding_many_small_shards_one_miner(tmp_path, monkeypatch):
    """With max_rows_per_shard set, a round splits into many small shards even
    with a single miner — so a low-memory machine trains small shards (no OOM)."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="bysize", num_shards=4)
    pid = p["pool_id"]
    pool = e.store.get_pool(pid)
    pool["metadata"] = {"max_rows_per_shard": 8}   # <=8 rows per shard
    e.store.upsert_pool(pool)
    e.pool.claim_shard(pid, "solo")                # 1 miner materialises the round
    shards = e.store.list_shards(pid, round=1)
    assert len(shards) == 8                          # ceil(60/8) = 8, beyond the floor of 4
    assert all(s["row_count"] <= 8 for s in shards)  # every shard is small


def test_reshard_round_restns_stuck_round(tmp_path, monkeypatch):
    """A stuck round (claimed, nothing submitted) can be re-sharded to apply new
    sizing; refuses once work is submitted."""
    e = _ena(tmp_path / "e", monkeypatch)
    data = _write_dataset(tmp_path / "d.jsonl", n=60)
    p = e.pool.create("tiny", data, name="restuck", num_shards=4)
    pid = p["pool_id"]
    e.pool.claim_shard(pid, "stuck")                 # materialise 4 shards
    assert len(e.store.list_shards(pid, round=1)) == 4
    # enable size-driven sharding, then reshard the stuck round
    pool = e.store.get_pool(pid); pool["metadata"] = {"max_rows_per_shard": 8}
    e.store.upsert_pool(pool)
    res = e.pool.reshard_round(pid)
    assert res["resharded"] and res["new_shards"] == 8 and res["old_shards"] == 4
    # once a shard is submitted, reshard refuses (protects real work)
    s = e.pool.claim_shard(pid, "w")
    e.pool.submit_shard(pid, s["shard_id"], worker_id="w", metrics={"samples": 1})
    res2 = e.pool.reshard_round(pid)
    assert res2["resharded"] is False and res2["submitted"] == 1


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
