"""Tests for the P1 coordinator-side safety fixes:

* payout never pays a round the merge guard rejected (NaN-poison / regression);
* every disbursement is recorded in the per-recipient ledger;
* a non-finite (NaN/inf) adapter is detected and never loaded for serving/eval.

Stdlib + numpy/safetensors only (no torch needed): the finiteness guard reads
the adapter safetensors directly, and the eval short-circuits before any model
load when the adapter is non-finite.
"""

from __future__ import annotations

import json

import pytest

from animica.ena import ENA, payments
from animica.ena.config import load_config
from animica.ena.models import PoolContribution

TREASURY = "anim1zqpfpwctgp7zkfhj8qr77g3d0ucvp52n7fsv3xsjdclyzwzsjryp4gs07vma0"


def _write_dataset(path, n=20) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


@pytest.fixture()
def funded_ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    return ENA(cfg=load_config())


def _add_trainer_contrib(e, pool_id, rnd, addr, weight, nano=0):
    c = PoolContribution(
        contribution_id="ctr-test-" + addr, pool_id=pool_id, round=rnd,
        role="trainer", address=addr, weight=float(weight),
        amount_nano=int(nano), ref="r%d" % rnd,
    ).to_dict()
    e.store.add_contribution(c)
    return c


def test_payout_skips_rejected_round(funded_ena, tmp_path):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl")
    p = e.pool.create("tiny", data, name="demo", num_shards=2, auto_promote=False)
    pid = p["pool_id"]

    # Give the pool a budget directly + a trainer contribution for round 1.
    pool = e.pool.get(pid)
    pool["budget_nano"] = 10_000_000_000
    # Mark round 1 as rejected (e.g. the merge guard caught an all-NaN upload).
    meta = dict(pool.get("metadata") or {})
    meta["rejected_rounds"] = [{"round": 1, "score": None, "threshold": 0.25,
                                "reason": "no_finite_merged_adapter"}]
    pool["metadata"] = meta
    e.store.upsert_pool(pool)
    _add_trainer_contrib(e, pid, 1, "anim1trainerA", 1.0)

    out = e.pool.payout(pid, round=1)
    assert out["reason"] == "round_rejected_unservable"
    assert out["entries"] == []
    # nothing disbursed, budget untouched, contribution still unpaid.
    assert e.pool.get(pid)["budget_nano"] == 10_000_000_000
    assert e.pool.get(pid).get("paid_out_nano", 0) == 0
    assert [c for c in e.store.list_contributions(pid, round=1) if not c.get("paid")]
    assert e.pool.payouts(pid) == []


def test_payout_records_per_recipient_ledger(funded_ena, tmp_path):
    e = funded_ena
    data = _write_dataset(tmp_path / "d.jsonl")
    p = e.pool.create("tiny", data, name="demo", num_shards=2, auto_promote=False)
    pid = p["pool_id"]

    pool = e.pool.get(pid)
    pool["budget_nano"] = 10_000_000_000
    e.store.upsert_pool(pool)
    # Round 2 is NOT rejected → payable. Two trainers, 3:1 work split.
    _add_trainer_contrib(e, pid, 2, "anim1trainerA", 3.0)
    _add_trainer_contrib(e, pid, 2, "anim1trainerB", 1.0)

    out = e.pool.payout(pid, round=2)
    assert out.get("reason") != "round_rejected_unservable"
    assert out["paid_nano"] > 0
    # trainers' 60% bucket = 6 ANM split 3:1.
    by_addr = {ent["address"]: ent["nano"] for ent in out["entries"]
               if ent["role"] == "trainer"}
    assert by_addr["anim1trainerA"] == 4_500_000_000
    assert by_addr["anim1trainerB"] == 1_500_000_000

    # Per-recipient ledger persisted + reconciles with the aggregate.
    ledger = e.pool.payouts(pid, round=2)
    assert {row["address"] for row in ledger} >= {"anim1trainerA", "anim1trainerB"}
    assert sum(row["nano"] for row in ledger) == out["paid_nano"]
    assert e.store.total_paid_out_nano(pid) == out["paid_nano"]
    # status exposes the ledger total.
    assert e.pool.status(pid)["paid_out_ledger_nano"] == out["paid_nano"]


def test_adapter_finiteness_guard(tmp_path):
    np = pytest.importorskip("numpy")
    st = pytest.importorskip("safetensors.numpy")
    from animica.ena.serving import adapter_is_finite

    finite_dir = tmp_path / "finite"
    finite_dir.mkdir()
    st.save_file({"w": np.ones((4, 4), dtype=np.float32)},
                 str(finite_dir / "adapter_model.safetensors"))
    assert adapter_is_finite(finite_dir) is True

    nan_dir = tmp_path / "nan"
    nan_dir.mkdir()
    bad = np.full((4, 4), np.nan, dtype=np.float32)
    st.save_file({"w": bad}, str(nan_dir / "adapter_model.safetensors"))
    assert adapter_is_finite(nan_dir) is False

    # The trainer-side eval reports NULL (no score) for a non-finite adapter so a
    # NaN model can never feed the gate a misleading match_rate.
    from animica.ena.curriculum import CurriculumService
    cur = CurriculumService.__new__(CurriculumService)  # path needs no heavy init
    res = CurriculumService.evaluate_checkpoint_detailed(
        cur, "tiny-base", str(nan_dir),
        [{"prompt": "Q", "response": "A"}], ["topic"])
    assert res == {}
