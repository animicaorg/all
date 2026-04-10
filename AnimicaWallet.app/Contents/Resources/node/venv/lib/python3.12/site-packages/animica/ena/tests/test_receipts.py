from __future__ import annotations

from pathlib import Path

from animica.ena.config import load_ena_config
from animica.ena.jobs import JobManager, WorkerEngine
from animica.ena.models import JobSpec, JobStatus, JobType
from animica.ena.receipts import validate_receipt
from animica.ena.store import EnaStore


def test_useful_work_manifest_hash_receipt_and_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "sync.md"
    source.write_text("# Sync\n\nSync downloads headers before state.\n", encoding="utf-8")

    config = load_ena_config()
    store = EnaStore(config)
    manager = JobManager(store, config)
    worker = WorkerEngine(store, config)

    spec = JobSpec(
        job_type=JobType.EXTRACT,
        sources=[str(source)],
        input_payload={},
        allowed_actions=["extract"],
    )
    first = manager.create(spec)
    second = manager.create(spec.model_copy())

    assert first.job_hash == second.job_hash
    assert first.job_id == second.job_id

    finished = worker.execute(first)
    assert finished.status == JobStatus.VERIFIED

    receipt = manager.receipt(first.job_id)
    assert receipt is not None
    assert validate_receipt(receipt)["ok"] is True
    assert receipt.reward["credits"] > 0

    exported = manager.export_onchain(first.job_id)
    assert exported is not None
    assert exported["validation"]["ok"] is True
    assert exported["onchain"]["credit_event"]["job_id"] == first.job_id
