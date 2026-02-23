from __future__ import annotations

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.training_service import ENATrainingService


def test_training_config_roundtrip() -> None:
    cfg = TrainingConfig(run_name="x", iterations=100000, dataset_path="/tmp/data", learning_rate=1e-5)
    out = TrainingConfig.from_dict(cfg.to_dict())
    assert out.run_name == "x"
    assert out.iterations == 100000
    assert out.learning_rate == 1e-5


def test_progress_parser_extracts_metrics() -> None:
    current = {"total_steps": 1000}
    line = "Status: running Progress: 25% step=250 loss=0.1234 steps/sec=8.5 eval_acc=0.91 checkpoint=/tmp/c1.ckpt"
    out = ENATrainingService._parse_progress(line, current)
    assert out["progress_percent"] == 25
    assert out["current_step"] == 250
    assert abs(out["loss"] - 0.1234) < 1e-8
    assert abs(out["steps_per_sec"] - 8.5) < 1e-8
    assert out["last_checkpoint_path"] == "/tmp/c1.ckpt"
    assert "eval_acc" in out["eval_metrics"]
