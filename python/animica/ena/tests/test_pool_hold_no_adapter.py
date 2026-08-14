"""
Regression: a training round where trainers report scores but upload NO adapter
must HOLD (reopen its shards, keep the round) instead of advancing.

Without this the round counter runs away while the served checkpoint is stranded
at the last real round — the "stuck on round N for days" production failure
(served_checkpoint frozen at 48 while round climbed to 61).
"""

from __future__ import annotations

import json

from animica.ena import ENA
from animica.ena.config import load_config


def _dataset(path, n=20):
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def test_no_adapter_round_holds_instead_of_advancing(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena-home"))  # isolated store
    ena = ENA(cfg=load_config())
    data = _dataset(tmp_path / "d.jsonl")
    # weight-training pool (lora) => a real adapter is required to promote
    p = ena.pool.create("tiny", data, method="lora", name="hold-test",
                        num_shards=1, auto_promote=False)
    pid = p["pool_id"]
    rnd0 = ena.pool.get(pid)["round"]

    s = ena.pool.claim_shard(pid, worker_id="t")
    # submit WITHOUT uploading an adapter (score reported, no checkpoint_path)
    ena.pool.submit_shard(pid, s["shard_id"], worker_id="t", run_id="r1",
                          checkpoint_path=None, metrics={"eval_match_rate": 0.9})

    res = ena.pool.aggregate(pid, auto=True)
    assert res["promoted"] is False
    assert res["reason"] == "held_awaiting_adapter_upload"
    assert res["held"] is True
    assert res["next_round"] == rnd0

    # round did NOT advance (no runaway) ...
    assert ena.pool.get(pid)["round"] == rnd0
    # ... and the shard was reopened so a healthy trainer can upload real weights.
    shards = ena.store.list_shards(pid, round=rnd0)
    assert any(sh["status"] == "open" for sh in shards)
    # served checkpoint is never set to garbage
    assert ena.pool.get(pid).get("served_checkpoint") in (None, {},) or \
        ena.pool.get(pid)["served_checkpoint"].get("round") != rnd0
    # the hold is recorded for observability
    held = (ena.pool.get(pid).get("metadata") or {}).get("held_rounds") or []
    assert held and held[-1]["round"] == rnd0
