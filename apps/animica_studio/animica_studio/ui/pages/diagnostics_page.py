"""Integrated logs and diagnostics page for Studio support workflows."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.build_info_service import collect_build_info
from animica_studio.services.debug_bundle import collect_debug_bundle
from animica_studio.services.diagnostics import diagnostics
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.services.wallet_service import WalletService
from animica_studio.services.workers import run_in_threadpool
from animica_studio.storage.config import Config, load_config
from animica_studio.ui.components.primitives import Card, SectionHeader

log = logging.getLogger(__name__)


class DiagnosticsPage(QWidget):
    def __init__(
        self,
        config: Config | None = None,
        parent: QWidget | None = None,
        *,
        status_service: StudioStatusService | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        self._status_service = status_service or StudioStatusService(self._config)
        self._wallet_service = WalletService(self._config)
        self._snapshot_job = None
        self._latest_snapshot = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)
        root.addWidget(SectionHeader("Logs", "Live warnings, recent logs, and a copyable diagnostics bundle."))

        info = Card()
        info.layout().addWidget(QLabel("Environment"))
        self._info_form = QFormLayout()
        info.layout().addLayout(self._info_form)
        root.addWidget(info)

        actions = Card()
        row = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter log lines by module, word, or error text")
        self._filter.textChanged.connect(self._refresh)
        row.addWidget(self._filter, 1)
        copy_btn = QPushButton("Copy Bundle")
        copy_btn.clicked.connect(self._copy_bundle)
        row.addWidget(copy_btn)
        export_btn = QPushButton("Export Bundle")
        export_btn.clicked.connect(self._export_bundle)
        row.addWidget(export_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        row.addWidget(refresh_btn)
        actions.layout().addLayout(row)
        root.addWidget(actions)

        errors = Card()
        errors.layout().addWidget(QLabel("Structured Issues"))
        self._events = QTextEdit()
        self._events.setReadOnly(True)
        self._events.setMinimumHeight(180)
        errors.layout().addWidget(self._events)
        root.addWidget(errors)

        logs = Card()
        logs.layout().addWidget(QLabel("Recent Log Lines"))
        self._logs = QTextEdit()
        self._logs.setReadOnly(True)
        self._logs.setMinimumHeight(260)
        logs.layout().addWidget(self._logs)
        root.addWidget(logs, 1)

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not hasattr(self, "_timer"):
            self._timer = QTimer(self)
            self._timer.setInterval(5_000)
            self._timer.timeout.connect(self._refresh)
            self._timer.start()
            QTimer.singleShot(0, self._refresh)

    def _refresh(self) -> None:
        build = collect_build_info()
        while self._info_form.rowCount():
            self._info_form.removeRow(0)
        self._info_form.addRow("App version", QLabel(build.app_version))
        self._info_form.addRow("Python", QLabel(build.python_version))
        self._info_form.addRow("Platform", QLabel(build.platform_label))
        self._info_form.addRow("Packaged", QLabel("Yes" if build.packaged else "No"))
        self._info_form.addRow("Executable", QLabel(build.executable))
        self._info_form.addRow("App data", QLabel(build.app_data_dir))
        self._info_form.addRow("Config", QLabel(build.config_path))
        self._info_form.addRow("Logs", QLabel(build.logs_dir))
        self._info_form.addRow("Current RPC", QLabel(getattr(self._latest_snapshot, "rpc_url", "") or "Loading…"))

        filter_text = self._filter.text().strip().lower()
        event_lines = []
        for event in diagnostics.get_events(last_n=80):
            line = f"[{event.level}] {event.source}: {event.message}"
            if filter_text and filter_text not in line.lower():
                continue
            event_lines.append(line)
        self._events.setPlainText("\n".join(event_lines) if event_lines else "(no matching issues)")

        log_lines = []
        for line in diagnostics.get_recent_logs(last_n=300):
            if filter_text and filter_text not in line.lower():
                continue
            log_lines.append(line)
        self._logs.setPlainText("\n".join(log_lines) if log_lines else "(no matching log lines)")

        if self._snapshot_job is None:
            self._snapshot_job = run_in_threadpool(self._status_service.collect_snapshot)
            self._snapshot_job.signals.result.connect(self._apply_snapshot)
            self._snapshot_job.signals.error.connect(lambda message, _tb: log.warning("Diagnostics refresh failed: %s", message))
            self._snapshot_job.signals.finished.connect(lambda: setattr(self, "_snapshot_job", None))

    def _apply_snapshot(self, snapshot) -> None:  # noqa: ANN001
        self._latest_snapshot = snapshot
        self._refresh()

    def _bundle_text(self) -> str:
        snapshot = self._latest_snapshot or self._status_service.collect_snapshot()
        last_head = type("HeadStub", (), {"number": snapshot.node.head_number, "hash": snapshot.node.head_hash, "timestamp": None})()
        return collect_debug_bundle(
            self._config,
            diagnostics=diagnostics,
            wallet_service=self._wallet_service,
            last_head=last_head,
            last_chain_id=snapshot.node.chain_id,
        )

    def _copy_bundle(self) -> None:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._bundle_text())

    def _export_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Diagnostics Bundle", "animica-studio-diagnostics.txt", "Text Files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._bundle_text())
        except OSError as exc:
            log.warning("Failed to export diagnostics bundle: %s", exc)
