"""Smoke + determinism tests for the ENA orchestration layer.

Uses only the standard library + the ENA facade (no typer/transformers), so it
runs in a minimal install. Exercises the useful-work lifecycle, deterministic
ids/hashes, retrieval, and the command-backend training run.
"""

from __future__ import annotations

import json
import os

import pytest

from animica.ena import ENA
from animica.ena import datasets as ds
from animica.ena import jobs as jobmod
from animica.ena import receipts as rc
from animica.ena.config import load_config


@pytest.fixture()
def ena(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return ENA(cfg=load_config())


def _write_raw(path) -> str:
    rows = [{"prompt": f"Q{i % 2}", "response": f"A{i % 2}"} for i in range(4)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def test_deterministic_ids_are_stable():
    a = jobmod.derive_job_ids("extract", {"source": "x"})
    b = jobmod.derive_job_ids("extract", {"source": "x"})
    c = jobmod.derive_job_ids("extract", {"source": "y"})
    assert a == b and a != c
    assert a[0].startswith("ena-") and len(a[2]) == 64  # task_id = sha3-256 hex


def test_useful_work_lifecycle(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    job = ena.jobs.create("dataset_clean", {"dataset": raw})
    assert job["status"] == "proposed"
    job = ena.jobs.run(job["job_id"])
    assert job["status"] == "completed"
    assert job["result"]["clean_rows"] == 2  # 4 rows, dup on %2 → 2 unique
    job = ena.jobs.verify(job["job_id"])
    assert job["status"] == "verified"
    receipt = ena.jobs.receipt(job["job_id"])
    assert rc.validate_receipt(receipt)["valid"]
    assert receipt["aicf_job_kind"] == "DATA_CURATION"
    env = ena.jobs.export_onchain(job["job_id"])
    assert env["validation"]["valid"]
    assert env["credit_event"]["kind"] == "aicf.useful_work.credit"
    assert env["onchain"]["receipt_hash"] == receipt["receipt_hash"]


def test_receipt_hash_excludes_volatile_fields():
    base = {"receipt_id": "r", "job_id": "j", "result_hash": "h",
            "receipt_hash": "AAA", "onchain_payload": {"x": 1}}
    h1 = rc.compute_receipt_hash(base)
    base2 = dict(base, receipt_hash="ZZZ", onchain_payload={"y": 9})
    assert h1 == rc.compute_receipt_hash(base2)


def test_atomic_claim(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    j = ena.jobs.create("dataset_clean", {"dataset": raw})
    claimed = ena.jobs.claim("miner-01", ["dataset_clean"])
    assert claimed and claimed["job_id"] == j["job_id"]
    assert claimed["status"] == "claimed" and claimed["worker_id"] == "miner-01"
    assert ena.jobs.claim("miner-02", ["dataset_clean"]) is None  # nothing left


def test_dataset_pipeline(tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    norm = ds.normalize(raw, tmp_path / "n.jsonl")
    assert norm["rows"] == 4
    dd = ds.dedupe(tmp_path / "n.jsonl", tmp_path / "c.jsonl")
    assert dd["rows"] == 2 and dd["removed"] == 2
    sp = ds.split(tmp_path / "c.jsonl", tmp_path / "sp")
    assert sum(sp[k]["row_count"] for k in ("train", "eval", "test")) == 2
    val = ds.validate(tmp_path / "c.jsonl")
    assert val["valid"]


def test_retrieval(ena, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("Animica finality uses header-first sync.", encoding="utf-8")
    info = ena.build_index([str(d)], name="docs")
    assert info["chunks"] >= 1
    hits = ena.search("finality sync", mode="hybrid")
    assert hits and hits[0]["score"] > 0


def test_training_command_backend(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    man = str(tmp_path / "m.json")
    ena.train_prepare(dataset=raw, out=man, base_model="tiny", backend="command",
                      auto_split=True, launcher_command="echo trained {manifest} {output_dir}")
    rec = ena.train_run(manifest_path=man)
    assert rec["status"] == "completed"
    assert rec["metrics"]["returncode"] == 0
    assert ena.run_status(rec["run_id"])["run_id"] == rec["run_id"]
    assert any(r["run_id"] == rec["run_id"] for r in ena.list_runs())


def test_training_methods_and_receipt(ena, tmp_path):
    from animica.ena import training
    raw = _write_raw(tmp_path / "raw.jsonl")
    man = str(tmp_path / "m.json")
    manifest = ena.train_prepare(dataset=raw, out=man, base_model="tiny", backend="command",
                                 method="lora", auto_split=True,
                                 launcher_command="echo trained {manifest} {output_dir}")
    assert manifest["hyperparameters"]["method"] == "lora"
    assert manifest["hyperparameters"]["lora"]["enabled"] is True
    rec = ena.train_run(manifest_path=man)
    assert rec["status"] == "completed"
    receipt = training.build_training_receipt(
        rec, manifest, miner_address="anim1miner", provider_id="prov-1", chain_id=1)
    assert receipt["job_type"] == "ena.train.sft"
    assert len(receipt["receipt_hash"]) == 64
    assert len(receipt["dataset_hash"]) == 64 and len(receipt["model_checkpoint_hash"]) == 64


def test_worker_local_loop(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    for i in range(3):
        ena.jobs.create("extract", {"source": raw, "n": i})  # distinct specs
    w = ena.worker(worker_id="miner-A", types=["extract"], poll_interval=0)
    stats = w.run_forever(max_jobs=3)
    assert stats.jobs_completed == 3
    assert ena.jobs.claim("miner-B", ["extract"]) is None  # queue drained


def test_stats_aggregate(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    j = ena.jobs.create("dataset_clean", {"dataset": raw})
    ena.jobs.run(j["job_id"], worker_id="miner-A")
    ena.jobs.verify(j["job_id"])
    ena.jobs.receipt(j["job_id"])
    s = ena.stats()
    assert s["jobs_total"] >= 1 and s["contributors"] >= 1
    assert s["receipts_total"] >= 1
    assert any(row["worker_id"] == "miner-A" for row in s["leaderboard"])


def test_python_transformers_backend_errors_cleanly(ena, tmp_path):
    raw = _write_raw(tmp_path / "raw.jsonl")
    man = str(tmp_path / "m.json")
    ena.train_prepare(dataset=raw, out=man, base_model="sshleifer/tiny-gpt2",
                      backend="python_transformers", auto_split=True)
    rec = ena.train_run(manifest_path=man)
    # Without transformers/datasets installed this fails with a helpful message,
    # not a crash; with them installed it completes.
    assert rec["status"] in ("completed", "failed")
    if rec["status"] == "failed":
        assert "transformers" in (rec.get("error") or "")
