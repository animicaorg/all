"""Wallet page — balance and pending nonce lookup."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config

log = logging.getLogger(__name__)


class WalletPage(QWidget):
    """Wallet: query balance and pending nonce for an address."""

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

        title = QLabel("💳  Wallet")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        group = QGroupBox("Address Lookup")
        form = QFormLayout(group)
        self._addr_edit = QLineEdit()
        self._addr_edit.setPlaceholderText("0x…")
        form.addRow("Address:", self._addr_edit)
        layout.addWidget(group)

        btn_row = QHBoxLayout()
        bal_btn = QPushButton("💰  Get Balance")
        bal_btn.clicked.connect(self._on_balance)
        nonce_btn = QPushButton("#  Get Pending Nonce")
        nonce_btn.clicked.connect(self._on_nonce)
        btn_row.addWidget(bal_btn)
        btn_row.addWidget(nonce_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._result_label = QLabel("")
        layout.addWidget(self._result_label)
        layout.addStretch()

    def _rpc_url(self) -> str:
        return self._config.get_active_profile().rpc_url

    def _run(self, fn) -> None:  # type: ignore[type-arg]
        if self._worker_thread and self._worker_thread.isRunning():
            return
        self._result_label.setText("⏳ …")
        self._worker_thread = WorkerThread(fn)
        self._worker_thread.worker.result.connect(lambda v: self._result_label.setText(str(v)))
        self._worker_thread.worker.error.connect(
            lambda msg, _tb: self._result_label.setText(f"❌ {msg}")
        )
        self._worker_thread.start()

    def _on_balance(self) -> None:
        addr = self._addr_edit.text().strip()
        url = self._rpc_url()

        def _task() -> str:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415
            with RpcClient(url) as c:
                bal = c.get_balance(addr)
            return f"Balance: {bal}"

        self._run(_task)

    def _on_nonce(self) -> None:
        addr = self._addr_edit.text().strip()
        url = self._rpc_url()

        def _task() -> str:
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415
            with RpcClient(url) as c:
                nonce = c.get_pending_nonce(addr)
            return f"Pending nonce: {nonce}"

        self._run(_task)
