"""AICF page — status, miner credits, claim, jobs list/submit/watch."""

from __future__ import annotations

import logging
import traceback

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.aicf_service import AicfService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.job_runner import JobHandle, JobRunner
from animica_studio.storage.config import Config
from animica_studio.ui.widgets.stream_console import StreamConsole

log = logging.getLogger(__name__)


class AicfPage(QWidget):
    """AICF credit and job management."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415
        self._config = config or load_config()
        self._service = AicfService(self._config)
        self._runner = JobRunner.instance()
        # Keep strong references so handles are not GC'd while in flight.
        self._active_handles: list[JobHandle] = []
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
    # Helpers
    # ------------------------------------------------------------------

    def _track(self, handle: JobHandle) -> JobHandle:
        """Keep *handle* alive until it finishes, then release the reference."""
        self._active_handles.append(handle)

        def _release(_jid: str, _rc: int, _payload: object) -> None:
            if handle in self._active_handles:
                self._active_handles.remove(handle)

        handle.finished.connect(_release)
        return handle

    # ------------------------------------------------------------------
    # Status tab
    # ------------------------------------------------------------------

    def _build_status_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self._refresh_btn = QPushButton("🔄  Refresh Status")
        self._status_output = QTextEdit()
        self._status_output.setReadOnly(True)
        self._refresh_btn.clicked.connect(self._refresh_status)
        layout.addWidget(self._refresh_btn)
        layout.addWidget(self._status_output, stretch=1)
        return w

    def _refresh_status(self) -> None:
        try:
            log.info("AICF action clicked: refresh_status")
            self._status_output.setPlainText("Loading…")
            self._refresh_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                return service.get_status()

            handle = self._track(self._runner.run_callable(_task, timeout_s=20))
            log.info("AICF started job_id=%s (refresh_status)", handle.job_id)
            handle.finished.connect(self._on_status_finished)
        except Exception:  # noqa: BLE001
            log.error("AICF _refresh_status slot error:\n%s", traceback.format_exc())
            self._refresh_btn.setEnabled(True)

    def _on_status_finished(self, _jid: str, rc: int, payload: object) -> None:
        try:
            log.info("AICF finished rc=%s job_id=%s (refresh_status)", rc, _jid)
            self._refresh_btn.setEnabled(True)
            result = payload if isinstance(payload, dict) else {}
            if result.get("ok"):
                self._status_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                err = format_rpc_error(result.get("error"))
                log.warning("AICF error=%s (refresh_status)", err)
                self._status_output.setPlainText(f"Error: {err}")
        except Exception:  # noqa: BLE001
            log.error("AICF _on_status_finished error:\n%s", traceback.format_exc())
            self._refresh_btn.setEnabled(True)

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
        self._fetch_btn = QPushButton("Fetch Credits")
        self._fetch_btn.clicked.connect(self._fetch_credits)
        self._claim_amount = QLineEdit()
        self._claim_amount.setPlaceholderText("Amount (blank = full)")
        self._claim_btn = QPushButton("Claim Credits")
        self._claim_btn.clicked.connect(self._claim_credits)
        btn_row.addWidget(self._fetch_btn)
        btn_row.addWidget(QLabel("Amount:"))
        btn_row.addWidget(self._claim_amount)
        btn_row.addWidget(self._claim_btn)
        layout.addLayout(btn_row)

        self._credits_output = QTextEdit()
        self._credits_output.setReadOnly(True)
        layout.addWidget(self._credits_output, stretch=1)
        return w

    def _fetch_credits(self) -> None:
        try:
            log.info("AICF action clicked: fetch_credits")
            addr = self._credits_addr.text().strip()
            if not addr:
                self._credits_output.setPlainText("Enter an address first.")
                return
            log.info("AICF argv/rpc: get_miner_credits addr=%s", addr)
            self._credits_output.setPlainText("Loading…")
            self._fetch_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                return service.get_miner_credits(addr)

            handle = self._track(self._runner.run_callable(_task, timeout_s=20))
            log.info("AICF started job_id=%s (fetch_credits)", handle.job_id)
            handle.finished.connect(self._on_fetch_finished)
        except Exception:  # noqa: BLE001
            log.error("AICF _fetch_credits slot error:\n%s", traceback.format_exc())
            self._fetch_btn.setEnabled(True)

    def _on_fetch_finished(self, _jid: str, rc: int, payload: object) -> None:
        try:
            log.info("AICF finished rc=%s job_id=%s (fetch_credits)", rc, _jid)
            self._fetch_btn.setEnabled(True)
            result = payload if isinstance(payload, dict) else {}
            if result.get("ok"):
                self._credits_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                err = format_rpc_error(result.get("error"))
                log.warning("AICF error=%s (fetch_credits)", err)
                self._credits_output.setPlainText(f"Error: {err}")
        except Exception:  # noqa: BLE001
            log.error("AICF _on_fetch_finished error:\n%s", traceback.format_exc())
            self._fetch_btn.setEnabled(True)

    def _claim_credits(self) -> None:
        try:
            log.info("AICF action clicked: claim_credits")
            addr = self._credits_addr.text().strip()
            if not addr:
                self._credits_output.setPlainText("Enter an address first.")
                return
            amount_text = self._claim_amount.text().strip()
            amount = int(amount_text) if amount_text.isdigit() else None
            log.info("AICF argv/rpc: claim_credits addr=%s amount=%s", addr, amount)
            self._claim_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                return service.claim_credits(addr, amount)

            handle = self._track(self._runner.run_callable(_task, timeout_s=30))
            log.info("AICF started job_id=%s (claim_credits)", handle.job_id)
            handle.finished.connect(self._on_claim_finished)
        except Exception:  # noqa: BLE001
            log.error("AICF _claim_credits slot error:\n%s", traceback.format_exc())
            self._claim_btn.setEnabled(True)

    def _on_claim_finished(self, _jid: str, rc: int, payload: object) -> None:
        try:
            log.info("AICF finished rc=%s job_id=%s (claim_credits)", rc, _jid)
            self._claim_btn.setEnabled(True)
            result = payload if isinstance(payload, dict) else {}
            if result.get("ok"):
                self._credits_output.setPlainText(
                    f"✅ Claimed!\n{safe_json_dumps(result.get('data'), indent=2)}"
                )
            else:
                err = format_rpc_error(result.get("error"))
                log.warning("AICF error=%s (claim_credits)", err)
                self._credits_output.setPlainText(f"Error: {err}")
        except Exception:  # noqa: BLE001
            log.error("AICF _on_claim_finished error:\n%s", traceback.format_exc())
            self._claim_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Jobs tab
    # ------------------------------------------------------------------

    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        btn_row = QHBoxLayout()
        self._list_jobs_btn = QPushButton("📋  List Jobs")
        self._list_jobs_btn.clicked.connect(self._list_jobs)
        btn_row.addWidget(self._list_jobs_btn)
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
        try:
            log.info("AICF action clicked: list_jobs")
            self._jobs_output.setPlainText("Loading…")
            self._list_jobs_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                return service.list_jobs()

            handle = self._track(self._runner.run_callable(_task, timeout_s=20))
            log.info("AICF started job_id=%s (list_jobs)", handle.job_id)
            handle.finished.connect(self._on_list_jobs_finished)
        except Exception:  # noqa: BLE001
            log.error("AICF _list_jobs slot error:\n%s", traceback.format_exc())
            self._list_jobs_btn.setEnabled(True)

    def _on_list_jobs_finished(self, _jid: str, rc: int, payload: object) -> None:
        try:
            log.info("AICF finished rc=%s job_id=%s (list_jobs)", rc, _jid)
            self._list_jobs_btn.setEnabled(True)
            result = payload if isinstance(payload, dict) else {}
            if result.get("ok"):
                self._jobs_output.setPlainText(safe_json_dumps(result.get("data"), indent=2))
            else:
                err = format_rpc_error(result.get("error"))
                log.warning("AICF error=%s (list_jobs)", err)
                self._jobs_output.setPlainText(f"Error: {err}")
        except Exception:  # noqa: BLE001
            log.error("AICF _on_list_jobs_finished error:\n%s", traceback.format_exc())
            self._list_jobs_btn.setEnabled(True)
