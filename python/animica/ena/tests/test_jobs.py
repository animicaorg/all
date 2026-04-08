from __future__ import annotations

import json
from pathlib import Path

from animica.ena.config import load_ena_config
from animica.ena.jobs import JobManager, WorkerEngine
from animica.ena.models import JobSpec, JobStatus, JobType
from animica.ena.store import EnaStore


def test_useful_work_job_executes_and_verifies(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "sync.md"
    source.write_text("# Sync\n\nSync keeps peers aligned.\n", encoding="utf-8")

    config = load_ena_config()
    store = EnaStore(config)
    manager = JobManager(store, config)
    worker = WorkerEngine(store, config)

    record = manager.propose(
        JobSpec(
            job_type=JobType.EXTRACT,
            sources=[str(source)],
            input_payload={},
            allowed_actions=["extract"],
        )
    )

    finished = worker.execute(record)
    assert finished.status == JobStatus.VERIFIED
    assert finished.verification is not None
    assert finished.verification.passed is True
    assert finished.reward["credits"] > 0
    assert Path(finished.result["output_path"]).exists()
