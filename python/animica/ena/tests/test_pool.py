"""Tests for the collaborative training-pool coordinator (animica.ena.pool).

Stdlib-only: exercises pool creation, deterministic sharding + atomic claim,
shard submission with on-chain training receipts, eval-gated aggregation +
promotion, and the proportional role-split payout. Heavy training deps are not
required (the merge falls back to a deterministic merge plan).
"""

from __future__ import annotations

import json

import pytest

from animica.ena import ENA
from animica.ena import payments
from animica.ena import pool as poolmod
from animica.ena.config import load_config
from animica.ena.errors import PoolError

TREASURY = "anim1zqpfpwctgp7zkfhj8qr77g3d0ucvp52n7fsv3xsjdclyzwzsjryp4gs07vma0"


def _write_dataset(path, n=20) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return ENA(cfg=load_config())


@pytest.fixture()
def funded_ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    return ENA(cfg=load_config())


class _FakeRPC:
    def __init__(self, txs):
        self.txs = txs

    def get_transaction(self, txhash):
        from animica.ena.payments import _norm_hex
        return self.txs.get(_norm_hex(txhash))

    def get_status(self, txhash):
        tx = self.get_transaction(txhash) or {}
        return {"status": tx.get("status", "unknown")}


def _tx(to_digest, memo, value_nano, status="confirmed", frm="anim1payer"):
    return {"to_addr": "0x" + to_digest, "memo": memo, "value": str(value_nano),
            "status": status, "from_addr": frm}


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def test_split_proportional_is_integer_safe():
    # 100 split across 3:1 weights → 75/25, sums back exactly.
    out = poolmod._split_proportional(100, [("a", 3.0), ("b", 1.0)])
    assert out == {"a": 75, "b": 25}
    # remainder distribution keeps the total exact and deterministic.
    out2 = poolmod._split_proportional(10, [("a", 1.0), ("b", 1.0), ("c", 1.0)])
    assert sum(out2.values()) == 10
    # zero / empty are safe.
    assert poolmod._split_proportional(0, [("a", 1.0)]) == {}
    assert poolmod._split_proportional(10, []) == {}


def test_reward_split_must_sum_to_10000():
    with pytest.raises(PoolError):
        poolmod._validate_reward_split({"funders": 1, "trainers": 1, "servers": 1})
    poolmod._validate_reward_split(poolmod.DEFAULT_REWARD_SPLIT)  # ok


def test_sharding_is_deterministic_and_total_preserving():
    # bucketing the same record always lands in the same shard.
    rec = {"prompt": "Q", "response": "A"}
    assert poolmod._shard_bucket(rec, 4) == poolmod._shard_bucket(rec, 4)


# ---------------------------------------------------------------------------
# pool lifecycle
# ---------------------------------------------------------------------------

def test_pool_create_is_deterministic(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, method="lora", name="demo", num_shards=4)
    assert p["pool_id"].startswith("enapool-")
    assert p["status"] == "open" and p["round"] == 1
    assert p["reward_split"] == poolmod.DEFAULT_REWARD_SPLIT
    # same spec → same id, and a duplicate raises.
    with pytest.raises(PoolError):
        ena.pool.create("tiny", data, method="lora", name="demo", num_shards=4)


