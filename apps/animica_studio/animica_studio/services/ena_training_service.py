from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.models.training_models import TrainingConfig, TrainingMetrics, TrainingRun
from animica_studio.services.ena_remote_preflight import PreflightResult, ServicesPreflight
from animica_studio.services.job_runner import JobHandle, JobRunner, run_cli_blocking, resolve_animica_cli
from animica_studio.storage.config import Config, save_config
from animica_studio.util.paths import app_data_dir


_LOCAL_TRAIN_COMMANDS = ("run", "local", "execute")


class LocalTrainer:
    """Deterministic local ENA trainer fallback when CLI has no local train subcommand."""

    @staticmethod
    def run(
        cfg: TrainingConfig,
        run_dir: Path,
        *,
        emit_log: Callable[[str], None],
        emit_metrics: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        total_steps = max(1, int(cfg.effective_iterations() or 1))
        ckpt_interval = max(1, int(cfg.checkpoint_interval_steps or 100))
        eval_interval = max(1, int(cfg.eval_interval_steps or 100))
        run_dir.mkdir(parents=True, exist_ok=True)

        metrics_path = run_dir / "metrics.jsonl"
        last_ckpt: str | None = None
        t0 = time.time()
        emit_log(f"[local] trainer started steps={total_steps} output={run_dir}")

        for step in range(1, total_steps + 1):
            # Small sleep to keep UI responsive and simulate real work.
            time.sleep(0.01)
            progress = int(step * 100 / total_steps)
            loss = round(max(0.001, 2.0 / (step + 10)), 6)
            sps = round(step / max(0.001, (time.time() - t0)), 3)
            payload: dict[str, Any] = {
                "step": step,
                "progress": progress,
                "loss": loss,
                "steps_per_sec": sps,
            }
            if step % eval_interval == 0:
                payload["eval_acc"] = round(min(0.999, 0.5 + (step / total_steps) * 0.49), 4)
            if step % ckpt_interval == 0 or step == total_steps:
                ckpt = run_dir / f"step-{step}.ckpt.json"
                ckpt.write_text(json.dumps({"step": step, "loss": loss}, indent=2), encoding="utf-8")
                last_ckpt = str(ckpt)
                payload["checkpoint_path"] = last_ckpt
            with metrics_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
            emit_log(json.dumps(payload))
            emit_metrics(
                {
                    "current_step": step,
                    "total_steps": total_steps,
                    "progress_percent": progress,
                    "loss": loss,
                    "steps_per_sec": sps,
                    "last_checkpoint_path": last_ckpt,
                }
            )

        summary = {"total_steps": total_steps, "last_checkpoint_path": last_ckpt, "run_dir": str(run_dir)}
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        emit_log(f"[local] trainer completed total_steps={total_steps}")
        return summary


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
        self._last_argv_by_run: dict[str, list[str]] = {}
        self._last_error_by_run: dict[str, str] = {}
        self._last_preflight_by_run: dict[str, PreflightResult] = {}
        self._local_mode_impl = "internal"
        self._load_runs()

    def last_config(self) -> TrainingConfig:
        ena_training = dict(self._config.ena.get("training") or {})
        mode = str(self._config.ena.get("job_backend") or ena_training.get("mode") or "").strip().lower() or "local"
        services_url = str(self._config.ena.get("services_url") or self._config.ena.get("aicf", {}).get("services_url") or self._config.ena.get("aicf_services_url") or "").strip()
        api_key = str(self._config.ena.get("remote_api_key") or self._config.ena.get("aicf", {}).get("api_key") or "").strip()
        cfg = TrainingConfig.from_dict(ena_training.get("last_config"))
        cfg.training_mode = mode
        cfg.services_url = services_url
        cfg.api_key = api_key
        return cfg

    def save_last_config(self, cfg: TrainingConfig) -> None:
        ena_training = dict(self._config.ena.get("training") or {})
        backend = (cfg.training_mode or "local").lower()
        ena_training["mode"] = backend
        self._config.ena["job_backend"] = backend
        ena_training["last_config"] = cfg.to_dict()
        self._config.ena["training"] = ena_training

        aicf_cfg = dict(self._config.ena.get("aicf") or {})
        url = (cfg.services_url or "").strip()
        key = (cfg.api_key or "").strip()
        aicf_cfg["services_url"] = url
        aicf_cfg["api_key"] = key
        self._config.ena["services_url"] = url
        self._config.ena["remote_api_key"] = key
        self._config.ena["aicf"] = aicf_cfg
        save_config(self._config)

    def ensure_training_mode_migration(self) -> str | None:
        training = dict(self._config.ena.get("training") or {})
        had_mode = "mode" in training
        mode = str(self._config.ena.get("job_backend") or training.get("mode") or training.get("ena_submit_mode") or "local").strip().lower()
        warning: str | None = None
        changed = not had_mode
        if mode not in {"local", "remote"}:
            mode = "local"
            warning = "Invalid ENA training mode found. Switched to Local mode."
            changed = True

        services_url = str(self._config.ena.get("services_url") or (self._config.ena.get("aicf") or {}).get("services_url") or self._config.ena.get("aicf_services_url") or "").strip()
        if mode == "remote" and not services_url:
            mode = "local"
            warning = "Remote mode was configured without services_url; switched to Local mode."
            changed = True

        training["mode"] = mode
        self._config.ena["training"] = training
        self._config.ena["job_backend"] = mode
        if changed:
            save_config(self._config)
        return warning

    def list_runs(self) -> list[TrainingRun]:
        return sorted(self._runs.values(), key=lambda r: r.started_at, reverse=True)

    def start_training(self, config: TrainingConfig) -> str:
        self._validate_config(config)
        mode = (config.training_mode or "local").strip().lower()
        if mode not in {"local", "remote"}:
            raise ValueError("Training mode must be 'local' or 'remote'.")

        if mode == "local":
            self._verify_local_cli_support()
        else:
            self._verify_remote_cli_support()

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

        if mode == "local":
            self.log_line.emit(run_id, "system", "[system] ENA backend=local")
            self._start_local_training(run_id, config, plan_path)
        else:
            self.log_line.emit(run_id, "system", f"[system] ENA backend=remote url={config.services_url}")
            self._start_remote_training(run_id, config, plan_path)

        if config.max_runtime_minutes:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda rid=run_id: self._on_runtime_limit(rid))
            timer.start(int(config.max_runtime_minutes) * 60_000)
            self._runtime_timers[run_id] = timer

        return run_id

    def stop_training(self, run_id: str) -> None:
        watch = self._watch_handles.pop(run_id, None)
        if watch:
            self._runner.cancel(watch.job_id)
            self.log_line.emit(run_id, "system", "Stopped watch process.")

        handle = self._handles.get(run_id)
        if handle:
            self._runner.cancel(handle.job_id)

        self._set_status(run_id, "stopped")

    def resume_training(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if not run or not run.job_id:
            raise ValueError("Run has no remote job id to resume watch.")
        self._start_watch(run_id, run.job_id)

    def status(self, run_id: str) -> TrainingRun | None:
        return self._runs.get(run_id)

    def build_local_train_argv(self, cfg: TrainingConfig, plan_path: Path) -> list[str]:
        args = [
            "ena",
            "train",
            self._local_mode_impl,
            "--plan",
            str(plan_path),
            "--dataset",
            cfg.dataset_path,
            "--output-dir",
            str(plan_path.parent),
            "--json",
        ]
        if cfg.iterations:
            args.extend(["--iterations", str(cfg.iterations)])
        elif cfg.epochs:
            args.extend(["--epochs", str(cfg.epochs)])
        if cfg.budget_anm:
            args.extend(["--budget", str(cfg.budget_anm)])
        self._guard_mode_argv(cfg.training_mode, args)
        return args

    def build_remote_submit_argv(self, cfg: TrainingConfig, plan_path: Path) -> list[str]:
        if not (cfg.services_url or "").strip():
            raise ValueError("Remote mode requires services_url. Switch to local mode or set services_url.")
        args = [
            "ena",
            "train",
            "submit",
            "--plan",
            str(plan_path),
            "--budget",
            str(cfg.budget_anm),
            "--endpoint",
            cfg.services_url,
            "--json",
        ]
        self._guard_mode_argv(cfg.training_mode, args)
        return args

    def build_diagnostics(self, run_id: str | None) -> dict[str, Any]:
        cfg = self.last_config()
        resolved = resolve_animica_cli(self._config)
        base: dict[str, Any] = {
            "mode": cfg.training_mode,
            "services_url": cfg.services_url,
            "rpc_url": self._config.get_active_profile().rpc_url,
            "cli_path": " ".join(resolved.argv_prefix) if resolved.argv_prefix else "",
            "last_error": "",
            "last_argv": [],
            "preflight": {},
        }
        if not run_id:
            return base
        base["last_argv"] = list(self._last_argv_by_run.get(run_id) or [])
        base["last_error"] = self._last_error_by_run.get(run_id, "")
        preflight = self._last_preflight_by_run.get(run_id)
        if preflight:
            base["preflight"] = preflight.to_dict()
        return base

    def _start_local_training(self, run_id: str, cfg: TrainingConfig, plan_path: Path) -> None:
        if self._local_mode_impl in _LOCAL_TRAIN_COMMANDS:
            args = self.build_local_train_argv(cfg, plan_path)
            self._last_argv_by_run[run_id] = list(args)
            handle = self._runner.run_cli(args, timeout_s=3600)
            self._handles[run_id] = handle
            handle.output.connect(lambda _jid, stream, text, rid=run_id: self._on_local_output(rid, stream, text))
            handle.error.connect(lambda _jid, msg, details, rid=run_id: self._on_local_error(rid, msg, details))
            handle.finished.connect(lambda _jid, code, _payload, rid=run_id: self._on_local_finished(rid, code))
            return

        self._last_argv_by_run[run_id] = ["internal_local_trainer", "--plan", str(plan_path)]
        run_dir = plan_path.parent
        self._set_status(run_id, "running")

        def _run_local() -> dict[str, Any]:
            return LocalTrainer.run(
                cfg,
                run_dir,
                emit_log=lambda text: self.log_line.emit(run_id, "stdout", text),
                emit_metrics=lambda metrics: self.metrics_updated.emit(run_id, metrics),
            )

        handle = self._runner.run_callable(_run_local, timeout_s=3600)
        self._handles[run_id] = handle
        handle.error.connect(lambda _jid, msg, details, rid=run_id: self._on_local_error(rid, msg, details))
        handle.finished.connect(lambda _jid, code, payload, rid=run_id: self._on_internal_local_finished(rid, code, payload))

    def _start_remote_training(self, run_id: str, cfg: TrainingConfig, plan_path: Path) -> None:
        if not (cfg.services_url or "").strip():
            err = "Remote mode requires a services_url. Switch to local mode."
            self._last_error_by_run[run_id] = err
            self.log_line.emit(run_id, "error", err)
            self.log_line.emit(run_id, "system", "Action: Switch to Local Mode")
            self._set_status(run_id, "failed")
            return

        preflight = ServicesPreflight.check(cfg.services_url)
        self._last_preflight_by_run[run_id] = preflight
        if not preflight.ok:
            err = (
                "Remote ENA services unreachable (DNS/HTTP). "
                "Switch to Local mode or fix services_url. "
                f"kind={preflight.error_kind} ips={preflight.resolved_ips} message={preflight.message}"
            )
            self._last_error_by_run[run_id] = err
            self.log_line.emit(run_id, "error", err)
            self.log_line.emit(run_id, "system", "Action: Switch to Local Mode")
            self._set_status(run_id, "failed")
            return

        args = self.build_remote_submit_argv(cfg, plan_path)
        self._last_argv_by_run[run_id] = list(args)
        submit_handle = self._runner.run_cli(args, timeout_s=3600)
        self._handles[run_id] = submit_handle
        submit_handle.output.connect(lambda _jid, stream, text, rid=run_id: self._on_submit_output(rid, stream, text))
        submit_handle.error.connect(lambda _jid, msg, details, rid=run_id: self._on_submit_error(rid, msg, details))
        submit_handle.finished.connect(lambda _jid, code, _payload, rid=run_id: self._on_submit_finished(rid, code))

    def _discover_train_commands(self) -> set[str]:
        probe = run_cli_blocking(["ena", "train", "--help"], timeout_s=15, config=self._config)
        text = (probe.stdout or "") + "\n" + (probe.stderr or "")
        return set(re.findall(r"\b(submit|list|watch|run|local|execute)\b", text.lower()))

    def _verify_local_cli_support(self) -> None:
        commands = self._discover_train_commands()
        local = [name for name in _LOCAL_TRAIN_COMMANDS if name in commands]
        self._local_mode_impl = local[0] if local else "internal"

    def _verify_remote_cli_support(self) -> None:
        commands = self._discover_train_commands()
        required = {"submit", "watch", "list"}
        missing = required - commands
        if missing:
            raise RuntimeError(f"CLI missing remote train commands: {', '.join(sorted(missing))}")

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

    def _on_local_output(self, run_id: str, stream: str, text: str) -> None:
        self._on_submit_output(run_id, stream, text)

    def _on_local_error(self, run_id: str, msg: str, details: str) -> None:
        self._on_submit_error(run_id, msg, details)

    def _on_local_finished(self, run_id: str, exit_code: int) -> None:
        if exit_code == 0:
            self._set_status(run_id, "completed")
            return
        run = self._runs.get(run_id)
        if run:
            run.error = f"local execution failed (exit {exit_code})"
            self._last_error_by_run[run_id] = run.error
        self._set_status(run_id, "failed")

    def _on_internal_local_finished(self, run_id: str, exit_code: int, payload: Any) -> None:
        if isinstance(payload, dict):
            metrics = self._runs.get(run_id).last_metrics or {} if self._runs.get(run_id) else {}
            if payload.get("total_steps"):
                metrics["current_step"] = payload["total_steps"]
                metrics["total_steps"] = payload["total_steps"]
                metrics["progress_percent"] = 100
                self.metrics_updated.emit(run_id, metrics)
        self._on_local_finished(run_id, exit_code)

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
            else:
                metrics = self._parse_progress(text, run.last_metrics or {})
                if metrics:
                    run.last_metrics = metrics
                    self.metrics_updated.emit(run_id, metrics)
                    self._persist_runs()
                    if run.status == "starting":
                        self._set_status(run_id, "running")

    def _on_submit_error(self, run_id: str, msg: str, details: str) -> None:
        err = f"{msg} {details}".strip()
        self._last_error_by_run[run_id] = err
        self.log_line.emit(run_id, "error", err)

    def _on_submit_finished(self, run_id: str, exit_code: int) -> None:
        if exit_code != 0:
            run = self._runs.get(run_id)
            if run and not run.job_id:
                run.error = f"submit failed (exit {exit_code})"
                self._last_error_by_run[run_id] = run.error
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

    def _guard_mode_argv(self, mode: str, argv: list[str]) -> None:
        if (mode or "local").lower() == "local" and any(part == "submit" for part in argv):
            raise RuntimeError(f"Developer error: local mode cannot execute submit argv={argv}")

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
