"""Tests for the pool warm-start / training-head logic (1.9.8).

The training head is the latest merged adapter; it advances EVERY round (gate
pass OR fail) so the next round warm-starts from it and learning compounds — the
fix for "pool trains forever but the model never improves because every round
restarts from the pristine base". Stdlib-only: the real adapter merge is
monkeypatched to a fake adapter dir (no torch needed).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from animica.ena import ENA
from animica.ena import pool as poolmod
from animica.ena.config import load_config


def _write_dataset(path, n=8) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return ENA(cfg=load_config())


def _fake_merge_factory():
    """Returns a merge_checkpoints stand-in that writes a real (empty) adapter
    dir so _training_head_path accepts it, and reports merged=True."""
    def _fake(submitted, out_dir, weights):
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
        (d / "adapter_model.safetensors").write_bytes(b"\x00")
        return {"path": str(d), "merged": True,
                "hash": hashlib.sha256(str(out_dir).encode()).hexdigest()}
    return _fake


def test_training_head_path_resolution(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="hp", num_shards=1)
    # no head yet → manifest warm-starts from nothing.
    assert ena.pool._training_head_path(p) is None
    shard = ena.pool._ensure_shards(p)[0]
    assert ena.pool._shard_manifest(p, shard)["init_adapter"] is None

    # a real adapter dir is accepted...
    adir = tmp_path / "head"
    adir.mkdir()
    (adir / "adapter_config.json").write_text("{}")
    p["training_head"] = {"path": str(adir), "round": 0}
    assert ena.pool._training_head_path(p) == str(adir)
    assert ena.pool._shard_manifest(p, shard)["init_adapter"] == str(adir)

    # ...but a merge-plan json (no adapter_config.json) is NOT (falls back to None).
    plan = tmp_path / "merge-plan.json"
    plan.write_text("{}")
    p["training_head"] = {"path": str(plan), "round": 0}
    assert ena.pool._training_head_path(p) is None


def test_tar_adapter_b64_uploads_only_adapter(tmp_path):
    # A bloated training output dir (tokenizer + per-epoch checkpoint subdir) must
    # upload ONLY the adapter files — a too-big tar was failing the checkpoint
    # upload, leaving the coordinator nothing to merge.
    from animica.ena.remote import tar_adapter_b64, extract_tar_b64
    d = tmp_path / "out"
    d.mkdir()
    (d / "adapter_config.json").write_text('{"r": 16}')
    (d / "adapter_model.safetensors").write_bytes(b"W" * 1000)
    (d / "tokenizer.json").write_bytes(b"T" * 5000)       # must be excluded
    (d / "chat_template.jinja").write_text("{{x}}")
    ck = d / "checkpoint-6"
    ck.mkdir()
    (ck / "optimizer.pt").write_bytes(b"O" * 5000)        # must be excluded
    got = sorted((extract_tar_b64(tar_adapter_b64(str(d)), tmp_path / "x")).iterdir())
    assert [f.name for f in got] == ["adapter_config.json", "adapter_model.safetensors",
                                     "chat_template.jinja"]
    # no-adapter dir (full fine-tune) falls back to shipping the whole tree
    d2 = tmp_path / "full"
    d2.mkdir()
    (d2 / "model.safetensors").write_bytes(b"x" * 100)
    got2 = sorted((extract_tar_b64(tar_adapter_b64(str(d2)), tmp_path / "y")).iterdir())
    assert [f.name for f in got2] == ["model.safetensors"]


def test_dynamic_midround_resharding_replicate(ena, tmp_path):
    # replicate mode: each trainer that joins mid-round gets a fresh FULL-dataset
    # shard (no waiting for the next round); a trainer that already holds one
    # doesn't spawn extras. split mode (default) keeps its fixed partition.
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    p = ena.pool.create("tiny", data, name="rs", num_shards=1)
    pid = p["pool_id"]
    pp = ena.store.get_pool(pid)
    md = dict(pp.get("metadata") or {})
    md["shard_mode"] = "replicate"
    md["min_shards"] = 1
    pp["metadata"] = md
    ena.store.upsert_pool(pp)
    rnd = pp["round"]
    for w in ("A", "B", "C"):
        assert ena.pool.claim_shard(pid, worker_id=w), f"{w} should get a shard"
    shards = ena.store.list_shards(pid, round=rnd)
    assert len(shards) == 3                      # one per trainer, grown on demand
    assert all(s["row_count"] == 12 for s in shards)   # replicate => full dataset
    # a worker that already holds a shard does NOT spawn another
    assert ena.pool.claim_shard(pid, worker_id="A") is None
    assert len(ena.store.list_shards(pid, round=rnd)) == 3


def test_split_mode_exhausts_no_dynamic_growth(ena, tmp_path):
    # split mode (default) must NOT grow mid-round: a fixed partition that exhausts.
    data = _write_dataset(tmp_path / "d.jsonl", n=6)
    p = ena.pool.create("tiny", data, name="sp", num_shards=2)
    pid = p["pool_id"]
    pp = ena.store.get_pool(pid)
    md = dict(pp.get("metadata") or {})
    md["min_shards"] = 2
    md["max_shards"] = 2
    pp["metadata"] = md
    ena.store.upsert_pool(pp)
    claims = [bool(ena.pool.claim_shard(pid, worker_id=f"w{i}")) for i in range(4)]
    assert claims[:2] == [True, True]            # 2 shards claimed
    assert claims[2:] == [False, False]          # then exhausted (no dynamic growth)
    assert len(ena.store.list_shards(pid, round=pp["round"])) == 2


def test_nan_adapter_is_never_served(ena, tmp_path):
    # A diverged (all-NaN) uploaded adapter must NOT be promoted/served, even if
    # the (stale/self-reported) eval score would pass the gate.
    import torch
    from safetensors.torch import save_file
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="nan", num_shards=1, auto_promote=False,
                        eval_gate={"metric": "match_rate", "min_score": 0.0})
    pid = p["pool_id"]
    s = ena.pool.claim_shard(pid, worker_id="t")
    ck = tmp_path / "nan_ckpt"
    ck.mkdir()
    save_file({"lora_A.weight": torch.full((8, 8), float("nan"))},
              str(ck / "adapter_model.safetensors"))
    (ck / "adapter_config.json").write_text('{"r": 16}')
    ena.pool.submit_shard(pid, s["shard_id"], worker_id="t", run_id="r1",
                          checkpoint_path=str(ck),
                          metrics={"samples": 5, "eval_match_rate": 0.99})
    res = ena.pool.aggregate(pid, eval_score=0.99)   # high score, but NaN weights
    assert res["promoted"] is False
    assert res["reason"] == "no_finite_merged_adapter"
    assert ena.pool.get(pid).get("served_checkpoint") is None   # garbage never served


def test_finite_adapter_still_serves(ena, tmp_path):
    # control: a finite adapter promotes normally (the guard is not over-broad).
    import torch
    from safetensors.torch import save_file
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="ok", num_shards=1, auto_promote=False,
                        eval_gate={"metric": "match_rate", "min_score": 0.0})
    pid = p["pool_id"]
    s = ena.pool.claim_shard(pid, worker_id="t")
    ck = tmp_path / "ok_ckpt"
    ck.mkdir()
    save_file({"lora_A.weight": torch.ones(8, 8)},
              str(ck / "adapter_model.safetensors"))
    (ck / "adapter_config.json").write_text('{"r": 16}')
    ena.pool.submit_shard(pid, s["shard_id"], worker_id="t", run_id="r1",
                          checkpoint_path=str(ck),
                          metrics={"samples": 5, "eval_match_rate": 0.5})
    res = ena.pool.aggregate(pid, eval_score=0.5)
    assert res["promoted"] is True
    assert ena.pool.get(pid)["served_checkpoint"]["round"] == 1


def test_head_advances_on_reject_then_promote(ena, tmp_path, monkeypatch):
    monkeypatch.setattr(poolmod, "merge_checkpoints", _fake_merge_factory())
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="warm", num_shards=1, auto_promote=True,
                        eval_gate={"metric": "match_rate", "min_score": 0.6})
    pid = p["pool_id"]

    # round 1: trainer self-reports a BELOW-gate score → auto-aggregate rejects and
    # advances the round, but the training head must STILL advance (so round 2 can
    # warm-start from it even though nothing was served).
    s = ena.pool.claim_shard(pid, worker_id="t")
    ena.pool.submit_shard(pid, s["shard_id"], worker_id="t",
                          metrics={"samples": 8, "eval_match_rate": 0.4})
    pool = ena.pool.get(pid)
    assert pool["round"] == 2                       # advanced past the rejected round
    assert pool.get("served_checkpoint") is None    # nothing served (gate failed)
    assert pool["training_head"]["round"] == 1      # but the head advanced
    head_path = pool["training_head"]["path"]
    assert Path(head_path, "adapter_config.json").is_file()

    # round 2's shard manifest now warm-starts from the round-1 head.
    s2 = ena.pool.claim_shard(pid, worker_id="t")
    assert ena.pool._shard_manifest(ena.pool.get(pid), s2)["init_adapter"] == head_path

    # round 2: above-gate score → promote; served + head both advance to round 2.
    ena.pool.submit_shard(pid, s2["shard_id"], worker_id="t",
                          metrics={"samples": 8, "eval_match_rate": 0.9})
    pool = ena.pool.get(pid)
    assert pool["served_checkpoint"]["round"] == 2
    assert pool["training_head"]["round"] == 2
    assert pool["round"] == 3
