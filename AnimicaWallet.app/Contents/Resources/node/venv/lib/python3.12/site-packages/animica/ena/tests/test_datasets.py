from __future__ import annotations

import json
from pathlib import Path

from animica.ena.config import load_ena_config
from animica.ena.datasets import DatasetManager
from animica.ena.store import EnaStore


def test_dataset_normalize_dedupe_and_validate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    store = EnaStore(config)
    manager = DatasetManager(store, config)

    raw = tmp_path / "raw.jsonl"
    rows = [
        {"title": "Sync Intro", "content_text": "Nodes sync by fetching headers then state."},
        {"title": "Sync Intro", "content_text": "Nodes sync by fetching headers then state."},
        {"title": "Finality", "content_text": "Finality confirms chain agreement."},
    ]
    with raw.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    normalized = tmp_path / "train.jsonl"
    normalize_result = manager.normalize(raw, normalized, task_type="summarize")
    assert normalize_result["rows"] == 3

    deduped = tmp_path / "train.deduped.jsonl"
    dedupe_result = manager.dedupe(normalized, deduped)
    assert dedupe_result["kept"] == 2
    assert dedupe_result["dropped"] == 1

    validation = manager.validate(deduped)
    assert validation["ok"] is True
