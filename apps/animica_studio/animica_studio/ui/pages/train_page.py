from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.dataset_evolution_engine import DatasetEvolutionEngine, EvolutionQuotas
from animica_studio.services.dataset_manager import DatasetManager
from animica_studio.services.training_service import ENATrainingService
from animica_studio.services.dataset_bootstrap_runtime import bootstrap_runtime
from animica_studio.storage.config import Config, save_config
from animica_studio.ui.widgets.bootstrap_progress_widget import BootstrapProgressWidget
from animica_studio.util.threading_guard import assert_ui_thread


class NullEvolutionEngine:
    enabled = False

    def preview_next_plan(self, *_args, **_kwargs) -> dict | None:
        return None

    def apply_plan(self, *_args, **_kwargs) -> dict:
        raise RuntimeError("Dataset evolution is disabled.")




class TrainPage(QWidget):
    PRESETS = {
        "Fast test": {"iterations": 200, "batch_size": 2, "learning_rate": 5e-5},
        "Medium": {"iterations": 10_000, "batch_size": 4, "learning_rate": 2e-5},
        "Crank": {"iterations": 100_000, "batch_size": 8, "learning_rate": 1e-5},
        "Overnight": {"iterations": 1_000_000, "batch_size": 8, "learning_rate": 1e-5},
    }

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._cfg = config
        self._svc = ENATrainingService(config, self)
        self._dataset_manager = DatasetManager()
        self._init_state()
        self._mode_migration_warning = self._svc.ensure_training_mode_migration()
        self._build()
        self._wire()
        self._restore_last()
        self._refresh_runs()
        self._sync_ui_state()
        self._validate_critical_state()
        QTimer.singleShot(0, self._maybe_prompt_bootstrap)
        if self._mode_migration_warning:
            self._on_log("", "system", self._mode_migration_warning)

    def _init_state(self) -> None:
        self._bootstrap_runtime = bootstrap_runtime()
        self._bootstrap_panel: BootstrapProgressWidget | None = None
        self._active_run_id: str | None = None
        self._run_in_progress = False
        self._last_plan: dict | None = None
        self._last_plan_id: str | None = None
        self._plan_approved = False
        self._pending_actions: dict[str, str] = {}
        self._evolution_enabled = False
        self._evolution: DatasetEvolutionEngine | NullEvolutionEngine = self._build_evolution_engine()
        self._bootstrap_prompted = False

    def _build_evolution_engine(self) -> DatasetEvolutionEngine | NullEvolutionEngine:
        ena_cfg = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        enabled = bool(ena_cfg.get("enable_dataset_evolution", True))
        self._evolution_enabled = enabled
        if not enabled:
            return NullEvolutionEngine()
        return DatasetEvolutionEngine()

    def _validate_critical_state(self) -> None:
        required_attrs = [
            "_bootstrap_runtime",
            "_bootstrap_panel",
            "_active_run_id",
            "_run_in_progress",
            "_last_plan",
            "_last_plan_id",
            "_plan_approved",
            "_pending_actions",
            "_evolution_enabled",
            "_evolution",
            "_bootstrap_prompted",
        ]
        missing = [name for name in required_attrs if not hasattr(self, name)]
        if missing:
            raise RuntimeError(f"TrainPage missing required state attributes: {', '.join(missing)}")

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel("ENA Training"))

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Crank presets:"))
        for name in self.PRESETS:
            btn = QPushButton(name)
            btn.clicked.connect(lambda _=False, n=name: self._apply_preset(n))
            preset_row.addWidget(btn)
        preset_row.addStretch(1)
        root.addLayout(preset_row)

        form_box = QGroupBox("Training Configuration")
        form = QFormLayout(form_box)

        self.run_name = QLineEdit("ena-train")
        self.dataset_path = QLineEdit("")
        pick_dataset = QPushButton("Browse")
        pick_dataset.clicked.connect(self._pick_dataset)
        dataset_row = QHBoxLayout(); dataset_row.addWidget(self.dataset_path, 1); dataset_row.addWidget(pick_dataset)

        self.dataset_id = QLineEdit("")
        self.dataset_mode = QComboBox(); self.dataset_mode.addItems(["auto", "custom"])
        self.auto_docs = QSpinBox(); self.auto_docs.setRange(1, 200000); self.auto_docs.setValue(200)
        self.auto_bytes = QSpinBox(); self.auto_bytes.setRange(1, 2_000_000_000); self.auto_bytes.setValue(2_000_000)
        self.auto_langs = QLineEdit("en")
        self.auto_topics = QLineEdit("machine learning")
        auto_btn = QPushButton("Auto Dataset")
        auto_btn.clicked.connect(self._auto_dataset)
        self.bootstrap_btn = QPushButton("Bootstrap Big Dataset")
        self.bootstrap_btn.clicked.connect(self._bootstrap_dataset)
        self.cancel_bootstrap_btn = QPushButton("Cancel Bootstrap")
        self.cancel_bootstrap_btn.setEnabled(False)
        self.cancel_bootstrap_btn.clicked.connect(self._cancel_bootstrap)
        custom_btn = QPushButton("Build Custom Dataset")
        custom_btn.clicked.connect(self._custom_dataset)
        self.show_bootstrap_progress_btn = QPushButton("Show Bootstrap Progress")
        self.show_bootstrap_progress_btn.clicked.connect(self._show_bootstrap_progress)
        dataset_actions = QHBoxLayout(); dataset_actions.addWidget(auto_btn); dataset_actions.addWidget(self.bootstrap_btn); dataset_actions.addWidget(self.cancel_bootstrap_btn); dataset_actions.addWidget(custom_btn); dataset_actions.addWidget(self.show_bootstrap_progress_btn); dataset_actions.addStretch(1)
        self.target_size = QComboBox(); self.target_size.addItems(["Starter", "Big", "Huge"]); self.target_size.setCurrentText("Big")
        self.target_size.currentTextChanged.connect(self._refresh_bootstrap_estimate)
        self.auto_start_after_dataset = QCheckBox("Auto-start training after dataset ready")
        self.dataset_offline_mode = QCheckBox("Offline mode: use cached sources only")
        self.dataset_offline_mode.setChecked(bool(self._dataset_sources().get("offline_mode", False)))
        self.dataset_source_wiki_base = QLineEdit(self._get_provider_setting("wikipedia", "base_url", ""))
        self.dataset_source_wiki_version = QLineEdit(self._get_provider_setting("wikipedia", "version", "latest"))
        self.dataset_source_arxiv_base = QLineEdit(self._get_provider_setting("arxiv", "base_url", ""))
        self.dataset_source_arxiv_version = QLineEdit(self._get_provider_setting("arxiv", "version", ""))
        self.save_dataset_sources_btn = QPushButton("Save dataset source overrides")
        self.save_dataset_sources_btn.clicked.connect(self._save_dataset_source_settings)
        self.copy_bootstrap_diag_btn = QPushButton("Copy diagnostics")
        self.copy_bootstrap_diag_btn.clicked.connect(self._copy_bootstrap_diagnostics)
        self.upload_shards_to_da = QCheckBox("Upload shards to DA")
        self.upload_shards_to_da.setChecked(bool(self._cfg.ena.get("allow_remote_put", False)))
        self.upload_shards_to_da.setEnabled(bool(self._cfg.ena.get("allow_remote_put", False)))
        self.bootstrap_estimate = QLabel("")
        self.bootstrap_progress = QLabel("Bootstrap idle")
        self.base_model = QLineEdit("")
        self.output_dir = QLineEdit("./ena-training-runs")
        pick_out = QPushButton("Browse")
        pick_out.clicked.connect(self._pick_output)
        out_row = QHBoxLayout(); out_row.addWidget(self.output_dir, 1); out_row.addWidget(pick_out)

        self.iterations = QSpinBox(); self.iterations.setRange(0, 1_000_000_000); self.iterations.setValue(10000)
        self.epochs = QDoubleSpinBox(); self.epochs.setRange(0, 100000); self.epochs.setDecimals(2); self.epochs.setValue(0)
        self.batch_size = QSpinBox(); self.batch_size.setRange(1, 32768); self.batch_size.setValue(4)
        self.learning_rate = QDoubleSpinBox(); self.learning_rate.setDecimals(8); self.learning_rate.setRange(0.00000001, 10.0); self.learning_rate.setValue(0.00002)
        self.optimizer = QComboBox(); self.optimizer.addItems(["adamw", "adam", "sgd"])

        self.eval_interval = QSpinBox(); self.eval_interval.setRange(1, 10_000_000); self.eval_interval.setValue(100)
        self.ckpt_interval = QSpinBox(); self.ckpt_interval.setRange(1, 10_000_000); self.ckpt_interval.setValue(500)
        self.max_runtime = QSpinBox(); self.max_runtime.setRange(0, 100_000); self.max_runtime.setValue(0)
        self.early_stop = QSpinBox(); self.early_stop.setRange(0, 10_000); self.early_stop.setValue(0)

        self.device = QComboBox(); self.device.addItems(["auto", "cpu", "cuda"])
        self.gpu_id = QSpinBox(); self.gpu_id.setRange(-1, 64); self.gpu_id.setValue(-1)
        self.workers = QSpinBox(); self.workers.setRange(0, 256); self.workers.setValue(0)
        self.threads = QSpinBox(); self.threads.setRange(0, 256); self.threads.setValue(0)

        self.grad_accum = QSpinBox(); self.grad_accum.setRange(0, 1024); self.grad_accum.setValue(0)
        self.seed = QSpinBox(); self.seed.setRange(0, 2_147_483_647); self.seed.setValue(0)
        self.precision = QComboBox(); self.precision.addItems(["fp32", "fp16", "bf16"])
        self.smart_defaults = QCheckBox("Enable smart defaults")
        self.smart_defaults.setChecked(True)
        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(0, 3)
        self.quality_slider.setValue(1)
        self.quality_slider.setTickInterval(1)
        self.quality_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.quality_label = QLabel("Balanced")
        self.auto_config_btn = QPushButton("Auto-Configure Now")

        self.lora_enabled = QCheckBox("Enable LoRA")
        self.lora_rank = QSpinBox(); self.lora_rank.setRange(0, 2048); self.lora_rank.setValue(0)
        self.resume_ckpt = QComboBox(); self.resume_ckpt.addItem("(none)")
        self.refresh_ckpt_btn = QPushButton("Refresh checkpoints")
        self.refresh_ckpt_btn.clicked.connect(self._refresh_checkpoints)

        self.continuous_improvement = QCheckBox("Auto-evolve dataset and keep improving")
        self.max_dataset_disk = QDoubleSpinBox(); self.max_dataset_disk.setRange(1, 5000); self.max_dataset_disk.setValue(20)
        self.max_daily_download = QDoubleSpinBox(); self.max_daily_download.setRange(0.1, 5000); self.max_daily_download.setValue(2)
        self.max_daily_train = QDoubleSpinBox(); self.max_daily_train.setRange(0.1, 1000); self.max_daily_train.setValue(4)
        self.retain_versions = QSpinBox(); self.retain_versions.setRange(1, 200); self.retain_versions.setValue(3)
        self.preview_plan_btn = QPushButton("Preview next dataset plan")
        self.approve_plan_btn = QPushButton("Approve & apply")
        self.run_cycle_btn = QPushButton("Run next improvement cycle now")
        self.recommended = QTextEdit(); self.recommended.setReadOnly(True); self.recommended.setMaximumHeight(140)

        self.submit_aicf = QCheckBox("Submit checkpoints/metrics to AICF")
        self.budget_anm = QLineEdit("10")
        self.training_mode = QComboBox(); self.training_mode.addItems(["local", "remote"])
        self.training_mode_help = QLabel("Local: runs training on this machine via animica CLI, streams logs.\nRemote: submits a job to AICF/ENA services URL (requires preflight reachability).")
        self.training_mode_help.setWordWrap(True)
        self.mode_badge = QLabel("Mode: Local")
        self.backend_label = QLabel("Backend: Local (on this machine)")
        self.services_url = QLineEdit("")
        self.auto_fallback = QCheckBox("Auto fallback to local when remote unreachable")
        self.auto_fallback.setChecked(True)
        self.api_key = QLineEdit("")
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Run name", self.run_name)
        form.addRow("Dataset path", dataset_row)
        form.addRow("Dataset ID", self.dataset_id)
        form.addRow("Dataset mode", self.dataset_mode)
        form.addRow("Auto max docs", self.auto_docs)
        form.addRow("Auto max bytes", self.auto_bytes)
        form.addRow("Auto languages (csv)", self.auto_langs)
        form.addRow("Auto topics (csv)", self.auto_topics)
        form.addRow("Target size", self.target_size)
        form.addRow("Dataset source: Wikipedia base", self.dataset_source_wiki_base)
        form.addRow("Dataset source: Wikipedia version", self.dataset_source_wiki_version)
        form.addRow("Dataset source: arXiv base", self.dataset_source_arxiv_base)
        form.addRow("Dataset source: arXiv version/date", self.dataset_source_arxiv_version)
        form.addRow("Dataset sources", self.dataset_offline_mode)
        dataset_source_buttons = QHBoxLayout(); dataset_source_buttons.addWidget(self.save_dataset_sources_btn); dataset_source_buttons.addWidget(self.copy_bootstrap_diag_btn); dataset_source_buttons.addStretch(1)
        form.addRow("", dataset_source_buttons)
        form.addRow("Bootstrap estimate", self.bootstrap_estimate)
        form.addRow("Bootstrap progress", self.bootstrap_progress)
        form.addRow("", self.auto_start_after_dataset)
        form.addRow("", self.upload_shards_to_da)
        form.addRow("Dataset tools", dataset_actions)
        form.addRow("Base model/checkpoint", self.base_model)
        form.addRow("Output dir", out_row)
        form.addRow("Iterations (primary)", self.iterations)
        form.addRow("Epochs (optional)", self.epochs)
        form.addRow("Batch size", self.batch_size)
        form.addRow("Learning rate", self.learning_rate)
        form.addRow("Optimizer", self.optimizer)
        form.addRow("Eval interval (steps)", self.eval_interval)
        form.addRow("Checkpoint interval (steps)", self.ckpt_interval)
        form.addRow("Max runtime (minutes)", self.max_runtime)
        form.addRow("Early stop patience", self.early_stop)
        form.addRow("Device", self.device)
        form.addRow("GPU id (-1 auto)", self.gpu_id)
        form.addRow("Workers (0 auto)", self.workers)
        form.addRow("Threads (0 auto)", self.threads)
        form.addRow("Grad accumulation", self.grad_accum)
        form.addRow("Seed", self.seed)
        form.addRow("Precision", self.precision)
        form.addRow(self.smart_defaults)
        quality_row = QHBoxLayout(); quality_row.addWidget(self.quality_slider, 1); quality_row.addWidget(self.quality_label)
        form.addRow("Quality profile", quality_row)
        form.addRow("", self.auto_config_btn)
        form.addRow(self.lora_enabled)
        form.addRow("LoRA rank", self.lora_rank)
        resume_row = QHBoxLayout(); resume_row.addWidget(self.resume_ckpt, 1); resume_row.addWidget(self.refresh_ckpt_btn)
        form.addRow("Resume from checkpoint", resume_row)
        form.addRow(self.submit_aicf)
        form.addRow("Budget (ANM)", self.budget_anm)
        form.addRow("Mode", self.training_mode)
        form.addRow("", self.mode_badge)
        form.addRow("", self.training_mode_help)
        form.addRow("Backend", self.backend_label)
        form.addRow("Services URL", self.services_url)
        form.addRow("", self.auto_fallback)
        form.addRow("API key (optional)", self.api_key)
        form.addRow("Continuous Improvement", self.continuous_improvement)
        form.addRow("Max dataset disk (GiB)", self.max_dataset_disk)
        form.addRow("Max daily download (GiB)", self.max_daily_download)
        form.addRow("Max daily training time (hours)", self.max_daily_train)
        form.addRow("Retain last N versions", self.retain_versions)
        prow = QHBoxLayout(); prow.addWidget(self.preview_plan_btn); prow.addWidget(self.approve_plan_btn); prow.addWidget(self.run_cycle_btn); prow.addStretch(1)
        pwrap = QWidget(); pwrap.setLayout(prow)
        form.addRow("Improvement controls", pwrap)
        form.addRow("Recommended settings", self.recommended)

        root.addWidget(form_box)

        ctl = QHBoxLayout()
        self.start_btn = QPushButton("Start training (local)")
        self.stop_btn = QPushButton("Stop")
        self.resume_btn = QPushButton("Resume Watch")
        self.switch_local_btn = QPushButton("Switch to Local")
        self.copy_diag_btn = QPushButton("Copy diagnostics")
        self.stop_btn.setEnabled(False)
        ctl.addWidget(self.start_btn); ctl.addWidget(self.stop_btn); ctl.addWidget(self.resume_btn); ctl.addWidget(self.switch_local_btn); ctl.addWidget(self.copy_diag_btn)
        ctl.addStretch(1)
        root.addLayout(ctl)

        stats = QGridLayout()
        self.status_lbl = QLabel("idle")
        self.step_lbl = QLabel("step: -")
        self.loss_lbl = QLabel("loss: -")
        self.sps_lbl = QLabel("steps/sec: -")
        self.eval_lbl = QLabel("eval: -")
        stats.addWidget(self.status_lbl, 0, 0)
        stats.addWidget(self.step_lbl, 0, 1)
        stats.addWidget(self.loss_lbl, 1, 0)
        stats.addWidget(self.sps_lbl, 1, 1)
        stats.addWidget(self.eval_lbl, 2, 0, 1, 2)
        root.addLayout(stats)

        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0)
        root.addWidget(self.progress)

        self.runs_combo = QComboBox()
        load_run_btn = QPushButton("Load run")
        load_run_btn.clicked.connect(self._load_selected_run)
        run_row = QHBoxLayout(); run_row.addWidget(QLabel("Run history:")); run_row.addWidget(self.runs_combo, 1); run_row.addWidget(load_run_btn)
        root.addLayout(run_row)

        self.console = QTextEdit(); self.console.setReadOnly(True)
        self.console.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.console, 1)
        self._refresh_bootstrap_estimate()

    def _wire(self) -> None:
        self.start_btn.clicked.connect(self._start)
        self.training_mode.currentTextChanged.connect(lambda _v: self._refresh_backend_label())
        self.services_url.textChanged.connect(lambda _v: self._refresh_backend_label())
        self.stop_btn.clicked.connect(self._stop)
        self.resume_btn.clicked.connect(self._resume_watch)
        self.switch_local_btn.clicked.connect(self._switch_to_local)
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        self.quality_slider.valueChanged.connect(self._update_quality_label)
        self.auto_config_btn.clicked.connect(self._auto_configure)
        self.preview_plan_btn.clicked.connect(self._preview_next_dataset_plan)
        self.approve_plan_btn.clicked.connect(self._approve_plan)
        self.run_cycle_btn.clicked.connect(self._run_improvement_cycle)
        self.continuous_improvement.toggled.connect(lambda _checked: self._sync_ui_state())
        self.dataset_path.textChanged.connect(lambda _text: self._sync_ui_state())

        self._svc.log_line.connect(self._on_log)
        self._svc.metrics_updated.connect(self._on_metrics)
        self._svc.status_changed.connect(self._on_status)
        self._bootstrap_runtime.stateChanged.connect(self._on_bootstrap_state_changed)
        self._bootstrap_runtime.progressUpdated.connect(self._on_bootstrap_runtime_progress)
        self._bootstrap_runtime.logLine.connect(self._on_bootstrap_runtime_log)
        self._bootstrap_runtime.finished.connect(self._on_bootstrap_runtime_finished)

    def _ensure_ui_thread(self, fn, *args) -> bool:
        if assert_ui_thread():
            return True
        QTimer.singleShot(0, lambda: fn(*args))
        return False

    def _maybe_prompt_bootstrap(self) -> None:
        if not assert_ui_thread():
            QTimer.singleShot(0, self._maybe_prompt_bootstrap)
            return
        if self._bootstrap_prompted:
            return
        self._bootstrap_prompted = True
        if os.getenv("ANIMICA_STUDIO_SAFE_MODE", "").strip() == "1":
            return
        if self.dataset_path.text().strip():
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Bootstrap Big Dataset")
        msg.setText("No ENA dataset found. Bootstrap Big Dataset now?")
        starter_btn = msg.addButton("Starter", QMessageBox.ButtonRole.ActionRole)
        big_btn = msg.addButton("Big (recommended)", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == starter_btn:
            self.target_size.setCurrentText("Starter")
            self._bootstrap_dataset()
        elif clicked == big_btn:
            self.target_size.setCurrentText("Big")
            self._bootstrap_dataset()

    def _pick_dataset(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Dataset")
        if p:
            self.dataset_path.setText(p)

    def _pick_output(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Output directory")
        if p:
            self.output_dir.setText(p)
            self._refresh_checkpoints()

    def _auto_dataset(self) -> None:
        try:
            res = self._dataset_manager.build_auto_dataset(
                name=self.run_name.text().strip() or "ena",
                max_documents=self.auto_docs.value(),
                max_bytes=self.auto_bytes.value(),
                languages=[x.strip() for x in self.auto_langs.text().split(",") if x.strip()],
                topics=[x.strip() for x in self.auto_topics.text().split(",") if x.strip()],
            )
            self.dataset_path.setText(res["manifest_path"])
            self.dataset_id.setText(Path(res["dataset_dir"]).name)
            self.console.append(f"[dataset] Auto dataset ready: {res['manifest_path']}")
            self._sync_ui_state()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Dataset", str(exc))

    def _custom_dataset(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Select dataset file", "", "Data (*.jsonl *.txt);;All files (*)")
        if not p:
            return
        try:
            res = self._dataset_manager.build_custom_dataset([p], name=self.run_name.text().strip() or "ena")
            self.dataset_path.setText(res["manifest_path"])
            self.dataset_id.setText(Path(res["dataset_dir"]).name)
            self.console.append(f"[dataset] Custom dataset ready: {res['manifest_path']}")
            self._sync_ui_state()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Dataset", str(exc))

    def _target_key(self) -> str:
        return (self.target_size.currentText() or "Big").strip().lower()

    def _refresh_bootstrap_estimate(self) -> None:
        est = self._dataset_manager.estimate_bootstrap(self._target_key())
        disk_gb = est["disk_needed_bytes"] / 1024**3
        dl_gb = est["download_bytes"] / 1024**3
        eta = est.get("eta_hours_range") or [0, 0]
        self.bootstrap_estimate.setText(
            f"Disk ~{disk_gb:.1f} GB | Download ~{dl_gb:.1f} GB | ETA ~{eta[0]}-{eta[1]} h @ {est['bandwidth_mbps']:.1f} Mbps"
        )

    def _show_bootstrap_progress(self) -> None:
        if self._bootstrap_panel is None:
            panel = BootstrapProgressWidget(self)
            panel.pauseRequested.connect(self._bootstrap_runtime.pause)
            panel.resumeRequested.connect(lambda: self._bootstrap_runtime.resume(name=self.run_name.text().strip() or "ena"))
            panel.cancelRequested.connect(self._cancel_bootstrap)
            panel.retryRequested.connect(lambda: self._bootstrap_runtime.retry(name=self.run_name.text().strip() or "ena", size_preset=self._target_key()))
            panel.copyDiagnosticsRequested.connect(self._copy_bootstrap_diagnostics)
            self._bootstrap_panel = panel
        run = self._bootstrap_runtime.active_run
        if run:
            self._bootstrap_panel.update_state(run.state)
            self._bootstrap_panel.set_output_dir(run.output_dir)
            for line in run.log_lines[-200:]:
                self._bootstrap_panel.append_log(str(line.get("kind", "system")), str(line.get("text", "")))
        self._bootstrap_panel.show()
        self._bootstrap_panel.raise_()

    def _bootstrap_dataset(self) -> None:
        preset = self._target_key()
        est = self._dataset_manager.estimate_bootstrap(preset)
        free = shutil.disk_usage(str(Path.home())).free
        if free < est["disk_needed_bytes"]:
            QMessageBox.warning(self, "Dataset", "Insufficient disk for selected target. Try Starter.")
            return
        self.bootstrap_btn.setEnabled(False)
        self.cancel_bootstrap_btn.setEnabled(True)
        self.bootstrap_progress.setText("Bootstrapping dataset...")
        run = self._bootstrap_runtime.start(name=self.run_name.text().strip() or "ena", size_preset=preset)
        if self._bootstrap_panel:
            self._bootstrap_panel.set_output_dir(run.output_dir)
        self._show_bootstrap_progress()
        self._sync_ui_state()

    def _cancel_bootstrap(self) -> None:
        self._bootstrap_runtime.cancel()
        self.bootstrap_progress.setText("Cancelling bootstrap; partial data preserved for resume...")
        self._sync_ui_state()

    def _on_bootstrap_state_changed(self, state: str) -> None:
        if not self._ensure_ui_thread(self._on_bootstrap_state_changed, state):
            return
        self.bootstrap_progress.setText(f"Bootstrap: {state}")
        if self._bootstrap_panel:
            self._bootstrap_panel.update_state(state)

    def _on_bootstrap_runtime_progress(self, payload: dict) -> None:
        if not self._ensure_ui_thread(self._on_bootstrap_runtime_progress, payload):
            return
        processed = int(payload.get("processed_bytes") or 0)
        target = int(payload.get("target_bytes") or 1)
        pct = max(0, min(100, int(processed * 100 / max(1, target))))
        self.progress.setValue(pct)
        if self._bootstrap_panel:
            run = self._bootstrap_runtime.active_run
            self._bootstrap_panel.update_metrics(
                bytes_downloaded=int(payload.get("downloaded_bytes") or (run.bytes_downloaded if run else 0)),
                bytes_total=payload.get("download_total_bytes") or (run.bytes_total if run else None),
                bytes_processed=processed,
                target_bytes=target,
                shards=int(payload.get("shards") or (run.shards_count if run else 0)),
                output_bytes=processed,
            )

    def _on_bootstrap_runtime_log(self, kind: str, text: str) -> None:
        if not self._ensure_ui_thread(self._on_bootstrap_runtime_log, kind, text):
            return
        if self._bootstrap_panel:
            self._bootstrap_panel.append_log(kind, text)

    def _on_bootstrap_runtime_finished(self, ok: bool, result: dict) -> None:
        if not self._ensure_ui_thread(self._on_bootstrap_runtime_finished, ok, result):
            return
        self.bootstrap_btn.setEnabled(True)
        self.cancel_bootstrap_btn.setEnabled(False)
        if not ok:
            QMessageBox.warning(self, "Dataset bootstrap", str(result.get("error") or "Bootstrap failed"))
            return
        if result.get("cancelled"):
            return
        self.dataset_path.setText(result.get("manifest_path") or "")
        self.dataset_id.setText(Path(result.get("dataset_dir") or "").name)
        if self.auto_start_after_dataset.isChecked():
            self._start()
        self._sync_ui_state()

    def _dataset_sources(self) -> dict:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        ds = ena.get("dataset_sources") if isinstance(ena, dict) else {}
        if not isinstance(ds, dict):
            return {"offline_mode": False, "providers": {}}
        providers = ds.get("providers") if isinstance(ds.get("providers"), dict) else {}
        return {"offline_mode": bool(ds.get("offline_mode", False)), "providers": providers}

    def _get_provider_setting(self, provider: str, key: str, default: str) -> str:
        settings = self._dataset_sources()
        providers = settings.get("providers", {})
        p = providers.get(provider, {}) if isinstance(providers, dict) else {}
        if not isinstance(p, dict):
            return default
        return str(p.get(key, default) or default)

    def _save_dataset_source_settings(self) -> None:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        if not isinstance(ena, dict):
            ena = {}
        ena["dataset_sources"] = {
            "offline_mode": self.dataset_offline_mode.isChecked(),
            "providers": {
                "wikipedia": {
                    "base_url": self.dataset_source_wiki_base.text().strip(),
                    "version": self.dataset_source_wiki_version.text().strip() or "latest",
                },
                "arxiv": {
                    "base_url": self.dataset_source_arxiv_base.text().strip(),
                    "version": self.dataset_source_arxiv_version.text().strip(),
                },
            },
        }
        self._cfg.ena = ena
        save_config(self._cfg)
        self.console.append("[dataset] Saved source overrides.")

    def _copy_bootstrap_diagnostics(self) -> None:
        text = self._bootstrap_runtime.diagnostics_text()
        QGuiApplication.clipboard().setText(text)
        if self._bootstrap_panel:
            self._bootstrap_panel.append_log("system", "Diagnostics copied to clipboard.")

    def _apply_preset(self, name: str) -> None:
        p = self.PRESETS[name]
        self.iterations.setValue(int(p["iterations"]))
        self.batch_size.setValue(int(p["batch_size"]))
        self.learning_rate.setValue(float(p["learning_rate"]))


    def _refresh_backend_label(self) -> None:
        mode = (self.training_mode.currentText() or "local").lower()
        if mode == "remote":
            url = self.services_url.text().strip() or "<unset>"
            self.mode_badge.setText("Mode: Remote")
            self.backend_label.setText(f"Backend: Remote ({url})")
            self.start_btn.setText("Submit training job")
            self.budget_anm.setEnabled(True)
            self.services_url.setEnabled(True)
            self.api_key.setEnabled(True)
            self.auto_fallback.setEnabled(False)
            return
        self.mode_badge.setText("Mode: Local")
        self.backend_label.setText("Backend: Local (on this machine)")
        self.start_btn.setText("Start training (local)")
        self.budget_anm.setEnabled(False)
        self.services_url.setEnabled(False)
        self.api_key.setEnabled(False)
        self.auto_fallback.setEnabled(False)

    def _read_config(self) -> TrainingConfig:
        cfg = TrainingConfig(
            run_name=self.run_name.text().strip() or "ena-train",
            total_steps=self.iterations.value() or None,
            iterations=self.iterations.value() or None,
            epochs=self.epochs.value() if self.epochs.value() > 0 else None,
            batch_size=self.batch_size.value(),
            learning_rate=self.learning_rate.value(),
            optimizer=self.optimizer.currentText(),
            dataset_path=self.dataset_path.text().strip(),
            dataset_id=self.dataset_id.text().strip() or None,
            base_model=self.base_model.text().strip(),
            output_dir=self.output_dir.text().strip() or "./ena-training-runs",
            eval_interval_steps=self.eval_interval.value(),
            checkpoint_interval_steps=self.ckpt_interval.value(),
            max_runtime_minutes=self.max_runtime.value() or None,
            early_stop_patience=self.early_stop.value() or None,
            device=self.device.currentText(),
            gpu_id=(None if self.gpu_id.value() < 0 else self.gpu_id.value()),
            num_workers=self.workers.value() or None,
            threads=self.threads.value() or None,
            gradient_accumulation_steps=self.grad_accum.value() or None,
            seed=self.seed.value() or None,
            precision=self.precision.currentText(),
            lora_enabled=self.lora_enabled.isChecked(),
            lora_rank=self.lora_rank.value() or None,
            resume_checkpoint=(None if self.resume_ckpt.currentIndex() <= 0 else self.resume_ckpt.currentData(Qt.ItemDataRole.UserRole)),
            submit_to_aicf=self.submit_aicf.isChecked(),
            budget_anm=self.budget_anm.text().strip() or "10",
            training_mode=self.training_mode.currentText(),
            services_url=self.services_url.text().strip(),
            api_key=self.api_key.text().strip(),
            quality_level=self._quality_level(),
            smart_defaults=self.smart_defaults.isChecked(),
        )
        if cfg.effective_iterations() and cfg.epochs:
            self._on_log("", "system", "Both iterations and epochs set; iterations/total_steps takes precedence.")
        if cfg.effective_iterations() and cfg.effective_iterations() >= 100_000_000:
            self._on_log("", "system", "Warning: extremely high iteration count configured.")
        return cfg

    def _start(self) -> None:
        try:
            cfg = self._read_config()
            if cfg.smart_defaults:
                cfg = self._svc.build_auto_recommendation(cfg, cfg.quality_level)
                self._render_recommendation(cfg)
            self._cfg.ena["job_backend"] = (cfg.training_mode or "local").lower()
            self._cfg.ena["services_url"] = cfg.services_url
            self._cfg.ena["remote_api_key"] = cfg.api_key
            self._cfg.ena["auto_fallback"] = self.auto_fallback.isChecked()
            save_config(self._cfg)
            self._check_output_conflict(cfg)
            run_id = self._svc.start_training(cfg)
            self._active_run_id = run_id
            self.console.clear()
            self._on_log(run_id, "system", f"Started run {run_id}")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self._refresh_runs()
            self._sync_ui_state()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Training", str(exc))

    def _check_output_conflict(self, cfg: TrainingConfig) -> None:
        out = Path(cfg.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        existing = [p for p in out.iterdir()]
        if not existing:
            return
        choice = QMessageBox.question(
            self,
            "Output directory not empty",
            "Output directory contains files. Continue (resume) with current directory?\n"
            "Choose No to create a new timestamped run directory.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            raise RuntimeError("Cancelled by user")
        if choice == QMessageBox.StandardButton.No:
            cfg.output_dir = str(out / f"run-{int(__import__('time').time())}")

    def _stop(self) -> None:
        if not self._active_run_id:
            return
        self._svc.stop_training(self._active_run_id)
        self.stop_btn.setEnabled(False)
        self.start_btn.setEnabled(True)
        self._sync_ui_state()

    def _resume_watch(self) -> None:
        run_id = self._active_run_id or self.runs_combo.currentData(Qt.ItemDataRole.UserRole)
        if not run_id:
            return
        try:
            self._svc.resume_training(str(run_id))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(self, "Resume", str(exc))

    def _on_log(self, run_id: str, tag: str, text: str) -> None:
        if not self._ensure_ui_thread(self._on_log, run_id, tag, text):
            return
        show_id = run_id or (self._active_run_id or "-")
        self.console.append(f"[{tag}] ({show_id}) {text}")

    def _on_metrics(self, run_id: str, metrics: dict) -> None:
        if not self._ensure_ui_thread(self._on_metrics, run_id, metrics):
            return
        if self._active_run_id and run_id != self._active_run_id:
            return
        step = metrics.get("current_step")
        loss = metrics.get("loss")
        sps = metrics.get("steps_per_sec")
        eval_metrics = metrics.get("eval_metrics") or {}
        pct = metrics.get("progress_percent")
        total = metrics.get("total_steps")

        if step is not None:
            self.step_lbl.setText(f"step: {step}{'/' + str(total) if total else ''}")
        if loss is not None:
            self.loss_lbl.setText(f"loss: {loss:.6f}")
        if sps is not None:
            self.sps_lbl.setText(f"steps/sec: {sps:.3f}")
        if eval_metrics:
            self.eval_lbl.setText("eval: " + ", ".join(f"{k}={v:.4f}" for k, v in eval_metrics.items()))
        if pct is not None:
            self.progress.setValue(max(0, min(100, int(pct))))
        elif total and step is not None and total > 0:
            self.progress.setValue(int(step * 100 / total))

    def _on_status(self, run_id: str, status: str) -> None:
        if not self._ensure_ui_thread(self._on_status, run_id, status):
            return
        if self._active_run_id and run_id != self._active_run_id:
            return
        self.status_lbl.setText(status)
        if status in {"completed", "failed", "stopped"}:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._refresh_runs()
            self._sync_ui_state()

    def _switch_to_local(self) -> None:
        self.training_mode.setCurrentText("local")
        self._on_log("", "system", "Switched ENA training mode to local.")
        self._refresh_backend_label()

    def _copy_diagnostics(self) -> None:
        run_id = self._active_run_id or self.runs_combo.currentData(Qt.ItemDataRole.UserRole)
        payload = self._svc.build_diagnostics(str(run_id) if run_id else None)
        QGuiApplication.clipboard().setText(json.dumps(payload, indent=2))
        self._on_log("", "system", "Diagnostics copied to clipboard.")

    def _refresh_runs(self) -> None:
        self.runs_combo.clear()
        for run in self._svc.list_runs():
            text = f"{run.run_id} [{run.status}]"
            self.runs_combo.addItem(text, run.run_id)

    def _load_selected_run(self) -> None:
        run_id = self.runs_combo.currentData(Qt.ItemDataRole.UserRole)
        if not run_id:
            return
        run = self._svc.status(str(run_id))
        if not run:
            return
        self._active_run_id = run.run_id
        self.status_lbl.setText(run.status)
        self.console.setPlainText(json.dumps(run.config, indent=2))
        if run.last_metrics:
            self._on_metrics(run.run_id, run.last_metrics)
        self._sync_ui_state()

    def _refresh_checkpoints(self) -> None:
        self.resume_ckpt.clear()
        self.resume_ckpt.addItem("(none)")
        out = Path(self.output_dir.text().strip() or "./ena-training-runs").expanduser()
        if not out.exists():
            return
        for p in sorted(out.rglob("*.ckpt*")):
            self.resume_ckpt.addItem(str(p.name), str(p))

    def _quality_level(self) -> str:
        return {0: "fast", 1: "balanced", 2: "quality", 3: "max_quality"}.get(self.quality_slider.value(), "balanced")

    def _update_quality_label(self) -> None:
        self.quality_label.setText(self._quality_level().replace("_", " ").title())

    def _auto_configure(self) -> None:
        try:
            cfg = self._read_config()
            cfg = self._svc.build_auto_recommendation(cfg, self._quality_level())
            self._apply_training_config(cfg)
            self._render_recommendation(cfg)
            self._on_log("", "system", "Auto-configure applied smart defaults.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Auto-Configure", str(exc))

    def _render_recommendation(self, cfg: TrainingConfig) -> None:
        self.recommended.setPlainText(
            "\n".join(
                [
                    f"total_steps={cfg.effective_iterations()} batch={cfg.batch_size} grad_accum={cfg.gradient_accumulation_steps}",
                    f"lr={cfg.learning_rate:.2e} optimizer={cfg.optimizer} warmup={cfg.warmup_steps}",
                    f"eval every {cfg.eval_interval_steps} steps, checkpoint every {cfg.checkpoint_interval_steps} steps",
                    f"device={cfg.device} threads={cfg.threads} precision={cfg.precision}",
                    f"est runtime={cfg.estimated_runtime_minutes} min, memory risk={cfg.memory_risk}",
                    f"why: {cfg.auto_config_rationale}",
                ]
            )
        )

    def _apply_training_config(self, cfg: TrainingConfig) -> None:
        self.iterations.setValue(int(cfg.effective_iterations() or 0))
        self.batch_size.setValue(int(cfg.batch_size))
        self.grad_accum.setValue(int(cfg.gradient_accumulation_steps or 0))
        self.learning_rate.setValue(float(cfg.learning_rate))
        self.optimizer.setCurrentText(cfg.optimizer)
        self.eval_interval.setValue(int(cfg.eval_interval_steps))
        self.ckpt_interval.setValue(int(cfg.checkpoint_interval_steps))
        self.device.setCurrentText(cfg.device)
        self.threads.setValue(int(cfg.threads or 0))
        self.workers.setValue(int(cfg.num_workers or 0))
        self.precision.setCurrentText(cfg.precision)

    def _evolution_quotas(self) -> EvolutionQuotas:
        return EvolutionQuotas(
            max_dataset_disk_gib=float(self.max_dataset_disk.value()),
            max_daily_download_gib=float(self.max_daily_download.value()),
            max_daily_training_hours=float(self.max_daily_train.value()),
            retain_last_versions=int(self.retain_versions.value()),
        )

    def _preview_next_dataset_plan(self) -> None:
        if not self._evolution_enabled:
            QMessageBox.information(self, "Continuous Improvement", "Dataset evolution is disabled.")
            self._sync_ui_state()
            return
        try:
            run_id = self._active_run_id or self.runs_combo.currentData(Qt.ItemDataRole.UserRole)
            report = None
            if run_id:
                cfg = self._read_config()
                rp = Path(cfg.output_dir).expanduser() / str(run_id) / "run_report.json"
                if rp.exists():
                    report = json.loads(rp.read_text(encoding="utf-8"))
            self._last_plan = self._evolution.preview_next_plan(report, self._evolution_quotas(), self._quality_level())
            self._plan_approved = False
            self._last_plan_id = self._last_plan.get("plan_id") if isinstance(self._last_plan, dict) else None
            if not self._last_plan:
                QMessageBox.information(self, "Continuous Improvement", "No plan could be generated.")
                return
            self.recommended.append("\nNext Dataset Plan:\n" + json.dumps(self._last_plan, indent=2))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Continuous Improvement", str(exc))
        finally:
            self._sync_ui_state()

    def _approve_plan(self) -> None:
        if self._last_plan is None:
            QMessageBox.information(self, "Continuous Improvement", "No plan to approve. Generate a plan first.")
            return
        self._plan_approved = True
        self._pending_actions["approved_plan_id"] = str(self._last_plan_id or "")
        self._on_log("", "system", "Plan approved for dataset evolution.")
        self._sync_ui_state()

    def _run_improvement_cycle(self) -> None:
        if self._run_in_progress:
            QMessageBox.information(self, "Continuous Improvement", "Run already in progress.")
            return
        if self._last_plan is None:
            QMessageBox.information(self, "Continuous Improvement", "No plan available. Generate a plan first.")
            return
        if not self._plan_approved:
            QMessageBox.information(self, "Continuous Improvement", "Approve the plan first.")
            return
        if not self._evolution_enabled:
            QMessageBox.information(self, "Continuous Improvement", "Dataset evolution is disabled.")
            self._sync_ui_state()
            return

        self._run_in_progress = True
        self._sync_ui_state()
        try:
            ds = self._evolution.apply_plan(self._last_plan, self._evolution_quotas(), self.run_name.text().strip() or "ena")
            self.dataset_path.setText(ds["manifest_path"])
            self.dataset_id.setText(ds.get("dataset_id") or "")
            self._plan_approved = False
            self._last_plan = None
            self._last_plan_id = None
            self._on_log("", "system", f"Built evolved dataset {self.dataset_id.text()} and updated configuration.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Continuous Improvement", str(exc))
        finally:
            self._run_in_progress = False
            self._sync_ui_state()

    def _sync_ui_state(self) -> None:
        has_plan = self._last_plan is not None
        has_dataset = bool(self.dataset_path.text().strip())
        evolution_enabled = bool(self._evolution_enabled)
        busy = bool(self._run_in_progress)

        self.preview_plan_btn.setEnabled(evolution_enabled and has_dataset and not busy)
        self.approve_plan_btn.setEnabled(evolution_enabled and has_plan and not self._plan_approved and not busy)
        self.run_cycle_btn.setEnabled(
            evolution_enabled and has_plan and self._plan_approved and self.continuous_improvement.isChecked() and not busy
        )

        for widget in (
            self.continuous_improvement,
            self.max_dataset_disk,
            self.max_daily_download,
            self.max_daily_train,
            self.retain_versions,
            self.preview_plan_btn,
            self.approve_plan_btn,
            self.run_cycle_btn,
        ):
            widget.setVisible(evolution_enabled)

    def _restore_last(self) -> None:
        cfg = self._svc.last_config()
        self.run_name.setText(cfg.run_name)
        self.dataset_path.setText(cfg.dataset_path)
        self.dataset_id.setText(cfg.dataset_id or "")
        self.base_model.setText(cfg.base_model)
        self.output_dir.setText(cfg.output_dir)
        self.iterations.setValue(int(cfg.effective_iterations() or 0))
        self.epochs.setValue(float(cfg.epochs or 0))
        self.batch_size.setValue(cfg.batch_size)
        self.learning_rate.setValue(cfg.learning_rate)
        self.optimizer.setCurrentText(cfg.optimizer)
        self.eval_interval.setValue(cfg.eval_interval_steps)
        self.ckpt_interval.setValue(cfg.checkpoint_interval_steps)
        self.max_runtime.setValue(int(cfg.max_runtime_minutes or 0))
        self.early_stop.setValue(int(cfg.early_stop_patience or 0))
        self.device.setCurrentText(cfg.device)
        self.gpu_id.setValue(cfg.gpu_id if cfg.gpu_id is not None else -1)
        self.workers.setValue(int(cfg.num_workers or 0))
        self.threads.setValue(int(cfg.threads or 0))
        self.grad_accum.setValue(int(cfg.gradient_accumulation_steps or 0))
        self.seed.setValue(int(cfg.seed or 0))
        self.precision.setCurrentText(cfg.precision)
        self.lora_enabled.setChecked(cfg.lora_enabled)
        self.lora_rank.setValue(int(cfg.lora_rank or 0))
        self.submit_aicf.setChecked(cfg.submit_to_aicf)
        self.budget_anm.setText(cfg.budget_anm)
        self.training_mode.setCurrentText(cfg.training_mode or "local")
        self.services_url.setText(cfg.services_url or "")
        self.api_key.setText(cfg.api_key or "")
        self.smart_defaults.setChecked(bool(cfg.smart_defaults))
        self.quality_slider.setValue({"fast":0,"balanced":1,"quality":2,"max_quality":3}.get((cfg.quality_level or "balanced").lower(),1))
        self._update_quality_label()
        self.auto_fallback.setChecked(bool(self._cfg.ena.get("auto_fallback", True)))
        self._refresh_backend_label()
        self._refresh_checkpoints()
