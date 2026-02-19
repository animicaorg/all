"""Console page — run CLI commands and stream output."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import Signal, QObject
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.exec_models import StreamEvent
from animica_studio.services.workers import WorkerThread
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class _StreamSignals(QObject):
    line_received = Signal(str)


class ConsolePage(QWidget):
    """Interactive console: run a CLI command and stream its output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker_thread: WorkerThread | None = None
        self._cancel_token: CancelToken | None = None
        self._signals = _StreamSignals()
        self._signals.line_received.connect(self._append_line)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        layout.addWidget(QLabel("🖱️  Console — CLI Command Runner"))

        cmd_row = QHBoxLayout()
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText("animica --version")
        self._cmd_edit.returnPressed.connect(self._on_run)
        run_btn = QPushButton("▶  Run")
        run_btn.clicked.connect(self._on_run)
        self._cancel_btn = QPushButton("⏹  Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)
        cmd_row.addWidget(self._cmd_edit, stretch=1)
        cmd_row.addWidget(run_btn)
        cmd_row.addWidget(self._cancel_btn)
        layout.addLayout(cmd_row)

        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setFontFamily("monospace")
        layout.addWidget(self._output, stretch=1)

    def _on_run(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return

        raw = self._cmd_edit.text().strip()
        if not raw:
            return

        import shlex  # noqa: PLC0415
        try:
            cmd = shlex.split(raw)
        except ValueError:
            cmd = raw.split()

        self._output.clear()
        self._cancel_token = CancelToken()
        signals = self._signals

        def _stream_cb(ev: StreamEvent) -> None:
            prefix = "" if ev.stream == "stdout" else ("[stderr] " if ev.stream == "stderr" else "[sys]  ")
            signals.line_received.emit(f"{prefix}{ev.line}")

        token = self._cancel_token

        def _task():  # type: ignore[return]
            from animica_studio.services.cli_runner import CliRunner  # noqa: PLC0415
            runner = CliRunner()
            result = runner.run(cmd, timeout_s=60.0, cancel_token=token, stream_cb=_stream_cb)
            return result

        self._worker_thread = WorkerThread(_task)
        self._worker_thread.worker.result.connect(self._on_done)
        self._worker_thread.worker.error.connect(
            lambda msg, _tb: self._append_line(f"[error] {msg}")
        )
        self._worker_thread.worker.finished.connect(lambda: self._cancel_btn.setEnabled(False))
        self._cancel_btn.setEnabled(True)
        self._worker_thread.start()

    def _on_cancel(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()

    def _on_done(self, result: object) -> None:
        from animica_studio.models.exec_models import ExecResult  # noqa: PLC0415
        if isinstance(result, ExecResult):
            summary = (
                f"\n[exit {result.returncode} | {result.duration_ms}ms"
                f"{' | TIMEOUT' if result.timed_out else ''}"
                f"{' | CANCELLED' if result.cancelled else ''}]"
            )
            self._append_line(summary)

    def _append_line(self, line: str) -> None:
        self._output.append(line)
