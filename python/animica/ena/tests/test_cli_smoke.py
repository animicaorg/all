from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from animica.cli.ena import app

runner = CliRunner()


def test_cli_smoke_index_search_and_job_flow(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    docs = tmp_path / "docs"
    docs.mkdir()
    doc = docs / "finality.md"
    doc.write_text("# Finality\n\nConsensus finality confirms a stable head.\n", encoding="utf-8")
    dataset = tmp_path / "train.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "sample_id": "sample-1",
                "task_type": "summarize",
                "input_text": "What is finality?",
                "output_text": "Finality confirms a stable chain head.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    config_result = runner.invoke(app, ["--json", "config", "init", "--path", str(tmp_path / "ena.toml")])
    assert config_result.exit_code == 0
    config_flag = ["--config", str(tmp_path / "ena.toml"), "--json"]

    models_list = runner.invoke(app, config_flag + ["models", "list"])
    assert models_list.exit_code == 0
    assert "deterministic" in models_list.stdout

    model_test = runner.invoke(app, config_flag + ["models", "test", "--provider", "deterministic"])
    assert model_test.exit_code == 0
    assert "\"ok\": true" in model_test.stdout

    embedding_test = runner.invoke(app, config_flag + ["embeddings", "test", "--provider", "hashing"])
    assert embedding_test.exit_code == 0
    assert "\"dimensions\": 64" in embedding_test.stdout

    index_result = runner.invoke(app, config_flag + ["index", "build", str(docs)])
    assert index_result.exit_code == 0
    assert "chunks_indexed" in index_result.stdout

    search_result = runner.invoke(app, config_flag + ["search", "finality", "--hybrid"])
    assert search_result.exit_code == 0
    assert "finality.md" in search_result.stdout

    create_result = runner.invoke(app, config_flag + ["jobs", "create", "--type", "extract", "--source", str(doc)])
    assert create_result.exit_code == 0
    match = re.search(r'"job_id"\s*:\s*"([^"]+)"', create_result.stdout)
    assert match is not None
    job_id = match.group(1)

    run_result = runner.invoke(app, config_flag + ["jobs", "run", job_id])
    assert run_result.exit_code == 0
    assert "\"status\": \"verified\"" in run_result.stdout.lower()

    receipt_result = runner.invoke(app, config_flag + ["jobs", "receipt", job_id])
    assert receipt_result.exit_code == 0
    assert "receipt_hash" in receipt_result.stdout

    export_result = runner.invoke(app, config_flag + ["jobs", "export-onchain", job_id])
    assert export_result.exit_code == 0
    assert "credit_event" in export_result.stdout

    train_prepare = runner.invoke(
        app,
        config_flag
        + [
            "train",
            "prepare",
            "--dataset",
            str(dataset),
            "--out",
            str(tmp_path / "manifest.json"),
            "--base-model",
            "tiny-local-model",
            "--backend",
            "command",
            "--auto-split",
        ],
    )
    assert train_prepare.exit_code == 0
    assert "\"base_model\": \"tiny-local-model\"" in train_prepare.stdout
