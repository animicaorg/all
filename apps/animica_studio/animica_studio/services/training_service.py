from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.models.training_models import TrainingConfig, TrainingMetrics, TrainingRun
from animica_studio.services.job_runner import JobHandle, JobRunner, run_cli_blocking
from animica_studio.storage.config import Config, save_config
from animica_studio.util.paths import app_data_dir

# ENA training CLI discovery (canonical from python/animica/cli/ena.py):
# - Command group: `animica ena train`
# - Methods:
#   - submit: `animica ena train submit --plan <plan.json> --budget <anm> [--endpoint --rpc-url --from --json]`
#   - watch: `animica ena train watch <job_id> [--interval N] [--json]`
#   - list:  `animica ena train list [--status --limit --json]`
# - Checkpoints: `animica ena checkpoints list|fetch|publish`
# - Primary progress signal: watch output lines containing `Status:`, `Progress:` and optional free-form message.
# - JSON mode (`--json`) may include structured fields: status, progress, message, budget, spent.


class ENATrainingService(QObject):
    log_line = Signal(str, str, str)  # run_id, tag, text
    metrics_updated = Signal(str, dict)
    status_changed = Signal(str, str)
    run_finished = Signal(str, str)

    WATCH_GRACE_SECONDS = 3

    def __init__(self, config: Config, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._runner = JobRunner.instance()
        self._runs_path = app_data_dir() / "ena_runs.json"
        self._runs: dict[str, TrainingRun] = {}
        self._handles: dict[str, JobHandle] = {}
        self._watch_handles: dict[str, JobHandle] = {}
        self._runtime_timers: dict[str, QTimer] = {}
        self._watch_jobs_to_run: dict[str, str] = {}
        self._load_runs()

    def last_config(self) -> TrainingConfig:
        return TrainingConfig.from_dict((self._config.ena.get("training") or {}).get("last_config"))

    def save_last_config(self, cfg: TrainingConfig) -> None:
        ena_training = dict(self._config.ena.get("training") or {})
        ena_training["last_config"] = cfg.to_dict()
        self._config.ena["training"] = ena_training
        save_config(self._config)

    def list_runs(self) -> list[TrainingRun]:
        return sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)

    def start_training(self, config: TrainingConfig) -> str:
        self._validate_config(config)
        self._verify_cli_support()

        run_id = f"run-{uuid.uuid4().hex[:12]}"
        plan_path = self._write_plan_file(run_id, config)

        run = TrainingRun(
            run_id=run_id,
            started_at=time.time(),
            config=config.to_dict(),
            status="starting",
            last_metrics=asdict(TrainingMetrics(total_steps=config.effective_iterations())),
        )
        self._runs[run_id] = run
        self._persist_runs()
        self.save_last_config(config)
        self.status_changed.emit(run_id, "starting")

        args = ["ena", "train", "submit", "--plan", str(plan_path), "--budget", str(config.budget_anm), "--json"]
        submit_handle = self._runner.run_cli(args, timeout_s=3600)
        self._handles[run_id] = submit_handle
        submit_handle.output.connect(lambda _jid, stream, text, rid=run_id: self._on_submit_output(rid, stream, text))
        submit_handle.error.connect(lambda _jid, msg, details, rid=run_id: self._on_submit_error(rid, msg, details))
        submit_handle.finished.connect(lambda _jid, code, _payload, rid=run_id: self._on_submit_finished(rid, code))

        if config.max_runtime_minutes:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda rid=run_id: self._on_runtime_limit(rid))
            timer.start(int(config.max_runtime_minutes) * 60 * 1000)
            self._runtime_timers[run_id] = timer

        return run_id

    def stop_training(self, run_id: str) -> None:
        watch = self._watch_handles.get(run_id)
        if watch:
            self._runner.cancel(watch.job_id)
            self.log_line.emit(run_id, "system", "Stopped watch process.")

        handle = self._handles.get(run_id)
        if handle:
            self._runner.cancel(handle.job_id)

        run = self._runs.get(run_id)
        if run and run.job_id:
            # CLI currently exposes submit/list/watch. Cancel endpoint may not be supported.
            self.log_line.emit(run_id, "system", "Remote cancel not supported by current CLI; stopped local watch.")

        self._set_status(run_id, "stopped")

    def resume_training(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if not run or not run.job_id:
            raise ValueError("Run has no remote job id to resume watch.")
        self._start_watch(run_id, run.job_id)

    def status(self, run_id: str) -> TrainingRun | None:
        return self._runs.get(run_id)

    def _verify_cli_support(self) -> None:
        probe = run_cli_blocking(["ena", "train", "--help"], timeout_s=15, config=self._config)
        text = (probe.stdout or "") + "\n" + (probe.stderr or "")
        if probe.returncode != 0:
            raise RuntimeError(
                "Unable to validate 'animica ena train --help'. Configure CLI path in Settings and verify ENA CLI is installed."
            )
        for sub in ("submit", "watch", "list"):
            if sub not in text:
                raise RuntimeError(f"CLI missing 'animica ena train {sub}'. Detected help:\n{text[:400]}")

    def _validate_config(self, cfg: TrainingConfig) -> None:
        if not cfg.iterations and not cfg.epochs:
            raise ValueError("Set iterations or epochs.")
        if cfg.iterations is not None and int(cfg.iterations) < 1:
            raise ValueError("iterations must be >= 1")
        if cfg.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if cfg.learning_rate <= 0:
            raise ValueError("learning_rate must be > 0")

        if cfg.dataset_path:
            p = Path(cfg.dataset_path).expanduser()
            if not p.exists():
                raise ValueError(f"dataset path does not exist: {p}")

        out = cfg.ensure_output_dir()
        if not out.exists() or not out.is_dir():
            raise ValueError(f"output_dir is not writable: {out}")

    def _write_plan_file(self, run_id: str, cfg: TrainingConfig) -> Path:
        run_dir = cfg.ensure_output_dir() / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        hp: dict[str, Any] = {
            "learning_rate": cfg.learning_rate,
            "batch_size": cfg.batch_size,
            "optimizer": cfg.optimizer,
            "eval_interval_steps": cfg.eval_interval_steps,
            "checkpoint_interval_steps": cfg.checkpoint_interval_steps,
            "num_workers": cfg.num_workers,
            "threads": cfg.threads,
            "gradient_accumulation_steps": cfg.gradient_accumulation_steps,
            "seed": cfg.seed,
            "precision": cfg.precision,
            "device": cfg.device,
            "gpu_id": cfg.gpu_id,
            "lora_enabled": cfg.lora_enabled,
            "lora_rank": cfg.lora_rank,
            "max_runtime_minutes": cfg.max_runtime_minutes,
            "early_stop_patience": cfg.early_stop_patience,
            "iterations": cfg.iterations,
            "epochs": cfg.epochs,
        }
        plan = {
            "job_id": cfg.run_name or run_id,
            "job_type": "ena.train.sft",
            "base_model": cfg.base_model,
            "dataset_hashes": [cfg.dataset_id] if cfg.dataset_id else [],
            "dataset_path": cfg.dataset_path,
            "checkpoint_resume": cfg.resume_checkpoint,
            "hyperparams": {k: v for k, v in hp.items() if v is not None},
            "output_dir": str(run_dir),
        }
        plan_path = run_dir / "training_plan.json"
        plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
        return plan_path

    def _on_submit_output(self, run_id: str, stream: str, text: str) -> None:
        tag = "stdout" if stream == "stdout" else "stderr"
        self.log_line.emit(run_id, tag, text)
        run = self._runs.get(run_id)
        if run is None:
            return
        try:
            payload = json.loads(text)
            job_id = payload.get("job_id")
            if job_id and not run.job_id:
                run.job_id = str(job_id)
                self._persist_runs()
                self._set_status(run_id, "running")
                self._start_watch(run_id, run.job_id)
        except json.JSONDecodeError:
            if "Job ID:" in text and not run.job_id:
                run.job_id = text.split("Job ID:", 1)[1].strip()
                self._persist_runs()
                self._set_status(run_id, "running")
                self._start_watch(run_id, run.job_id)

    def _on_submit_error(self, run_id: str, msg: str, details: str) -> None:
        self.log_line.emit(run_id, "error", f"{msg} {details}".strip())

    def _on_submit_finished(self, run_id: str, exit_code: int) -> None:
        if exit_code != 0:
            run = self._runs.get(run_id)
            if run and not run.job_id:
                run.error = f"submit failed (exit {exit_code})"
                self._set_status(run_id, "failed")

    def _start_watch(self, run_id: str, job_id: str) -> None:
        watch = self._runner.run_cli(["ena", "train", "watch", job_id, "--interval", "2"], timeout_s=86400)
        self._watch_handles[run_id] = watch
        self._watch_jobs_to_run[watch.job_id] = run_id
        watch.output.connect(lambda jid, stream, text: self._on_watch_output(jid, stream, text))
        watch.error.connect(lambda jid, msg, details: self._on_watch_error(jid, msg, details))
        watch.finished.connect(lambda jid, code, _payload: self._on_watch_finished(jid, code))

    def _on_watch_output(self, watch_job_id: str, stream: str, text: str) -> None:
        run_id = self._watch_jobs_to_run.get(watch_job_id)
        if not run_id:
            return
        tag = "stdout" if stream == "stdout" else "stderr"
        self.log_line.emit(run_id, tag, text)
        metrics = self._parse_progress(text, self._runs[run_id].last_metrics or {})
        if metrics:
            self._runs[run_id].last_metrics = metrics
            self.metrics_updated.emit(run_id, metrics)
            self._persist_runs()
        lowered = text.lower()
        if "job completed" in lowered or "status: completed" in lowered:
            self._set_status(run_id, "completed")
        if "status: failed" in lowered:
            self._set_status(run_id, "failed")
        if "status: cancelled" in lowered:
            self._set_status(run_id, "stopped")

    def _on_watch_error(self, watch_job_id: str, msg: str, details: str) -> None:
        run_id = self._watch_jobs_to_run.get(watch_job_id)
        if run_id:
            self.log_line.emit(run_id, "error", f"{msg} {details}".strip())

    def _on_watch_finished(self, watch_job_id: str, code: int) -> None:
        run_id = self._watch_jobs_to_run.pop(watch_job_id, None)
        if not run_id:
            return
        if code != 0 and self._runs[run_id].status not in {"stopped", "failed", "completed"}:
            self._set_status(run_id, "failed")

    def _on_runtime_limit(self, run_id: str) -> None:
        self.log_line.emit(run_id, "system", "Max runtime reached; stopping training watch.")
        self.stop_training(run_id)

    def _set_status(self, run_id: str, status: str) -> None:
        run = self._runs.get(run_id)
        if not run:
            return
        run.status = status
        if status in {"completed", "failed", "stopped"}:
            run.ended_at = time.time()
            timer = self._runtime_timers.pop(run_id, None)
            if timer:
                timer.stop()
        self._persist_runs()
        self.status_changed.emit(run_id, status)
        if status in {"completed", "failed", "stopped"}:
            self.run_finished.emit(run_id, status)

    def _load_runs(self) -> None:
        if not self._runs_path.exists():
            return
        try:
            payload = json.loads(self._runs_path.read_text(encoding="utf-8"))
            self._runs = {
                str(item["run_id"]): TrainingRun(**item)
                for item in payload
                if isinstance(item, dict) and item.get("run_id")
            }
        except Exception:
            self._runs = {}

    def _persist_runs(self) -> None:
        data = [asdict(r) for r in self._runs.values()]
        self._runs_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _parse_progress(text: str, current: dict[str, Any]) -> dict[str, Any]:
        out = dict(current)
        m_step = re.search(r"step\s*[=:]\s*(\d+)", text, re.IGNORECASE)
        if m_step:
            out["current_step"] = int(m_step.group(1))

        m_progress = re.search(r"progress\s*[=:]\s*(\d+)%", text, re.IGNORECASE)
        if m_progress:
            out["progress_percent"] = int(m_progress.group(1))

        m_loss = re.search(r"loss\s*[=:]\s*([0-9]*\.?[0-9]+)", text, re.IGNORECASE)
        if m_loss:
            out["loss"] = float(m_loss.group(1))

        m_sps = re.search(r"(steps?/sec|sps)\s*[=:]\s*([0-9]*\.?[0-9]+)", text, re.IGNORECASE)
        if m_sps:
            out["steps_per_sec"] = float(m_sps.group(2))

        m_ckpt = re.search(r"checkpoint(?:_path)?\s*[=:]\s*(\S+)", text, re.IGNORECASE)
        if m_ckpt:
            out["last_checkpoint_path"] = m_ckpt.group(1)

        if "eval" in text.lower():
            pairs = re.findall(r"([a-zA-Z_]+)\s*[=:]\s*([0-9]*\.?[0-9]+)", text)
            eval_metrics = dict(out.get("eval_metrics") or {})
            for k, v in pairs:
                if k.lower().startswith("eval"):
                    eval_metrics[k] = float(v)
            if eval_metrics:
                out["eval_metrics"] = eval_metrics

        return out


TrainingService = ENATrainingService
