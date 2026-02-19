"""Settings page — profile configuration + RPC test + diagnostics panel."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.diagnostics import diagnostics
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config, save_config

log = logging.getLogger(__name__)


class SettingsPage(QWidget):
    """Application settings: edit profile, test RPC, view diagnostics."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._worker_thread: WorkerThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        profile_group = QGroupBox("Active Profile")
        form = QFormLayout(profile_group)
        profile = self._config.get_active_profile()

        self._rpc_url_edit = QLineEdit(profile.rpc_url)
        form.addRow("RPC URL:", self._rpc_url_edit)

        self._chain_id_edit = QLineEdit(str(profile.chain_id_expected))
        form.addRow("Chain ID:", self._chain_id_edit)

        self._start_cmd_edit = QLineEdit(" ".join(profile.node.start_cmd))
        form.addRow("Node start cmd:", self._start_cmd_edit)
        layout.addWidget(profile_group)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾  Save")
        save_btn.clicked.connect(self._on_save)
        test_rpc_btn = QPushButton("🔌  Test RPC")
        test_rpc_btn.clicked.connect(self._on_test_rpc)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(test_rpc_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        diag_group = QGroupBox("Recent Diagnostics (last 20 events)")
        diag_layout = QVBoxLayout(diag_group)
        self._diag_text = QTextEdit()
        self._diag_text.setReadOnly(True)
        self._diag_text.setMaximumHeight(200)
        diag_layout.addWidget(self._diag_text)
        refresh_btn = QPushButton("↺  Refresh")
        refresh_btn.clicked.connect(self._refresh_diagnostics)
        diag_layout.addWidget(refresh_btn)
        layout.addWidget(diag_group)

        layout.addStretch()
        self._refresh_diagnostics()

    def _on_save(self) -> None:
        profile = self._config.get_active_profile()
        profile.rpc_url = self._rpc_url_edit.text().strip()
        try:
            profile.chain_id_expected = int(self._chain_id_edit.text().strip())
        except ValueError:
            pass
        cmd_text = self._start_cmd_edit.text().strip()
        profile.node.start_cmd = cmd_text.split() if cmd_text else ["animica", "node", "start"]
        save_config(self._config)
        self._status_label.setText("✅ Settings saved.")
        log.info("Settings saved by user")

    def _on_test_rpc(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return
        url = self._rpc_url_edit.text().strip()
        self._status_label.setText(f"⏳ Testing RPC at {url} …")

        def _do_test() -> str:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415
            client = RpcClient(url, connect_timeout=5.0, read_timeout=10.0, max_retries=2)
            try:
                result = client.discover()
                methods = result.get("methods", [])
                return f"✅ RPC OK — {len(methods)} methods discovered"
            finally:
                client.close()

        self._worker_thread = WorkerThread(_do_test)
        self._worker_thread.worker.result.connect(lambda msg: self._status_label.setText(str(msg)))
        self._worker_thread.worker.error.connect(
            lambda msg, _tb: self._status_label.setText(f"❌ RPC error: {msg}")
        )
        self._worker_thread.start()

    def _refresh_diagnostics(self) -> None:
        import datetime  # noqa: PLC0415
        events = diagnostics.get_events(last_n=20)
        if not events:
            self._diag_text.setPlainText("(no events)")
            return
        lines = [
            f"[{datetime.datetime.fromtimestamp(ev.ts).strftime('%H:%M:%S')}] "
            f"{ev.level:5s} {ev.source}: {ev.message}"
            for ev in reversed(events)
        ]
        self._diag_text.setPlainText("\n".join(lines))
