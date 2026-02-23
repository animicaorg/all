from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.ena_remote_preflight import ServicesPreflight
from animica_studio.services.ena_training_service import ENATrainingService
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
    out = ServicesPreflight.check("http://nonexistent-hostname.invalid")
    assert out.ok is False
    assert out.error_kind == "DNS"


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

    def run_callable(self, fn, timeout_s: int = 0):
        fn()
        return _DummyHandle()


class _DummyService(ENATrainingService):
    def _verify_local_cli_support(self) -> None:
        self._local_mode_impl = "internal"

    def _verify_remote_cli_support(self) -> None:
        return None


def test_local_mode_never_uses_submit(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="local", budget_anm="5")
    svc.start_training(train_cfg)

    assert svc.list_runs()[0].status in {"running", "completed"}
    assert runner.calls == []


def test_local_guard_blocks_submit_argv(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)
    cfg = Config()
    svc = _DummyService(cfg)
    svc._local_mode_impl = "submit"

    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    train_cfg = TrainingConfig(output_dir=str(tmp_path), iterations=1, training_mode="local")

    try:
        svc.build_local_train_argv(train_cfg, plan)
        assert False, "expected guard exception"
    except RuntimeError as exc:
        assert "local mode cannot execute submit" in str(exc)


def test_remote_mode_requires_services_url_and_blocks(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="remote", services_url="")
    run_id = svc.start_training(train_cfg)

    assert runner.calls == []
    assert svc.status(run_id).status == "failed"


def test_remote_mode_preflight_failure_blocks_submission(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)

    class _BadPreflight:
        ok = False
        resolved_ips = []
        error_kind = "DNS"
        message = "DNS resolution failed"

        def to_dict(self):
            return {"ok": False}

    monkeypatch.setattr("animica_studio.services.ena_training_service.ServicesPreflight.check", lambda _url: _BadPreflight())

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="remote", services_url="http://badhost")
    run_id = svc.start_training(train_cfg)

    assert runner.calls == []
    assert svc.status(run_id).status == "failed"


def test_config_default_backend_is_local() -> None:
    cfg = Config()
    assert cfg.ena.get("job_backend") == "local"


def test_backend_switch_updates_saved_config(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=2, training_mode="remote", services_url="http://example.com")

    class _OkPreflight:
        ok = True
        resolved_ips = ["127.0.0.1"]
        error_kind = ""
        message = "ok"

        def to_dict(self):
            return {"ok": True}

    monkeypatch.setattr("animica_studio.services.ena_training_service.ServicesPreflight.check", lambda _url: _OkPreflight())
    svc.start_training(train_cfg)
    assert cfg.ena.get("job_backend") == "remote"


def test_smoke_local_run_reaches_running_or_completed(monkeypatch, tmp_path: Path) -> None:
    runner = _DummyRunner()
    monkeypatch.setattr("animica_studio.services.ena_training_service.JobRunner.instance", lambda: runner)

    cfg = Config()
    svc = _DummyService(cfg)
    train_cfg = TrainingConfig(dataset_path="", output_dir=str(tmp_path), iterations=3, training_mode="local")
    run_id = svc.start_training(train_cfg)

    status = svc.status(run_id).status
    assert status in {"running", "completed"}
