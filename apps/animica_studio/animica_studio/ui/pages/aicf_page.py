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
from animica_studio.services.da_status_service import DaStatusService
from animica_studio.services.error_format import format_rpc_error, safe_json_dumps
from animica_studio.services.profile_helpers import get_active_rpc_url
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
        self._da_status = DaStatusService(self._config)
        self._runner = JobRunner.instance()
        self._claimable_amount: int = 0
        # Keep strong references so handles are not GC'd while in flight.
        self._active_handles: list[JobHandle] = []
        self._build_ui()
        self._refresh_da_readiness()

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
        self._credits_addr.textChanged.connect(self._on_address_changed)
        form.addRow("Address:", self._credits_addr)
        self._claimable_label = QLabel("Claimable: 0")
        form.addRow("", self._claimable_label)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._fetch_btn = QPushButton("Fetch Credits")
        self._fetch_btn.clicked.connect(self._fetch_credits)
        self._claim_amount = QLineEdit()
        self._claim_amount.setPlaceholderText("Amount (blank = full)")
        self._claim_btn = QPushButton("Claim Credits")
        self._claim_btn.setEnabled(False)
        self._claim_btn.clicked.connect(self._claim_credits)
        self._diag_btn = QPushButton("Copy diagnostics")
        self._diag_btn.clicked.connect(self._copy_diagnostics)
        btn_row.addWidget(self._fetch_btn)
        btn_row.addWidget(QLabel("Amount:"))
        btn_row.addWidget(self._claim_amount)
        btn_row.addWidget(self._claim_btn)
        btn_row.addWidget(self._diag_btn)
        layout.addLayout(btn_row)

        self._credits_output = QTextEdit()
        self._credits_output.setReadOnly(True)
        layout.addWidget(self._credits_output, stretch=1)
        return w

    def _on_address_changed(self) -> None:
        self._claim_btn.setEnabled(False)
        self._claimable_amount = 0
        self._claimable_label.setText("Claimable: 0")

    def _fetch_credits(self) -> None:
        try:
            log.info("AICF action clicked: fetch_credits")
            addr = self._credits_addr.text().strip()
            if not addr:
                self._credits_output.setPlainText("Enter an address first.")
                return
            log.info("AICF argv/rpc: get_miner_credits/get_claimable addr=%s", addr)
            self._credits_output.setPlainText("[system] Fetching claimable…\nLoading…")
            self._fetch_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                claimable = service.get_claimable(addr)
                credits = service.get_miner_credits(addr)
                return {"claimable": claimable, "credits": credits}

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
            claimable_result = result.get("claimable") if isinstance(result.get("claimable"), dict) else {}
            credits_result = result.get("credits") if isinstance(result.get("credits"), dict) else {}
            if claimable_result.get("ok") and credits_result.get("ok"):
                self._claimable_amount = int(claimable_result.get("claimable") or 0)
                self._claimable_label.setText(f"Claimable: {self._claimable_amount}")
                self._claim_btn.setEnabled(self._claimable_amount > 0)
                self._credits_output.setPlainText(
                    "[system] Fetching claimable…\n"
                    f"{safe_json_dumps({'claimable': claimable_result.get('data'), 'credits': credits_result.get('data')}, indent=2)}"
                )
            else:
                err = format_rpc_error(claimable_result.get("error") or credits_result.get("error"))
                log.warning("AICF error=%s (fetch_credits)", err)
                self._credits_output.setPlainText(f"Error: {err}")
                self._claim_btn.setEnabled(False)
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
            if self._claimable_amount <= 0:
                self._credits_output.setPlainText("No claimable credits available for this address.")
                return
            log.info("AICF argv/rpc: claim_credits addr=%s amount=%s", addr, amount)
            self._credits_output.setPlainText("[system] Fetching claimable…\n[system] Submitting claim…")
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
                tx_hash = result.get("tx_hash")
                tx_line = f"\nTx hash: {tx_hash}" if tx_hash else ""
                explorer = (
                    f"\nExplorer: https://explorer.animica.ai/tx/{tx_hash}" if tx_hash else ""
                )
                self._credits_output.setPlainText(
                    f"✅ Claimed!{tx_line}{explorer}\n{safe_json_dumps(result.get('data'), indent=2)}"
                )
                self._fetch_credits()
            else:
                err = format_rpc_error(result.get("error"))
                log.warning("AICF error=%s (claim_credits)", err)
                self._credits_output.setPlainText(f"Error: {err}")
                self._claim_btn.setEnabled(self._claimable_amount > 0)
        except Exception:  # noqa: BLE001
            log.error("AICF _on_claim_finished error:\n%s", traceback.format_exc())
            self._claim_btn.setEnabled(self._claimable_amount > 0)

    def _copy_diagnostics(self) -> None:
        addr = self._credits_addr.text().strip()
        payload = self._service.get_diagnostics()
        payload["address"] = addr
        text = safe_json_dumps(payload, indent=2)
        from PySide6.QtGui import QGuiApplication  # noqa: PLC0415

        QGuiApplication.clipboard().setText(text)
        self._credits_output.setPlainText(f"Diagnostics copied to clipboard.\n{text}")

    # ------------------------------------------------------------------
    # Jobs tab
    # ------------------------------------------------------------------

    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._rpc_label = QLabel()
        self._rpc_label.setText(f"RPC: {get_active_rpc_url(self._config)}")
        layout.addWidget(self._rpc_label)

        self._da_readiness_label = QLabel("DA readiness: unknown")
        layout.addWidget(self._da_readiness_label)

        btn_row = QHBoxLayout()
        self._refresh_da_btn = QPushButton("🔄  Refresh DA readiness")
        self._refresh_da_btn.clicked.connect(self._refresh_da_readiness)
        btn_row.addWidget(self._refresh_da_btn)
        self._enable_da_btn = QPushButton("DA Capability Check")
        self._enable_da_btn.clicked.connect(self._refresh_da_readiness)
        self._enable_da_btn.setEnabled(True)
        btn_row.addWidget(self._enable_da_btn)
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

    def _refresh_da_readiness(self) -> None:
        self._refresh_da_btn.setEnabled(False)

        def _task() -> dict:
            return self._da_status.get_status()

        handle = self._track(self._runner.run_callable(_task, timeout_s=15))
        handle.finished.connect(self._on_da_readiness_finished)

    def _on_da_readiness_finished(self, _jid: str, rc: int, payload: object) -> None:
        self._refresh_da_btn.setEnabled(True)
        result = payload if isinstance(payload, dict) else {}
        enabled = bool(result.get("enabled", False))
        self._enable_da_btn.setEnabled(True)
        methods = result.get("da_methods") if isinstance(result.get("da_methods"), dict) else {}
        put_method = methods.get("put_blob")
        get_method = methods.get("get_blob")
        self._rpc_label.setText(f"RPC: {result.get('rpc_url') or get_active_rpc_url(self._config)}")
        self._da_readiness_label.setText(
            f"DA readiness: {'DA RPC available' if put_method else 'DA methods not exposed'} | "
            f"put={put_method or 'missing'} get={get_method or 'missing'} | "
            f"server={result.get('server_version', 'unknown')}"
        )

    def _list_jobs(self) -> None:
        try:
            log.info("AICF action clicked: list_jobs")
            self._jobs_output.setPlainText("Loading…")
            self._list_jobs_btn.setEnabled(False)
            service = self._service

            def _task() -> dict:
                da_status = self._da_status.get_status()
                log.info("AICF preflight da_status=%s", da_status)
                if not da_status.get("enabled"):
                    return {"ok": False, "error": "DA disabled on node", "da_status": da_status}
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
                da_status = result.get("da_status") if isinstance(result.get("da_status"), dict) else None
                if da_status and not da_status.get("enabled"):
                    self._da_readiness_label.setText(
                        f"DA readiness: DA methods not exposed | server={da_status.get('server_version', 'unknown')} | "
                        f"last_error={da_status.get('last_error', '')}"
                    )
                    self._rpc_label.setText(f"RPC: {da_status.get('rpc_url', get_active_rpc_url(self._config))}")
                    self._jobs_output.setPlainText("DA methods not exposed by this node.")
                else:
                    err = format_rpc_error(result.get("error"))
                    log.warning("AICF error=%s (list_jobs)", err)
                    self._jobs_output.setPlainText(f"Error: {err}")
        except Exception:  # noqa: BLE001
            log.error("AICF _on_list_jobs_finished error:\n%s", traceback.format_exc())
            self._list_jobs_btn.setEnabled(True)
