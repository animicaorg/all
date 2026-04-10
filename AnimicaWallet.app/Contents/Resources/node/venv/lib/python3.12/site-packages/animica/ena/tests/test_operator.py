from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from animica.cli.ena import app
from animica.ena.config import load_ena_config
from animica.ena.jobs import JobManager, WorkerEngine
from animica.ena.models import JobSpec, JobType
from animica.ena.operator import EnaOperator
from animica.ena.store import EnaStore
from animica.ena.training import TrainingManager


def test_operator_extract_build_dataset_and_index_stats(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "sync.md"
    source.write_text("# Sync\n\nSync downloads headers first and validates ancestry.\n", encoding="utf-8")

    config = load_ena_config()
    store = EnaStore(config)
    operator = EnaOperator(store=store, config=config)

    extracted = operator.extract_schema(
        [str(source)],
        schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "summary": {"type": "string"},
            },
            "required": ["summary"],
            "additionalProperties": False,
        },
        out=tmp_path / "schema.jsonl",
    )
    assert extracted["rows"] == 1
    first_row = json.loads(Path(extracted["output_path"]).read_text(encoding="utf-8").splitlines()[0])
    assert "summary" in first_row["extracted"]

    built = operator.build_dataset(
        [Path(extracted["output_path"])],
        raw_out=tmp_path / "combined.raw.jsonl",
        split=True,
        manifest_path=tmp_path / "dataset_manifest.json",
    )
    assert Path(built["final_dataset_path"]).exists()
    assert Path(built["manifest_path"]).exists()
    assert built["split"] is not None

    index_result = operator.index.index_path(docs, index_name="docs", reset=True)
    stats = operator.index.stats("docs")
    assert stats["index"]["manifest_artifact_id"] == index_result["index_manifest_artifact_id"]
    verify = operator.verify_artifact(index_result["index_manifest_artifact_id"])
    assert verify["ok"] is True


def test_credits_adapter_and_mining_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "sync.md"
    source.write_text("# Sync\n\nSync keeps peers aligned.\n", encoding="utf-8")

    config = load_ena_config()
    store = EnaStore(config)
    jobs = JobManager(store, config)
    worker = WorkerEngine(store, config)
    operator = EnaOperator(store=store, config=config)

    record = jobs.create(JobSpec(job_type=JobType.EXTRACT, input_payload={}, sources=[str(source)], allowed_actions=["extract"]))
    finished = worker.execute(record)
    assert finished.reward["credits"] > 0

    credits = operator.credits_show(limit=10)
    assert int(credits["totals"]["balance_total"]) >= finished.reward["credits"]

    mining = operator.mining_status()
    assert mining["verified_receipt_count"] >= 1
    assert int(mining["credit_totals"]["balance_total"]) >= finished.reward["credits"]


def test_training_resume_reuses_output_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    store = EnaStore(config)
    manager = TrainingManager(store, config)

    dataset = tmp_path / "train.jsonl"
    rows = [
        {"sample_id": "s-1", "task_type": "summarize", "input_text": "Prompt 1", "output_text": "Answer 1"},
        {"sample_id": "s-2", "task_type": "summarize", "input_text": "Prompt 2", "output_text": "Answer 2"},
    ]
    dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manager.prepare(
        dataset,
        out_path=manifest_path,
        base_model="tiny-local-model",
        backend="command",
        launcher={
            "command": [
                sys.executable,
                "-c",
                "import json, pathlib, sys; out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); (out / 'metrics.json').write_text(json.dumps({'loss': 0.2}))",
                "{output_dir}",
            ]
        },
    )

    first = manager.run(manifest_path)
    resumed = manager.resume(first.run_id)
    assert resumed.resumed_from_run_id == first.run_id
    assert resumed.output_dir == first.output_dir


def test_cli_doctor_demo_runs_and_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    runner = CliRunner()

    config_path = tmp_path / "ena.toml"
    init_result = runner.invoke(app, ["--json", "config", "init", "--path", str(config_path)])
    assert init_result.exit_code == 0

    base_args = ["--config", str(config_path), "--json"]
    doctor = runner.invoke(app, base_args + ["doctor"])
    assert doctor.exit_code == 0
    assert "\"ok\": true" in doctor.stdout.lower()

    demo = runner.invoke(app, base_args + ["demo", "--work-dir", str(tmp_path / "demo")])
    assert demo.exit_code == 0
    assert "\"ok\": true" in demo.stdout.lower()

    runs = runner.invoke(app, base_args + ["runs", "list"])
    assert runs.exit_code == 0
    assert "session_id" in runs.stdout

    artifacts = runner.invoke(app, base_args + ["artifacts", "list"])
    assert artifacts.exit_code == 0
    assert "artifact_id" in artifacts.stdout

    credits = runner.invoke(app, base_args + ["credits", "show"])
    assert credits.exit_code == 0
    assert "balance_total" in credits.stdout

    mining = runner.invoke(app, base_args + ["mining", "status"])
    assert mining.exit_code == 0
    assert "verified_receipt_count" in mining.stdout