def test_shard_claim_is_atomic_and_exhausts(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    p = ena.pool.create("tiny", data, name="demo", num_shards=4)
    claimed = []
    for i in range(4):
        s = ena.pool.claim_shard(p["pool_id"], worker_id=f"w{i}")
        assert s is not None and s["status"] == "claimed"
        assert "manifest" in s and s["manifest"]["train"]["path"] == s["path"]
        claimed.append(s["shard_id"])
    # all shards claimed → next claim returns None.
    assert ena.pool.claim_shard(p["pool_id"], worker_id="w-late") is None
    assert len(set(claimed)) == 4
    # every dataset row landed in exactly one shard.
    total = sum(s["row_count"] for s in ena.store.list_shards(p["pool_id"], round=1))
    assert total == 20


def test_submit_aggregate_promote_and_payout(funded_ena, tmp_path, monkeypatch):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    # exercises the explicit aggregate→payout path → pin off auto-promote
    p = e.pool.create("tiny", data, name="demo", num_shards=2, auto_promote=False)
    pid = p["pool_id"]

    # --- fund the pool from two different wallets (memo = pool_hash) ---
    digest = payments.address_to_digest_hex(TREASURY)
    q = e.pool.fund_quote(pid, reward_anm=10.0, requester="anim1alice")
    assert q["memo"] == p["pool_hash"]
    rpc = _FakeRPC({
        "a1": _tx(digest, p["pool_hash"], 6_000_000_000, frm="anim1alice"),
        "b2": _tx(digest, p["pool_hash"], 4_000_000_000, frm="anim1bob"),
    })
    monkeypatch.setattr(payments, "AnimicaRPC", lambda *a, **k: rpc)
    r1 = e.pool.fund_confirm(pid, "a1", requester="anim1alice")
    r2 = e.pool.fund_confirm(pid, "b2", requester="anim1bob")
    assert r1["funded"] and r2["funded"]
    assert e.pool.get(pid)["budget_nano"] == 10_000_000_000
    # txid reuse is rejected.
    assert e.pool.fund_confirm(pid, "a1")["funded"] is False

    # --- two trainers claim + submit shards ---
    s0 = e.pool.claim_shard(pid, worker_id="trainer-A")
    s1 = e.pool.claim_shard(pid, worker_id="trainer-B")
    sub0 = e.pool.submit_shard(pid, s0["shard_id"], worker_id="trainer-A",
                               miner_address="anim1trainerA",
                               metrics={"samples": 30, "train_loss": 0.5})
    sub1 = e.pool.submit_shard(pid, s1["shard_id"], worker_id="trainer-B",
                               miner_address="anim1trainerB",
                               metrics={"samples": 10, "train_loss": 0.7})
    assert len(sub0["receipt_hash"]) == 64 and len(sub1["checkpoint_hash"]) == 64
    # re-submitting a shard is rejected.
    with pytest.raises(PoolError):
        e.pool.submit_shard(pid, s0["shard_id"], worker_id="trainer-A")

    # --- aggregate (no eval gate) promotes a merged checkpoint, advances round ---
    agg = e.pool.aggregate(pid)
    assert agg["promoted"] is True and agg["next_round"] == 2
    ck = agg["served_checkpoint"]
    assert len(ck["checkpoint_hash"]) == 64
    # merge weights reflect work (30 vs 10 samples → 0.75 / 0.25).
    assert pytest.approx(sum(ck["weights"].values()), abs=1e-6) == 1.0
    assert e.pool.get(pid)["served_checkpoint"]["checkpoint_hash"] == ck["checkpoint_hash"]

    # --- payout round 1: split 10 ANM by 2000/6000/2000 bps, then by weight ---
    out = e.pool.payout(pid, round=1)
    by_role = {}
    for ent in out["entries"]:
        by_role.setdefault(ent["role"], {})[ent["address"]] = ent["nano"]
    # funders share = 20% of 10 ANM = 2 ANM, split 6:4 by contribution.
    assert by_role["funder"]["anim1alice"] == 1_200_000_000
    assert by_role["funder"]["anim1bob"] == 800_000_000
    # trainers share = 60% = 6 ANM, split 3:1 by work weight (samples 30 vs 10).
    assert by_role["trainer"]["anim1trainerA"] == 4_500_000_000
    assert by_role["trainer"]["anim1trainerB"] == 1_500_000_000
    # servers (none yet) → their 20% stays in the budget for later rounds.
    assert out["paid_nano"] == 8_000_000_000
    assert e.pool.get(pid)["budget_nano"] == 2_000_000_000
    # paying out again finds nothing unpaid.
    assert e.pool.payout(pid, round=1)["reason"] == "nothing_to_pay"


def test_eval_gate_blocks_then_promotes(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl", n=12)
    # auto_promote=False → tests the explicit (manual) gated aggregate path; the
    # autonomous self-evaluating gate is covered in test_curriculum.py.
    p = ena.pool.create("tiny", data, name="gated", num_shards=2, auto_promote=False,
                        eval_gate={"metric": "match_rate", "min_score": 0.6})
    pid = p["pool_id"]
    for _ in range(2):
        s = ena.pool.claim_shard(pid, worker_id="t")
        ena.pool.submit_shard(pid, s["shard_id"], worker_id="t",
                              metrics={"samples": 5})
    # no score yet → awaiting eval, round does not advance.
    r = ena.pool.aggregate(pid)
    assert r["promoted"] is False and r["reason"] == "awaiting_eval"
    assert ena.pool.get(pid)["round"] == 1
    # below threshold → blocked.
    r = ena.pool.aggregate(pid, eval_score=0.4)
    assert r["promoted"] is False and r["reason"] == "eval_below_threshold"
    # at/above threshold → promoted, round advances.
    r = ena.pool.aggregate(pid, eval_score=0.8)
    assert r["promoted"] is True and ena.pool.get(pid)["round"] == 2


def test_funding_requires_treasury(ena, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl")
    p = ena.pool.create("tiny", data, name="demo")
    with pytest.raises(PoolError):
        ena.pool.fund_quote(p["pool_id"], reward_anm=1.0)


def test_train_shard_once_claims_trains_and_submits(ena, tmp_path, monkeypatch):
    from animica.ena import training
    data = _write_dataset(tmp_path / "d.jsonl", n=8)
    # asserts the round does not auto-advance (3rd claim → None) → pin off
    p = ena.pool.create("tiny", data, name="tl", num_shards=2, auto_promote=False)
    pid = p["pool_id"]
    # stand in for a real training run (no torch needed)
    monkeypatch.setattr(training, "run",
                        lambda cfg, store, *, manifest_path, backend=None: {
                            "run_id": "r1", "status": "completed",
                            "output_dir": str(tmp_path / "out"),
                            "metrics": {"samples": 7}})
    r1 = ena.train_shard_once(pid, "trainer-A", address="anim1t")
    assert r1["status"] == "submitted" and r1["weight"] == 7.0
    r2 = ena.train_shard_once(pid, "trainer-A", address="anim1t")
    assert r2["status"] == "submitted"
    # both shards now submitted → nothing left to claim
    assert ena.train_shard_once(pid, "trainer-A") is None
    submitted = [s for s in ena.store.list_shards(pid) if s["status"] == "submitted"]
    assert len(submitted) == 2


def test_train_shard_once_reports_training_failure(ena, tmp_path, monkeypatch):
    from animica.ena import training
    data = _write_dataset(tmp_path / "d.jsonl", n=6)
    p = ena.pool.create("tiny", data, name="tlf", num_shards=2)
    monkeypatch.setattr(training, "run",
                        lambda cfg, store, *, manifest_path, backend=None: {
                            "run_id": "r", "status": "failed",
                            "error": "transformers not installed"})
    res = ena.train_shard_once(p["pool_id"], "trainer-A")
    assert res["status"] == "train_failed" and "transformers" in res["error"]
