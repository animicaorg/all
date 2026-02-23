from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from animica_studio.services.ena_contribution_engine import EnaContributionConfig, EnaContributionEngine
from animica_studio.storage.config import Config, save_config


class EnaContributePage(QWidget):
    def __init__(self, config: Config, engine: EnaContributionEngine, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._engine = engine
        self._build_ui()
        self._load_settings()
        self._engine.stateChanged.connect(self._on_state)
        self._engine.metricsUpdated.connect(self._on_metrics)
        self._engine.logLine.connect(self._on_log)
        self._engine.lastErrorChanged.connect(self._on_error)
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._refresh_uptime)
        self._uptime_timer.start(1000)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel("ENA Auto-Contribute CPU"))

        settings = QGroupBox("Controls")
        form = QFormLayout(settings)
        self._enabled = QCheckBox("Auto-Contribute CPU (always on)")
        self._enabled.toggled.connect(self._save_settings)
        form.addRow("", self._enabled)

        self._intensity = QComboBox()
        for value in ["low", "medium", "high", "max"]:
            self._intensity.addItem(value.capitalize(), value)
        self._intensity.currentIndexChanged.connect(self._save_settings)
        form.addRow("Intensity:", self._intensity)

        self._mode = QComboBox()
        self._mode.addItem("local", "local")
        self._mode.addItem("remote", "remote")
        self._mode.addItem("rpc", "rpc")
        self._mode.currentIndexChanged.connect(self._save_settings)
        form.addRow("Mode:", self._mode)

        self._services_url = QLineEdit()
        self._services_url.setPlaceholderText("https://ena.animica.org")
        self._backend = QLabel("Backend: Local (on this machine)")
        form.addRow("Backend:", self._backend)
        self._services_url.editingFinished.connect(self._save_settings)
        form.addRow("Services URL:", self._services_url)

        self._autostart = QCheckBox("Auto-start on launch")
        self._autostart.toggled.connect(self._save_settings)
        form.addRow("", self._autostart)

        row = QHBoxLayout()
        self._start = QPushButton("Start")
        self._start.clicked.connect(self._on_start)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self._engine.stop)
        self._test = QPushButton("Test connection")
        self._switch_local = QPushButton("Switch to local")
        self._switch_local.clicked.connect(self._switch_to_local)
        self._test.clicked.connect(self._on_test)
        self._copy = QPushButton("Copy diagnostics")
        self._copy.clicked.connect(self._copy_diag)
        self._view_logs = QPushButton("View last logs")
        self._view_logs.clicked.connect(lambda: self._logs.ensureCursorVisible())
        for b in [self._start, self._stop, self._test, self._switch_local, self._copy, self._view_logs]:
            row.addWidget(b)
        form.addRow("", row)
        root.addWidget(settings)

        self._status = QLabel("Status: idle")
        self._error = QLabel("")
        root.addWidget(self._status)
        root.addWidget(self._error)

        self._metrics = QLabel("")
        root.addWidget(self._metrics)

        self._logs = QTextEdit()
        self._logs.setReadOnly(True)
        root.addWidget(self._logs, 1)

    def _load_settings(self) -> None:
        raw = self._config.ena.get("ena_contrib") if isinstance(self._config.ena, dict) else None
        if not isinstance(raw, dict):
            raw = {}
        self._enabled.setChecked(bool(raw.get("enabled", False)))
        self._autostart.setChecked(bool(raw.get("auto_start", False)))
        self._services_url.setText(str(raw.get("services_url") or raw.get("aicf_services_url") or ""))
        self._set_combo(self._mode, str(raw.get("mode") or "local"))
        self._refresh_backend()
        self._set_combo(self._intensity, str(raw.get("intensity") or "medium"))

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        combo.setCurrentIndex(max(0, idx))

    def _save_settings(self) -> None:
        ena_cfg = dict(self._config.ena or {})
        ena_contrib = dict(ena_cfg.get("ena_contrib") or {})
        ena_contrib.update(
            {
                "enabled": self._enabled.isChecked(),
                "intensity": self._intensity.currentData(),
                "mode": self._mode.currentData(),
                "services_url": self._services_url.text().strip(),
                "auto_start": self._autostart.isChecked(),
            }
        )
        ena_cfg["ena_contrib"] = ena_contrib
        self._config.ena = ena_cfg
        save_config(self._config)
        self._refresh_backend()
        self._engine.apply_config(
            EnaContributionConfig(
                enabled=bool(ena_contrib.get("enabled", False)),
                intensity=str(ena_contrib.get("intensity") or "medium"),
                mode=str(ena_contrib.get("mode") or "local"),
                services_url=str(ena_contrib.get("services_url") or ""),
                auto_start=bool(ena_contrib.get("auto_start", False)),
                rpc_url=self._config.get_active_profile().node.rpc_local_url,
            )
        )


    def _switch_to_local(self) -> None:
        self._mode.setCurrentIndex(max(0, self._mode.findData("local")))
        self._save_settings()
        self._logs.append("[system] Switched to local backend")

    def _refresh_backend(self) -> None:
        mode = str(self._mode.currentData() or "local")
        if mode == "remote":
            self._backend.setText(f"Backend: Remote ({self._services_url.text().strip() or '<unset>'})")
        elif mode == "rpc":
            self._backend.setText("Backend: RPC queue")
        else:
            self._backend.setText("Backend: Local (on this machine)")

    def _on_start(self) -> None:
        self._save_settings()
        self._engine.start()

    def _on_test(self) -> None:
        self._save_settings()
        ok, msg = self._engine.test_connection()
        self._status.setText(f"Status: {'Running' if ok else 'Idle'} — {msg}")

    def _on_state(self, state: str) -> None:
        self._status.setText(f"Status: {state}")

    def _on_metrics(self, metrics) -> None:
        uptime = "-"
        if metrics.running_since > 0:
            uptime = f"{int(time.time() - metrics.running_since)}s"
        self._metrics.setText(
            f"Job: {metrics.current_job_id or '-'} | Uptime: {uptime} | Threads: {metrics.cpu_threads_in_use} | "
            f"Jobs ok/fail: {metrics.jobs_completed}/{metrics.jobs_failed} | Submissions ok/fail: {metrics.submissions_ok}/{metrics.submissions_failed} | "
            f"Credits: {metrics.credits_earned:.2f} | Last submit: {int(metrics.last_submit_time) if metrics.last_submit_time else '-'} | Backoff: {metrics.backoff_seconds}s"
        )

    def _refresh_uptime(self) -> None:
        self._on_metrics(self._engine.metrics)

    def _on_log(self, kind: str, text: str) -> None:
        self._logs.append(f"[{kind}] {text}")

    def _on_error(self, msg: str) -> None:
        self._error.setText(msg)

    def _copy_diag(self) -> None:
        text = self._engine.copy_diagnostics()
        QGuiApplication.clipboard().setText(text)
        self._logs.append("[system] Diagnostics copied to clipboard")
