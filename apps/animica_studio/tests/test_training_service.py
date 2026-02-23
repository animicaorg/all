from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.ena_remote_preflight import run_remote_preflight
from animica_studio.services.training_service import ENATrainingService
from animica_studio.storage.config import Config


def test_training_config_roundtrip() -> None:
    cfg = TrainingConfig(run_name="x", iterations=100000, dataset_path="/tmp/data", learning_rate=1e-5)
    out = TrainingConfig.from_dict(cfg.to_dict())
    assert out.run_name == "x"
    assert out.iterations == 100000
    assert out.learning_rate == 1e-5


def test_training_mode_default_is_local() -> None:
    cfg = TrainingConfig.from_dict({})
    assert cfg.training_mode == "local"


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


def test_remote_preflight_fails_on_invalid_hostname() -> None:
    out = run_remote_preflight("http://nonexistent-hostname.invalid")
    assert out.ok is False
    assert "DNS" in out.error


@dataclass
class _DummySignal:
    def connect(self, *_args, **_kwargs) -> None:
        return None


class _DummyHandle:
    def __init__(self) -> None:
        self.job_id = "job-1"
        self.output = _DummySignal()
        self.error = _DummySignal()
        self.finished = _DummySignal()


class _DummyRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run_cli(self, args: list[str], timeout_s: int = 0):
        self.calls.append(list(args))
        return _DummyHandle()


class _DummyService(ENATrainingService):
    def _verify_local_cli_support(self) -> None:
        return None

    def _verify_remote_cli_support(self) -> None:
        return None


def test_local_mode_calls_jobrunner_with_expected_argv(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.training_service.JobRunner.instance", lambda: runner)

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="local", budget_anm="5")
    svc.start_training(train_cfg)

    assert runner.calls
    assert runner.calls[0][:3] == ["ena", "train", "submit"]
    assert "--plan" in runner.calls[0]


def test_remote_mode_preflight_failure_blocks_submission(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.training_service.JobRunner.instance", lambda: runner)

    class _BadPreflight:
        ok = False
        host = "badhost"
        resolved_ips = []
        error = "DNS resolution failed"

        def to_dict(self):
            return {"ok": False}

    monkeypatch.setattr("animica_studio.services.training_service.run_remote_preflight", lambda _url: _BadPreflight())

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="remote", services_url="http://badhost")
    run_id = svc.start_training(train_cfg)

    assert runner.calls == []
    assert svc.status(run_id).status == "failed"
