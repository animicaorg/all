"""Node page — Start/Stop/Restart/Status controls with log tail."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.util.qt import qthread_running, stop_thread

log = logging.getLogger(__name__)


class NodePage(QWidget):
    """Local node management: start, stop, restart, status, and log tail."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._worker_thread: WorkerThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("🖥️  Local Node")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("▶  Start", self._on_start),
            ("⏹  Stop", self._on_stop),
            ("↺  Restart", self._on_restart),
            ("ℹ  Status", self._on_status),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel("Status: unknown")
        layout.addWidget(self._status_label)

        layout.addWidget(QLabel("Last log lines:"))
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        layout.addWidget(self._log_view, stretch=1)

    def _make_manager(self):  # type: ignore[return]
        from animica_studio.services.process_manager import ProcessManager  # noqa: PLC0415
        profile = self._config.get_active_profile()
        return ProcessManager(
            start_cmd=profile.node.start_cmd,
            rpc_url=profile.node.rpc_local_url,
        )

    def _run_task(self, fn) -> None:  # type: ignore[type-arg]
        if qthread_running(self._worker_thread):
            return
        self._status_label.setText("⏳ Working…")
        self._worker_thread = WorkerThread(fn)
        self._worker_thread.worker.result.connect(self._on_result)
        self._worker_thread.worker.error.connect(
            lambda msg, _tb: self._status_label.setText(f"❌ {msg}")
        )
        self._worker_thread.worker.finished.connect(self._on_worker_finished)
        self._worker_thread.destroyed.connect(lambda *_: setattr(self, "_worker_thread", None))
        self._worker_thread.start()


    def _on_worker_finished(self) -> None:
        self._worker_thread = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        thread = self._worker_thread
        self._worker_thread = None
        stop_thread(thread)
        super().closeEvent(event)

    def _on_result(self, status: object) -> None:
        if not isinstance(status, dict):
            self._status_label.setText(str(status))
            return
        running = status.get("running", False)
        rpc = status.get("rpc_reachable", False)
        pid = status.get("pid")
        self._status_label.setText(
            f"{'🟢 Running' if running else '🔴 Stopped'} | "
            f"PID: {pid or 'N/A'} | RPC: {'✅' if rpc else '❌'}"
        )
        lines = status.get("last_log_lines", [])
        self._log_view.setPlainText("\n".join(lines[-50:]))

    def _on_start(self) -> None:
        self._run_task(lambda: self._make_manager().start())

    def _on_stop(self) -> None:
        self._run_task(lambda: self._make_manager().stop())

    def _on_restart(self) -> None:
        self._run_task(lambda: self._make_manager().restart())

    def _on_status(self) -> None:
        self._run_task(lambda: self._make_manager().status())
