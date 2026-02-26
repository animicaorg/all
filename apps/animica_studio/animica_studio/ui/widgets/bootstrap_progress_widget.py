from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class BootstrapProgressWidget(QDialog):
    pauseRequested = Signal()
    resumeRequested = Signal()
    cancelRequested = Signal()
    retryRequested = Signal()
    copyDiagnosticsRequested = Signal()
    addSourcesRequested = Signal()
    continueRequested = Signal()
    exportPlanRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dataset Bootstrap")
        self.resize(760, 500)
        root = QVBoxLayout(self)

        self.state_lbl = QLabel("IDLE")
        self.download = QProgressBar(); self.download.setRange(0, 100)
        self.processing = QProgressBar(); self.processing.setRange(0, 100)
        self.repo_lbl = QLabel("Active source: -")
        self.target_lbl = QLabel("Target progress: 0 / 0")
        self.queue_lbl = QLabel("Queue: 0 work items remaining")
        self.stop_reason_lbl = QLabel("Stop reason: -")
        self.shards_lbl = QLabel("Shards: 0 | Output: 0 B")
        self.notice_lbl = QLabel("")
        root.addWidget(QLabel("State"))
        root.addWidget(self.state_lbl)
        root.addWidget(QLabel("Download progress")); root.addWidget(self.download)
        root.addWidget(QLabel("Processing progress")); root.addWidget(self.processing)
        root.addWidget(self.repo_lbl)
        root.addWidget(self.target_lbl)
        root.addWidget(self.queue_lbl)
        root.addWidget(self.stop_reason_lbl)
        root.addWidget(self.shards_lbl)
        root.addWidget(self.notice_lbl)

        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True)
        root.addWidget(self.logs, 1)

        row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.cancel_btn = QPushButton("Cancel")
        self.retry_btn = QPushButton("Retry")
        self.copy_btn = QPushButton("Copy diagnostics")
        self.add_sources_btn = QPushButton("Add sources / expand allowlist")
        self.continue_btn = QPushButton("Continue anyway")
        self.export_plan_btn = QPushButton("Export plan diagnostics")
        self.open_dir_btn = QPushButton("Open output folder")
        for b in [self.pause_btn, self.resume_btn, self.cancel_btn, self.retry_btn, self.copy_btn, self.add_sources_btn, self.continue_btn, self.export_plan_btn, self.open_dir_btn]:
            row.addWidget(b)
        row.addStretch(1)
        root.addLayout(row)

        self.pause_btn.clicked.connect(self.pauseRequested.emit)
        self.resume_btn.clicked.connect(self.resumeRequested.emit)
        self.cancel_btn.clicked.connect(self.cancelRequested.emit)
        self.retry_btn.clicked.connect(self.retryRequested.emit)
        self.copy_btn.clicked.connect(self.copyDiagnosticsRequested.emit)
        self.add_sources_btn.clicked.connect(self.addSourcesRequested.emit)
        self.continue_btn.clicked.connect(self.continueRequested.emit)
        self.export_plan_btn.clicked.connect(self.exportPlanRequested.emit)
        self.open_dir_btn.clicked.connect(self._open_output)

        self._output_dir = ""

    def update_state(self, state: str) -> None:
        self.state_lbl.setText(state)

    def update_metrics(self, *, bytes_downloaded: int, bytes_total: int | None, bytes_processed: int, target_bytes: int | None, shards: int, output_bytes: int, repo: str = "", ref: str = "", sources_exhausted: bool = False, queue_remaining: int = 0, stop_reason: str = "") -> None:
        if bytes_total:
            self.download.setValue(max(0, min(100, int(bytes_downloaded * 100 / max(1, bytes_total)))))
            self.download.setFormat(f"{bytes_downloaded} / {bytes_total} bytes")
        else:
            self.download.setValue(0)
            self.download.setFormat(f"{bytes_downloaded} bytes")
        if target_bytes:
            self.processing.setValue(max(0, min(100, int(bytes_processed * 100 / max(1, target_bytes)))))
        self.repo_lbl.setText(f"Active source: {repo}@{ref}" if repo else "Active source: -")
        self.target_lbl.setText(f"Target progress: {bytes_processed} / {target_bytes or 0}")
        self.queue_lbl.setText(f"Queue: {max(0, int(queue_remaining))} work items remaining")
        self.stop_reason_lbl.setText(f"Stop reason: {stop_reason or '-'}")
        self.shards_lbl.setText(f"Shards: {shards} | Output: {output_bytes} B")
        if sources_exhausted and target_bytes:
            self.notice_lbl.setText(f"Sources exhausted: only {bytes_processed} bytes processed; target {target_bytes}.")
        else:
            self.notice_lbl.setText("")

    def append_log(self, kind: str, text: str) -> None:
        self.logs.appendPlainText(f"[{kind}] {text}")

    def set_output_dir(self, output_dir: str) -> None:
        self._output_dir = output_dir

    def _open_output(self) -> None:
        if not self._output_dir:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._output_dir).expanduser())))
