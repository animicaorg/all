"""Node page with clear sync state, diagnostics, and non-blocking controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.studio_models import NodeSummary
from animica_studio.services.studio_status_service import ServiceActionResult, StudioStatusService
from animica_studio.storage.config import Config
from animica_studio.ui.components.primitives import Card, SectionHeader
from animica_studio.util.qt import ui_thread_only
from animica_studio.services.workers import run_in_threadpool

log = logging.getLogger(__name__)


class NodePage(QWidget):
    open_logs_requested = Signal()

    def __init__(
        self,
        config: Config | None = None,
        parent: QWidget | None = None,
        *,
        status_service: StudioStatusService | None = None,
    ) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._status_service = status_service or StudioStatusService(self._config)
        self._refresh_job = None
        self._action_job = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)
        root.addWidget(SectionHeader("Node", "Start the node, understand sync progress, and recover from stalls without guesswork."))

        self._summary = QLabel("Checking local node status…")
        self._summary.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(self._summary)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        self._process_card = self._metric_card("Process")
        self._rpc_card = self._metric_card("RPC")
        self._sync_card = self._metric_card("Sync")
        self._peers_card = self._metric_card("Peers")
        self._chain_card = self._metric_card("Chain")
        self._network_card = self._metric_card("Network")
        for index, card in enumerate(
            [
                self._process_card,
                self._rpc_card,
                self._sync_card,
                self._peers_card,
                self._chain_card,
                self._network_card,
            ]
        ):
            metrics.addWidget(card, index // 3, index % 3)
        root.addLayout(metrics)

        actions = Card()
        actions.layout().addWidget(QLabel("Actions"))
        row1 = QHBoxLayout()
        for label, slot in [
            ("Start Node", self._run_start),
            ("Stop Node", self._run_stop),
            ("Restart Node", self._run_restart),
            ("Refresh Status", self.refresh_status),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            row1.addWidget(btn)
        row1.addStretch(1)
        actions.layout().addLayout(row1)
        row2 = QHBoxLayout()
        for label, slot in [
            ("Force Sync", self._run_force_sync),
            ("Bootstrap Peers", self._run_bootstrap),
            ("Discover Snapshot", self._run_snapshot_discover),
            ("Open Logs", self._open_logs),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            row2.addWidget(btn)
        row2.addStretch(1)
        actions.layout().addLayout(row2)
        root.addWidget(actions)

        self._explain = Card()
        self._explain.layout().addWidget(QLabel("What is happening"))
        self._explain_label = QLabel("Loading diagnostics…")
        self._explain_label.setWordWrap(True)
        self._explain.layout().addWidget(self._explain_label)
        root.addWidget(self._explain)

        diag = Card()
        header = QHBoxLayout()
        header.addWidget(QLabel("Diagnostics"))
        header.addStretch()
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_diagnostics)
        header.addWidget(copy_btn)
        diag.layout().addLayout(header)
        self._diag = QTextEdit()
        self._diag.setReadOnly(True)
        self._diag.setMinimumHeight(140)
        diag.layout().addWidget(self._diag)
        root.addWidget(diag)

        logs = Card()
        logs.layout().addWidget(QLabel("Recent Node Logs"))
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(220)
        logs.layout().addWidget(self._log_view)
        root.addWidget(logs, 1)

    def _metric_card(self, title: str) -> Card:
        card = Card()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
        value = QLabel("—")
        value.setStyleSheet("font-size: 20px; font-weight: 700;")
        detail = QLabel("")
        detail.setStyleSheet("color: #8f99a5;")
        detail.setWordWrap(True)
        card.layout().addWidget(title_label)
        card.layout().addWidget(value)
        card.layout().addWidget(detail)
        card._value = value  # type: ignore[attr-defined]
        card._detail = detail  # type: ignore[attr-defined]
        return card

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not hasattr(self, "_timer"):
            self._timer = QTimer(self)
            self._timer.setInterval(15_000)
            self._timer.timeout.connect(self.refresh_status)
            self._timer.start()
            QTimer.singleShot(0, self.refresh_status)

    def refresh_status(self) -> None:
        if self._refresh_job is not None:
            return
        self._refresh_job = run_in_threadpool(self._status_service.collect_snapshot)
        self._refresh_job.signals.result.connect(self._apply_snapshot)
        self._refresh_job.signals.error.connect(self._on_refresh_error)
        self._refresh_job.signals.finished.connect(lambda: setattr(self, "_refresh_job", None))

    def _run_start(self) -> None:
        self._run_action(self._status_service.start_node)

    def _run_stop(self) -> None:
        self._run_action(self._status_service.stop_node)

    def _run_restart(self) -> None:
        self._run_action(self._status_service.restart_node)

    def _run_force_sync(self) -> None:
        self._run_action(self._status_service.force_sync)

    def _run_bootstrap(self) -> None:
        self._run_action(self._status_service.bootstrap_node)

    def _run_snapshot_discover(self) -> None:
        self._run_action(self._status_service.discover_snapshot)

    def _run_action(self, action) -> None:  # type: ignore[no-untyped-def]
        if self._action_job is not None:
            return
        self._diag.setPlainText("Working…")
        self._action_job = run_in_threadpool(action)
        self._action_job.signals.result.connect(self._apply_action_result)
        self._action_job.signals.error.connect(self._on_action_error)
        self._action_job.signals.finished.connect(lambda: setattr(self, "_action_job", None))

    @ui_thread_only(log)
    def _apply_snapshot(self, snapshot) -> None:  # noqa: ANN001
        node: NodeSummary = snapshot.node
        self._summary.setText(
            "Node is running and reachable." if node.rpc_reachable else ("Node process is running, but RPC is not reachable." if node.running else "Node is stopped.")
        )
        self._set_metric(self._process_card, "Running" if node.running else "Stopped", f"RPC target: {node.rpc_url}")
        self._set_metric(self._rpc_card, "Online" if node.rpc_reachable else "Offline", node.last_error or node.rpc_url)
        sync_value = node.sync.state.replace("_", " ").title()
        sync_detail = node.sync.detail or "No sync data yet."
        if node.sync.stall_reason:
            sync_detail = f"{sync_detail}\nStall reason: {node.sync.stall_reason}"
        self._set_metric(self._sync_card, sync_value, sync_detail)
        self._set_metric(
            self._peers_card,
            str(node.peer_count) if node.peer_count is not None else "—",
            "Connected peers",
        )
        self._set_metric(
            self._chain_card,
            str(node.head_number) if node.head_number is not None else "—",
            node.head_hash or "Head hash unavailable",
        )
        self._set_metric(
            self._network_card,
            str(node.chain_id) if node.chain_id is not None else "Unknown",
            snapshot.network_name or "Network preset unknown",
        )
        explanation = "Studio is using a single shared status model for the node."
        if node.sync.peer_count == 0:
            explanation += " The main blocker is that the node has no peers."
        elif node.sync.stall_reason:
            explanation += f" Sync appears stalled because: {node.sync.stall_reason}"
        elif node.sync.progress_pct is not None and node.sync.progress_pct < 100:
            explanation += " The node is still catching up to the network tip."
        elif node.rpc_reachable:
            explanation += " The node looks healthy."
        self._explain_label.setText(explanation)
        self._diag.setPlainText(self._status_service.sync_diagnostics_text())
        self._log_view.setPlainText("\n".join(node.log_tail[-120:]) if node.log_tail else "(no log output yet)")

    @ui_thread_only(log)
    def _apply_action_result(self, result: ServiceActionResult) -> None:
        self._diag.setPlainText(f"{result.summary}\n\n{result.details}".strip())
        self.refresh_status()

    @ui_thread_only(log)
    def _on_refresh_error(self, message: str, _traceback: str) -> None:
        self._summary.setText("Failed to refresh node status.")
        self._diag.setPlainText(message)

    @ui_thread_only(log)
    def _on_action_error(self, message: str, _traceback: str) -> None:
        self._diag.setPlainText(message)

    def _set_metric(self, card: Card, value: str, detail: str) -> None:
        card._value.setText(value)  # type: ignore[attr-defined]
        card._detail.setText(detail)  # type: ignore[attr-defined]

    def _copy_diagnostics(self) -> None:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._diag.toPlainText())

    def _open_logs(self) -> None:
        self.open_logs_requested.emit()
