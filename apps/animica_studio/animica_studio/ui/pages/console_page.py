"""Console page — full-featured CLI command runner with presets, history, and node controls."""
from __future__ import annotations

import logging
import shlex

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.console_models import CommandPreset
from animica_studio.services.console_service import ConsoleService
from animica_studio.services.job_runner import JobHandle, JobRunner
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.qt import qalive

log = logging.getLogger(__name__)

_HISTORY_LIMIT = 200


class _HistoryLineEdit(QLineEdit):
    """A QLineEdit that supports Up/Down arrow history navigation."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._history: list[str] = []
        self._history_idx: int = -1
        self._draft: str = ""

    def set_history(self, history: list[str]) -> None:
        self._history = history
        self._history_idx = -1

    def keyPressEvent(self, event):  # type: ignore[override]
        from PySide6.QtCore import Qt as _Qt  # noqa: PLC0415
        key = event.key()
        if key == _Qt.Key.Key_Up:
            if not self._history:
                return
            if self._history_idx == -1:
                self._draft = self.text()
            new_idx = min(self._history_idx + 1, len(self._history) - 1)
            if new_idx != self._history_idx:
                self._history_idx = new_idx
                self.setText(self._history[self._history_idx])
            return
        if key == _Qt.Key.Key_Down:
            if self._history_idx <= 0:
                self._history_idx = -1
                self.setText(self._draft)
                return
            self._history_idx -= 1
            self.setText(self._history[self._history_idx])
            return
        self._history_idx = -1
        super().keyPressEvent(event)


class ConsolePage(QWidget):
    """Full-featured console page with presets, history, node controls, and streaming output."""

    def __init__(self, config: Config | None = None, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._svc = ConsoleService(config=config)
        self._runner = JobRunner.instance()
        self._worker: "JobHandle | None" = None
        self._node_worker: "JobHandle | None" = None
        self._node_job_id: str | None = None
        self._active_job_id: str | None = None

        self._build_ui()
        self._refresh_presets()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_profile_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_presets_panel())
        splitter.addWidget(self._build_center_panel())
        splitter.addWidget(self._build_node_panel())
        splitter.setSizes([200, 600, 200])
        root.addWidget(splitter, stretch=1)

    def _build_profile_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.addItem("(Active profile)", "active")
        row.addWidget(self._profile_combo)
        row.addStretch()
        return bar

    def _build_presets_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(160)
        panel.setMaximumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("📋 Presets"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self._presets_layout = QVBoxLayout(inner)
        self._presets_layout.setContentsMargins(0, 0, 0, 0)
        self._presets_layout.setSpacing(2)
        self._presets_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, stretch=1)
        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(QLabel("🖥️  Output"))
        self._stream = StreamConsole()
        self._stream.stopped.connect(self._on_cancel)
        layout.addWidget(self._stream, stretch=1)

        layout.addWidget(self._build_input_bar())
        return panel

    def _build_input_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        prefix = QLabel("animica")
        prefix.setObjectName("headerMeta")
        row.addWidget(prefix)

        self._cmd_edit = _HistoryLineEdit()
        self._cmd_edit.setPlaceholderText("node status  (↑↓ history)")
        self._cmd_edit.returnPressed.connect(self._on_run)
        row.addWidget(self._cmd_edit, stretch=1)

        run_btn = QPushButton("▶ Run")
        run_btn.setObjectName("primaryButton")
        run_btn.clicked.connect(self._on_run)
        row.addWidget(run_btn)

        self._stop_btn = QPushButton("⏹ Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_cancel)
        row.addWidget(self._stop_btn)

        return bar

    def _build_node_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(160)
        panel.setMaximumWidth(220)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("🖥️  Node"))

        box = QGroupBox("Local Node")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(4)

        self._node_status_label = QLabel("Status: Unknown")
        self._node_status_label.setWordWrap(True)
        box_layout.addWidget(self._node_status_label)

        start_btn = QPushButton("▶ Start")
        start_btn.clicked.connect(self._on_node_start)
        box_layout.addWidget(start_btn)

        stop_btn = QPushButton("⏹ Stop")
        stop_btn.clicked.connect(self._on_node_stop)
        box_layout.addWidget(stop_btn)

        restart_btn = QPushButton("🔄 Restart")
        restart_btn.clicked.connect(self._on_node_restart)
        box_layout.addWidget(restart_btn)

        status_btn = QPushButton("🔍 Refresh")
        status_btn.clicked.connect(self._on_node_refresh)
        box_layout.addWidget(status_btn)

        layout.addWidget(box)
        layout.addStretch()

        self._poll_toggle = QCheckBox("Enable polling")
        self._poll_toggle.setChecked(False)
        self._poll_toggle.toggled.connect(self._on_poll_toggle)
        box_layout.addWidget(self._poll_toggle)

        self._node_timer = QTimer(self)
        self._node_timer.timeout.connect(self._on_node_refresh)
        QTimer.singleShot(1500, self._on_node_refresh)

        return panel

    def _on_poll_toggle(self, enabled: bool) -> None:
        if enabled:
            self._node_timer.start(60_000)
            self._stream.append_system("Node polling enabled (60s interval).")
            self._on_node_refresh()
        else:
            self._node_timer.stop()
            self._stream.append_system("Node polling paused.")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._poll_toggle.isChecked():
            self._node_timer.start(60_000)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        if qalive(self._node_timer):
            self._node_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------

    def _refresh_presets(self) -> None:
        # Clear existing buttons (except the trailing stretch)
        while self._presets_layout.count() > 1:
            item = self._presets_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        presets = self._svc.get_presets()
        groups: dict[str, list[CommandPreset]] = {}
        for p in presets:
            groups.setdefault(p.group, []).append(p)

        for group_name, group_presets in groups.items():
            box = QGroupBox(group_name)
            box.setCheckable(True)
            box.setChecked(True)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(4, 4, 4, 4)
            box_layout.setSpacing(2)
            for preset in group_presets:
                btn = QPushButton(preset.label)
                btn.setFlat(True)
                btn.setToolTip(" ".join(preset.argv))
                btn.clicked.connect(lambda _c, p=preset: self._on_preset_clicked(p))
                box_layout.addWidget(btn)
            self._presets_layout.insertWidget(self._presets_layout.count() - 1, box)

    def _on_preset_clicked(self, preset: CommandPreset) -> None:
        if preset.dangerous and preset.confirm:
            reply = QMessageBox.question(
                self,
                "Confirm",
                f"Run: {' '.join(preset.argv)}\nThis action may be irreversible. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._stream.clear()
        self._run_argv(preset.argv)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        raw = self._cmd_edit.text().strip()
        if not raw:
            return

        # Build argv: prepend "animica" if not already present
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        if parts and parts[0] != "animica":
            parts = ["animica"] + parts

        self._stream.clear()
        self._cmd_edit.clear()
        self._run_argv(parts)

    def _run_argv(self, argv: list[str]) -> None:
        if self._active_job_id is not None:
            self._stream.append_error("A command is already running.")
            return

        self._stream.set_cancel_token(None)
        self._stream.set_running(True)
        self._stream.append_system(f"Running: {' '.join(argv)}")
        self._stop_btn.setEnabled(True)

        self._svc.push_history(" ".join(argv))
        self._worker = self._runner.run_cli(argv, timeout_s=120)
        self._active_job_id = self._worker.job_id
        self._worker.started.connect(lambda _jid: self._stream.set_running(True))
        self._worker.output.connect(self._on_output)
        self._worker.error.connect(self._on_job_error)
        self._worker.finished.connect(self._on_job_finished)

        self._cmd_edit.set_history(self._svc.get_history())

    def _on_job_finished(self, job_id: str, exit_code: int, _payload: object) -> None:
        if job_id != self._active_job_id:
            return
        self._stream.set_exit_status(exit_code, 0, cancelled=(exit_code == 143))
        self._active_job_id = None
        self._worker = None
        self._stop_btn.setEnabled(False)
        self._stream.set_running(False)

    def _on_output(self, job_id: str, stream: str, text: str) -> None:
        if job_id != self._active_job_id:
            return
        self._stream.append_line(stream, text)

    def _on_job_error(self, job_id: str, message: str, _details: str) -> None:
        if job_id != self._active_job_id:
            return
        self._stream.append_error(message)

    def _on_cancel(self) -> None:
        if self._active_job_id:
            self._runner.cancel(self._active_job_id)

    # ------------------------------------------------------------------
    # Node control
    # ------------------------------------------------------------------

    def _on_node_refresh(self) -> None:
        self._run_node_op("status")

    def _on_node_start(self) -> None:
        self._run_node_op("start")

    def _on_node_stop(self) -> None:
        self._run_node_op("stop")

    def _on_node_restart(self) -> None:
        self._run_node_op("restart")

    def _run_node_op(self, op: str) -> None:
        if self._node_job_id is not None:
            self._stream.append_system(f"Node operation already running: {op}")
            return
        self._stream.append_system(f"Running node {op}…")
        self._node_worker = self._runner.run_cli(["animica", "node", op], timeout_s=45)
        self._node_job_id = self._node_worker.job_id
        self._node_worker.output.connect(self._on_node_output)
        self._node_worker.error.connect(lambda _j, msg, _d: (self._node_status_label.setText(f"Error: {msg[:80]}"), self._stream.append_error(msg)))
        self._node_worker.finished.connect(self._on_node_finished)

    def _on_node_output(self, job_id: str, stream: str, text: str) -> None:
        if job_id != self._node_job_id:
            return
        if stream == "stdout" and "running" in text.lower():
            self._node_status_label.setText("🟢 Running")
        elif stream == "stdout" and "stopped" in text.lower():
            self._node_status_label.setText("🔴 Stopped")

    def _on_node_finished(self, job_id: str, _exit_code: int, _payload: object) -> None:
        if job_id != self._node_job_id:
            return
        self._node_worker = None
        self._node_job_id = None

    def _stop_node_worker(self) -> None:
        worker = self._node_worker
        node_job_id = self._node_job_id
        self._node_worker = None
        self._node_job_id = None
        if node_job_id:
            self._runner.cancel(node_job_id)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if qalive(self._node_timer):
            self._node_timer.stop()
        self._stop_node_worker()
        if self._active_job_id:
            self._runner.cancel(self._active_job_id)
        self._worker = None
        super().closeEvent(event)

    def _on_node_result(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        running = result.get("running", False)
        pid = result.get("pid")
        rpc = result.get("rpc_reachable", False)
        if running:
            pid_str = f" PID:{pid}" if pid else ""
            rpc_str = " RPC:✅" if rpc else " RPC:❌"
            self._node_status_label.setText(f"🟢 Running{pid_str}{rpc_str}")
        else:
            self._node_status_label.setText("🔴 Stopped")
