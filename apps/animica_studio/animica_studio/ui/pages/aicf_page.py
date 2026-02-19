"""AICF page — status, miner credits, claim, jobs list/submit/watch."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.aicf_service import AicfService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class AicfPage(QWidget):
    """AICF credit and job management."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = AicfService(self._config)
        self._cancel_token = CancelToken()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("🤖  AICF")
        title.setObjectName("placeholderLabel")
        layout.addWidget(title)

        tabs = QTabWidget()

        # Status tab
        status_tab = self._build_status_tab()
        tabs.addTab(status_tab, "Status")

        # Credits tab
        credits_tab = self._build_credits_tab()
        tabs.addTab(credits_tab, "Miner Credits")

        # Jobs tab
        jobs_tab = self._build_jobs_tab()
        tabs.addTab(jobs_tab, "Jobs")

        layout.addWidget(tabs, stretch=1)

    # ------------------------------------------------------------------
    # Status tab
    # ------------------------------------------------------------------

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        btn = QPushButton("🔄  Refresh Status")
        self._status_output = QTextEdit()
        self._status_output.setReadOnly(True)
        btn.clicked.connect(self._refresh_status)
        layout.addWidget(btn)
        layout.addWidget(self._status_output, stretch=1)
        return w

    def _refresh_status(self) -> None:
        self._status_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.get_status()

        def _done(result):
            if result.get("ok"):
                self._status_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._status_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._status_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Credits tab
    # ------------------------------------------------------------------

    def _build_credits_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self._credits_addr = QLineEdit()
        self._credits_addr.setPlaceholderText("0x… or bech32 address")
        form.addRow("Address:", self._credits_addr)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        fetch_btn = QPushButton("Fetch Credits")
        fetch_btn.clicked.connect(self._fetch_credits)
        self._claim_amount = QLineEdit()
        self._claim_amount.setPlaceholderText("Amount (blank = full)")
        claim_btn = QPushButton("Claim Credits")
        claim_btn.clicked.connect(self._claim_credits)
        btn_row.addWidget(fetch_btn)
        btn_row.addWidget(QLabel("Amount:"))
        btn_row.addWidget(self._claim_amount)
        btn_row.addWidget(claim_btn)
        layout.addLayout(btn_row)

        self._credits_output = QTextEdit()
        self._credits_output.setReadOnly(True)
        layout.addWidget(self._credits_output, stretch=1)
        return w

    def _fetch_credits(self) -> None:
        addr = self._credits_addr.text().strip()
        if not addr:
            self._credits_output.setPlainText("Enter an address first.")
            return
        self._credits_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.get_miner_credits(addr)

        def _done(result):
            if result.get("ok"):
                self._credits_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._credits_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._credits_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    def _claim_credits(self) -> None:
        addr = self._credits_addr.text().strip()
        if not addr:
            self._credits_output.setPlainText("Enter an address first.")
            return
        amount_text = self._claim_amount.text().strip()
        amount = int(amount_text) if amount_text.isdigit() else None
        service = self._service

        def _task():
            return service.claim_credits(addr, amount)

        def _done(result):
            if result.get("ok"):
                self._credits_output.setPlainText(f"✅ Claimed!\n{safe_json_dumps(result.get('data'), indent=2)}")
            else:
                self._credits_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._credits_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()

    # ------------------------------------------------------------------
    # Jobs tab
    # ------------------------------------------------------------------

    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        btn_row = QHBoxLayout()
        list_btn = QPushButton("📋  List Jobs")
        list_btn.clicked.connect(self._list_jobs)
        btn_row.addWidget(list_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._jobs_output = QTextEdit()
        self._jobs_output.setReadOnly(True)
        layout.addWidget(self._jobs_output, stretch=1)

        # Watch stream
        self._jobs_console = StreamConsole()
        layout.addWidget(self._jobs_console, stretch=1)
        return w

    def _list_jobs(self) -> None:
        self._jobs_output.setPlainText("Loading…")
        service = self._service

        def _task():
            return service.list_jobs()

        def _done(result):
            if result.get("ok"):
                self._jobs_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                self._jobs_output.setPlainText(f"Error: {format_rpc_error(result.get('error'))}")

        def _err(msg, _tb):
            self._jobs_output.setPlainText(f"Error: {msg}")

        w = WorkerThread(_task)
        w.worker.result.connect(_done)
        w.worker.error.connect(_err)
        w.start()
