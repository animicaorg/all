from __future__ import annotations

import json
import sys
from pathlib import Path

from animica.ena.config import load_ena_config
from animica.ena.store import EnaStore
from animica.ena.training import TrainingManager


def _write_training_rows(path: Path, rows: int = 10) -> None:
    payloads = []
    for index in range(rows):
        payloads.append(
            {
                "sample_id": f"s-{index}",
                "task_type": "summarize",
                "input_text": f"Prompt {index}",
                "output_text": f"Answer {index}",
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in payloads) + "\n", encoding="utf-8")


def test_training_manifest_generation_with_splits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    store = EnaStore(config)
    manager = TrainingManager(store, config)

    dataset = tmp_path / "train.jsonl"
    _write_training_rows(dataset, rows=12)
    manifest_path = tmp_path / "manifest.json"

    manifest = manager.prepare(
        dataset,
        out_path=manifest_path,
        base_model="tiny-local-model",
        backend="command",
        auto_split=True,
    )

    assert manifest["base_model"] == "tiny-local-model"
    assert Path(manifest["train"]["path"]).exists()
    assert Path(manifest["eval"]["path"]).exists()
    assert Path(manifest["test"]["path"]).exists()


def test_training_command_runner_orchestration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    store = EnaStore(config)
    manager = TrainingManager(store, config)

    dataset = tmp_path / "train.jsonl"
    _write_training_rows(dataset, rows=4)
    manifest_path = tmp_path / "manifest.json"
    launcher_command = " ".join(
        [
            sys.executable,
            "-c",
            "import json, pathlib, sys; out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); (out / 'metrics.json').write_text(json.dumps({'loss': 0.1}))",
            "{output_dir}",
        ]
    )
    manager.prepare(
        dataset,
        out_path=manifest_path,
        base_model="tiny-local-model",
        backend="command",
        launcher={"command": [sys.executable, "-c", "import json, pathlib, sys; out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); (out / 'metrics.json').write_text(json.dumps({'loss': 0.1}))", "{output_dir}"]},
    )

    run = manager.run(manifest_path)
    assert run.status == "completed"
    assert run.metrics["loss"] == 0.1
    assert run.artifact_ids
    assert manager.status(run.run_id) is not None
