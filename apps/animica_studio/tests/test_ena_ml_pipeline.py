from __future__ import annotations

import json
from pathlib import Path

import pytest

from animica_studio.ena_ml.dataset.build import bootstrap_dataset
from animica_studio.ena_ml.model.tokenizer import ByteTokenizer


torch = pytest.importorskip("torch")

from animica_studio.ena_ml.train.config import TrainerConfig
from animica_studio.ena_ml.train.trainer import Trainer


def test_bootstrap_manifest_written(tmp_path: Path) -> None:
    manifest = bootstrap_dataset(["hello", "world"], tmp_path / "dataset", shard_target_bytes=8)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["shards"]
    assert "sha256" in payload["shards"][0]


def test_trainer_honors_exact_total_steps(tmp_path: Path) -> None:
    tok = ByteTokenizer()
    token_ids = tok.encode("abc " * 1024)
    cfg = TrainerConfig(total_steps=12, batch_size=2, seq_len=16, checkpoint_interval=6, eval_interval=4)
    trainer = Trainer(cfg)
    out = trainer.train(token_ids, tmp_path / "run")
    assert out["total_steps"] == 12

    metrics_lines = (tmp_path / "run" / "train_metrics.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(metrics_lines) == 12
    final = json.loads(metrics_lines[-1])
    assert final["step"] == 12
