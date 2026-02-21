"""Wallet page — full multi-account wallet UI.

Layout
------
::

    ┌──────────────────────────────────────────────────────────────────┐
    │  [Debug Bundle]                                     (top-right)  │
    ├───────────────────────┬──────────────────────────────────────────┤
    │  Accounts (list)      │  Tabs: Overview | Send | History         │
    │  [Add] [Remove]       │                                          │
    └───────────────────────┴──────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable


from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QButtonGroup,
    QRadioButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from animica_studio.models.wallet_models import (
    Account,
    BalanceState,
    PendingTx,
    format_amount,
    is_valid_address,
    parse_amount_to_wei,
    shorten_address,
)
from animica_studio.services.error_format import format_exception, format_rpc_error, safe_str
from animica_studio.services.job_runner import JobHandle, JobRunner
from animica_studio.services.tx_builder import estimate_fee
from animica_studio.services.wallet_service import WalletService
from animica_studio.services.signer_service import SigningNotAvailableError
from animica_studio.storage.config import Config
from animica_studio.util.cancel import CancelToken
from animica_studio.util.qt import qalive, qthread_running

log = logging.getLogger(__name__)

_REFRESH_DEBOUNCE_MS = 300  # ms to wait after profile switch before triggering balance refresh
_POLL_INTERVAL_MS = 15_000  # receipt poll


# ---------------------------------------------------------------------------
# Background worker helpers
# ---------------------------------------------------------------------------


class _Worker(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            val = self._fn()
            self.result.emit(val)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(format_exception(exc))
        finally:
            self.finished.emit()


def _run_in_thread(
    fn: Callable[[], Any],
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> QThread:
    """Run *fn* on a background thread; wire signals and return the thread."""
    thread = QThread()
    worker = _Worker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    if on_result:
        worker.result.connect(on_result)
    if on_error:
        worker.error.connect(on_error)
    thread.start()
    return thread


# ---------------------------------------------------------------------------
# Add Account Dialog
# ---------------------------------------------------------------------------


class _AddAccountDialog(QDialog):
    """Dialog to add a new watched account."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Account")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("My Account")
        form.addRow("Label:", self._label_edit)

        self._addr_edit = QLineEdit()
        self._addr_edit.setPlaceholderText("anim1…")
        form.addRow("Address:", self._addr_edit)

        layout.addLayout(form)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        label = self._label_edit.text().strip() or "Account"
        addr = self._addr_edit.text().strip()
        if not addr:
            self._error_label.setText("Address is required.")
            return
        if not is_valid_address(addr):
            self._error_label.setText(
                "Invalid address format. Must match anim1[a-z0-9]{10,}"
            )
            return
        self.accept()

    def get_values(self) -> tuple[str, str]:
        label = self._label_edit.text().strip() or "Account"
        addr = self._addr_edit.text().strip()
        return label, addr


# ---------------------------------------------------------------------------
# Create Wallet Dialog
# ---------------------------------------------------------------------------


class _CreateWalletDialog(QDialog):
    """Dialog to create a new wallet entry via backend wallet service."""

    create_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Wallet")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._is_busy = False

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("My Wallet")
        self._label_edit.returnPressed.connect(self._on_accept)
        form.addRow("Wallet label:", self._label_edit)

        layout.addLayout(form)

        scheme_box = QGroupBox("Signature Scheme")
        scheme_layout = QVBoxLayout(scheme_box)
        self._scheme_group = QButtonGroup(self)

        self._dilithium_radio = QRadioButton("Dilithium3 (Recommended)")
        self._dilithium_radio.setChecked(True)
        self._dilithium_desc = QLabel("Fast signing/verification and smaller signatures; lattice-based post-quantum scheme. Good default for most users.")
        self._dilithium_desc.setWordWrap(True)
        self._dilithium_desc.setStyleSheet("color: #9aa0a6;")

        self._sphincs_radio = QRadioButton("SPHINCS+ 128s (sphincs128s)")
        self._sphincs_desc = QLabel("Hash-based post-quantum scheme with very large signatures and slower signing; conservative security assumptions.")
        self._sphincs_desc.setWordWrap(True)
        self._sphincs_desc.setStyleSheet("color: #9aa0a6;")

        self._scheme_group.addButton(self._dilithium_radio)
        self._scheme_group.addButton(self._sphincs_radio)
        scheme_layout.addWidget(self._dilithium_radio)
        scheme_layout.addWidget(self._dilithium_desc)
        scheme_layout.addWidget(self._sphincs_radio)
        scheme_layout.addWidget(self._sphincs_desc)
        layout.addWidget(scheme_box)

        self._progress_text = QLabel("Creating wallet…")
        self._progress_text.setVisible(False)
        layout.addWidget(self._progress_text)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._create_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._cancel_btn = self._buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self._create_btn.setText("Create")
        self._create_btn.clicked.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _on_accept(self) -> None:
        if self._is_busy:
            return
        self._error_label.setText("")
        if not self._is_valid_label():
            return
        self.create_requested.emit(self.label, self.sig_scheme)

    def _is_valid_label(self) -> bool:
        label = self.label
        if not label:
            self._error_label.setText("Wallet label is required.")
            return False
        if len(label) > 32:
            self._error_label.setText("Wallet label must be 1–32 characters.")
            return False
        import re

        if not re.match(r"^[A-Za-z0-9 _-]{1,32}$", label):
            self._error_label.setText(
                "Use only letters, numbers, spaces, underscores, and hyphens."
            )
            return False
        return True

    @property
    def label(self) -> str:
        return self._label_edit.text().strip()

    @property
    def sig_scheme(self) -> str:
        return "sphincs128s" if self._sphincs_radio.isChecked() else "dilithium3"

    def set_busy(self, busy: bool) -> None:
        self._is_busy = busy
        self._label_edit.setEnabled(not busy)
        self._dilithium_radio.setEnabled(not busy)
        self._sphincs_radio.setEnabled(not busy)
        self._create_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(not busy)
        self._progress.setVisible(busy)
        self._progress_text.setVisible(busy)

    def set_error(self, message: str) -> None:
        self._error_label.setText(message)
# ---------------------------------------------------------------------------
# Overview tab
# ---------------------------------------------------------------------------


class _OverviewTab(QWidget):
    refresh_requested = Signal()

    def __init__(self, wallet_service: WalletService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wallet_service = wallet_service
        self._account: Account | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        self._addr_label = QLabel("—")
        self._addr_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        form.addRow("Address:", self._addr_label)

        self._balance_label = QLabel("—")
        form.addRow("Balance:", self._balance_label)

        self._scheme_label = QLabel("—")
        form.addRow("Signature:", self._scheme_label)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("📋 Copy Address")
        copy_btn.clicked.connect(self._copy_address)
        btn_row.addWidget(copy_btn)

        self._explorer_btn = QPushButton("🔗 Explorer")
        self._explorer_btn.clicked.connect(self._open_explorer)
        btn_row.addWidget(self._explorer_btn)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)
        layout.addStretch()

    def set_account(self, account: Account | None) -> None:
        self._account = account
        if account is None:
            self._addr_label.setText("—")
            self._balance_label.setText("—")
            self._scheme_label.setText("—")
            self._error_label.setText("")
            return
        self._addr_label.setText(account.address)
        self._scheme_label.setText(account.sig_scheme)

    def set_balance(self, state: BalanceState | None) -> None:
        if state is None:
            self._balance_label.setText("—")
            self._error_label.setText("")
            return
        if state.error:
            self._balance_label.setText("—")
            self._error_label.setText(f"Unavailable: {state.error}")
        else:
            self._balance_label.setText(state.formatted)
            self._error_label.setText("")

    def _copy_address(self) -> None:
        if self._account:
            QApplication.clipboard().setText(self._account.address)

    def _open_explorer(self) -> None:
        if self._account:
            url = self._wallet_service.explorer_url_for_address(self._account.address)
            QDesktopServices.openUrl(QUrl(url))


# ---------------------------------------------------------------------------
# Send tab
# ---------------------------------------------------------------------------


class _SendTab(QWidget):
    send_requested = Signal(dict)  # emits kwargs dict

    def __init__(self, wallet_service: WalletService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wallet_service = wallet_service
        self._account: Account | None = None
        self._active_thread: QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form = QFormLayout()
        self._from_label = QLabel("—")
        form.addRow("From:", self._from_label)

        self._to_edit = QLineEdit()
        self._to_edit.setPlaceholderText("anim1…")
        form.addRow("To:", self._to_edit)

        self._amount_edit = QLineEdit()
        self._amount_edit.setPlaceholderText("0.0")
        form.addRow("Amount (ANM):", self._amount_edit)

        self._memo_edit = QLineEdit()
        self._memo_edit.setPlaceholderText("Optional memo")
        form.addRow("Memo:", self._memo_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._preview_btn = QPushButton("👁 Preview")
        self._preview_btn.clicked.connect(self._on_preview)
        btn_row.addWidget(self._preview_btn)

        self._send_btn = QPushButton("🚀 Send")
        self._send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(self._send_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._result_area = QTextEdit()
        self._result_area.setReadOnly(True)
        self._result_area.setMaximumHeight(120)
        self._result_area.setVisible(False)
        layout.addWidget(self._result_area)

        self._explorer_btn = QPushButton("🔗 View on Explorer")
        self._explorer_btn.setVisible(False)
        self._explorer_btn.clicked.connect(self._open_tx_explorer)
        layout.addWidget(self._explorer_btn)
        layout.addStretch()

        self._last_tx_hash: str | None = None

    def set_account(self, account: Account | None) -> None:
        self._account = account
        if account:
            self._from_label.setText(f"{account.label} — {shorten_address(account.address)}")
        else:
            self._from_label.setText("—")
        self._status_label.setText("")
        self._result_area.setVisible(False)
        self._explorer_btn.setVisible(False)

    def _validate_inputs(self) -> tuple[str, int, str | None] | None:
        """Validate inputs; return (to_addr, amount_wei, memo) or None."""
        to_addr = self._to_edit.text().strip()
        amount_text = self._amount_edit.text().strip()
        memo = self._memo_edit.text().strip() or None

        if not to_addr:
            self._status_label.setText("❌ 'To' address is required.")
            return None
        if not is_valid_address(to_addr):
            self._status_label.setText("❌ Invalid 'To' address format.")
            return None
        if not amount_text:
            self._status_label.setText("❌ Amount is required.")
            return None
        try:
            amount_wei = parse_amount_to_wei(amount_text)
        except ValueError as exc:
            self._status_label.setText(f"❌ {format_exception(exc)}")
            return None

        return to_addr, amount_wei, memo

    def _on_preview(self) -> None:
        if self._account is None:
            self._status_label.setText("❌ Select an account first.")
            return
        inputs = self._validate_inputs()
        if inputs is None:
            return
        to_addr, amount_wei, memo = inputs
        summary_lines = [
            f"From   : {self._account.address}",
            f"To     : {to_addr}",
            f"Amount : {format_amount(amount_wei)}",
            f"Memo   : {memo or '(none)'}",
            f"Fee    : {format_amount(estimate_fee())} (estimated)",
        ]
        self._result_area.setText("\n".join(summary_lines))
        self._result_area.setVisible(True)
        self._status_label.setText("Preview ready — review and click Send.")

    def _on_send(self) -> None:
        if self._account is None:
            self._status_label.setText("❌ Select an account first.")
            return
        if qthread_running(self._active_thread):
            self._status_label.setText("⏳ Send already in progress…")
            return
        inputs = self._validate_inputs()
        if inputs is None:
            return
        to_addr, amount_wei, memo = inputs

        self._send_btn.setEnabled(False)
        self._status_label.setText("⏳ Sending…")
        self._result_area.setVisible(False)
        self._explorer_btn.setVisible(False)

        account = self._account
        wallet_service = self._wallet_service

        def _task() -> PendingTx:
            from animica_studio.storage.config import load_config  # noqa: PLC0415
            cfg = wallet_service._config
            rpc_url = _get_rpc_url(cfg)
            chain_id = _get_chain_id(cfg)
            return wallet_service.build_and_send(
                rpc_url=rpc_url,
                chain_id=chain_id,
                from_addr=account.address,
                to_addr=to_addr,
                amount_wei=amount_wei,
                memo=memo,
            )

        def _on_result(ptx: PendingTx) -> None:
            self._send_btn.setEnabled(True)
            if ptx.status == "FAILED":
                err_msg = ptx.error or "Unknown error"
                self._status_label.setText(f"❌ Send failed: {err_msg}")
                self._result_area.setText(f"Status: FAILED\nError: {err_msg}")
            else:
                hash_str = ptx.tx_hash or "(no hash)"
                self._last_tx_hash = ptx.tx_hash
                self._status_label.setText(f"✅ Sent! Tx hash: {hash_str}")
                self._result_area.setText(
                    f"Status : {ptx.status}\nTx hash: {hash_str}\nNonce  : {ptx.nonce}"
                )
                self._explorer_btn.setVisible(bool(ptx.tx_hash))
            self._result_area.setVisible(True)

        def _on_error(msg: str) -> None:
            self._send_btn.setEnabled(True)
            self._status_label.setText(f"❌ {msg}")
            self._result_area.setText(f"Error: {msg}")
            self._result_area.setVisible(True)

        self._active_thread = _run_in_thread(_task, _on_result, _on_error)

    def _open_tx_explorer(self) -> None:
        if self._last_tx_hash:
            url = self._wallet_service.explorer_url_for_tx(self._last_tx_hash)
            QDesktopServices.openUrl(QUrl(url))


# ---------------------------------------------------------------------------
# History tab
# ---------------------------------------------------------------------------


class _HistoryTab(QWidget):
    def __init__(self, wallet_service: WalletService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wallet_service = wallet_service
        self._account: Account | None = None
        self._active_thread: QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh Receipts")
        refresh_btn.clicked.connect(self._refresh_receipts)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Time", "To / From", "Amount", "Status", "Tx Hash"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        layout.addWidget(self._table)

    def set_account(self, account: Account | None) -> None:
        self._account = account
        self._reload_table()

    def _reload_table(self) -> None:
        self._table.setRowCount(0)
        if self._account is None:
            return
        txs = self._wallet_service.list_pending_txs(self._account.address)
        for ptx in txs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(ptx.created_ts))
            other = ptx.to_addr if ptx.from_addr == self._account.address else ptx.from_addr
            amount_str = format_amount(ptx.amount_wei)
            hash_str = ptx.tx_hash or "(pending)"
            for col, text in enumerate(
                [ts_str, shorten_address(other), amount_str, ptx.status, hash_str]
            ):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, ptx.tx_hash)
                self._table.setItem(row, col, item)

    def _refresh_receipts(self) -> None:
        if self._account is None:
            return
        if qthread_running(self._active_thread):
            return

        account = self._account
        wallet_service = self._wallet_service

        def _task() -> list[PendingTx]:
            cfg = wallet_service._config
            rpc_url = _get_rpc_url(cfg)
            pending = wallet_service.list_pending_txs(account.address)
            updated = []
            for ptx in pending:
                if ptx.status in ("PENDING", "SENT"):
                    updated.append(wallet_service.poll_receipt(ptx, rpc_url))
            return updated

        def _on_result(txs: list[PendingTx]) -> None:
            self._reload_table()

        self._active_thread = _run_in_thread(_task, _on_result, None)

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        item = self._table.item(row, 4)  # Tx Hash column
        if item:
            tx_hash = item.text()
            if tx_hash and tx_hash != "(pending)":
                url = self._wallet_service.explorer_url_for_tx(tx_hash)
                QDesktopServices.openUrl(QUrl(url))


# ---------------------------------------------------------------------------
# Wallet Page
# ---------------------------------------------------------------------------


def _get_rpc_url(config: Config) -> str:
    """Extract the active RPC URL from config."""
    active_id = config.active_profile_id
    for d in config.rpc_profiles:
        if isinstance(d, dict) and d.get("id") == active_id:
            url = d.get("node_rpc_url") or d.get("rpc_url") or ""
            if url:
                return url
    # Fall back to legacy profile
    try:
        return config.get_active_profile().rpc_url
    except Exception:  # noqa: BLE001
        return ""


def _get_chain_id(config: Config) -> int:
    """Extract the expected chain ID from config."""
    active_id = config.active_profile_id
    for d in config.rpc_profiles:
        if isinstance(d, dict) and d.get("id") == active_id:
            return int(d.get("chain_id_expected", 1))
    try:
        return config.get_active_profile().chain_id_expected
    except Exception:  # noqa: BLE001
        return 1


class WalletPage(QWidget):
    """Full multi-account wallet page."""

    def __init__(self, config: Config | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from animica_studio.storage.config import load_config  # noqa: PLC0415

        self._config = config or load_config()
        self._wallet_service = WalletService(self._config)
        self._selected_account: Account | None = None
        self._cancel_token: CancelToken = CancelToken()
        self._balance_thread: QThread | None = None
        self._poll_timer: QTimer | None = None
        self._active_threads: list[QThread] = []
        self._create_wallet_dialog: _CreateWalletDialog | None = None
        self._runner = JobRunner.instance()
        self._create_wallet_job: JobHandle | None = None

        self._build_ui()
        # Defer first refresh to after window is shown
        QTimer.singleShot(500, self._refresh_all)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar with debug bundle button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(12, 8, 12, 0)
        title = QLabel("💳  Wallet")
        title.setObjectName("placeholderLabel")
        top_bar.addWidget(title)
        top_bar.addStretch()

        self._create_wallet_btn = QPushButton("Create Wallet")
        self._create_wallet_btn.clicked.connect(self._on_create_wallet)
        self._create_wallet_btn.setDefault(False)
        top_bar.addWidget(self._create_wallet_btn)

        self._debug_btn = QPushButton("📦 Copy Debug Bundle")
        self._debug_btn.clicked.connect(self._copy_debug_bundle)
        top_bar.addWidget(self._debug_btn)
        root.addLayout(top_bar)

        # Global RPC error banner
        self._rpc_banner = QLabel("")
        self._rpc_banner.setObjectName("errorBanner")
        self._rpc_banner.setStyleSheet(
            "background: #fff3cd; color: #856404; padding: 4px 12px; border-radius: 4px;"
        )
        self._rpc_banner.setWordWrap(True)
        self._rpc_banner.setVisible(False)
        root.addWidget(self._rpc_banner)

        # Main splitter: left=accounts, right=tabs
        body = QHBoxLayout()
        body.setContentsMargins(12, 8, 12, 12)
        body.setSpacing(12)

        # Left panel
        left = QVBoxLayout()
        left.setSpacing(6)

        accounts_lbl = QLabel("Accounts")
        accounts_lbl.setStyleSheet("font-weight: bold;")
        left.addWidget(accounts_lbl)

        self._accounts_list = QListWidget()
        self._accounts_list.setMinimumWidth(200)
        self._accounts_list.setMaximumWidth(260)
        self._accounts_list.currentRowChanged.connect(self._on_account_selected)
        left.addWidget(self._accounts_list, stretch=1)

        acct_btn_row = QHBoxLayout()
        add_btn = QPushButton("＋ Add")
        add_btn.clicked.connect(self._on_add_account)
        acct_btn_row.addWidget(add_btn)
        remove_btn = QPushButton("✕ Remove")
        remove_btn.clicked.connect(self._on_remove_account)
        acct_btn_row.addWidget(remove_btn)
        left.addLayout(acct_btn_row)

        body.addLayout(left)

        # Right panel: tabs
        self._tabs = QTabWidget()
        self._overview_tab = _OverviewTab(self._wallet_service)
        self._overview_tab.refresh_requested.connect(self._refresh_selected)
        self._send_tab = _SendTab(self._wallet_service)
        self._history_tab = _HistoryTab(self._wallet_service)
        self._tabs.addTab(self._overview_tab, "Overview")
        self._tabs.addTab(self._send_tab, "Send")
        self._tabs.addTab(self._history_tab, "History")
        body.addWidget(self._tabs, stretch=1)

        root.addLayout(body, stretch=1)

        # Receipt poll timer
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_receipts)
        self._poll_timer.start(_POLL_INTERVAL_MS)

    # ------------------------------------------------------------------
    # Account list management
    # ------------------------------------------------------------------

    def _reload_accounts_list(self) -> None:
        """Repopulate the accounts QListWidget from the wallet service."""
        accounts = self._wallet_service.list_accounts()
        self._accounts_list.blockSignals(True)
        # Remember current selection by id
        prev_id = self._selected_account.id if self._selected_account else None
        self._accounts_list.clear()
        restore_row = 0
        for i, acc in enumerate(accounts):
            balance_state = self._wallet_service.get_cached_balance(acc.address)
            if balance_state and balance_state.error:
                bal_text = f"⚠ Unavailable"
            elif balance_state:
                bal_text = balance_state.formatted
            else:
                bal_text = "—"
            item = QListWidgetItem(f"{acc.label} [{acc.sig_scheme}]\n{shorten_address(acc.address)}  {bal_text}")
            item.setData(Qt.ItemDataRole.UserRole, acc.id)
            self._accounts_list.addItem(item)
            if acc.id == prev_id:
                restore_row = i

        self._accounts_list.blockSignals(False)
        if self._accounts_list.count() > 0:
            self._accounts_list.setCurrentRow(restore_row)
        else:
            self._selected_account = None
            self._overview_tab.set_account(None)
            self._send_tab.set_account(None)
            self._history_tab.set_account(None)

    def _on_account_selected(self, row: int) -> None:
        item = self._accounts_list.item(row)
        if item is None:
            return
        account_id = item.data(Qt.ItemDataRole.UserRole)
        account = self._wallet_service.get_account(account_id)
        self._selected_account = account
        self._overview_tab.set_account(account)
        self._send_tab.set_account(account)
        self._history_tab.set_account(account)
        if account:
            # Show cached balance immediately, then fetch fresh
            cached = self._wallet_service.get_cached_balance(account.address)
            self._overview_tab.set_balance(cached)
            self._refresh_selected()

    def _on_add_account(self) -> None:
        dlg = _AddAccountDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            label, addr = dlg.get_values()
            try:
                self._wallet_service.add_account(label, addr)
                self._reload_accounts_list()
                self._refresh_all()
            except ValueError as exc:
                QMessageBox.warning(self, "Add Account", format_exception(exc))

    def _on_remove_account(self) -> None:
        if self._selected_account is None:
            return
        acc = self._selected_account
        reply = QMessageBox.question(
            self,
            "Remove Account",
            f"Remove account '{acc.label}' ({shorten_address(acc.address)})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._wallet_service.remove_account(acc.id)
            self._selected_account = None
            self._reload_accounts_list()

    def _on_create_wallet(self) -> None:
        if self._create_wallet_job is not None:
            return

        dlg = _CreateWalletDialog(self)
        self._create_wallet_dialog = dlg
        dlg.destroyed.connect(self._on_create_wallet_dialog_destroyed)
        dlg.create_requested.connect(self._start_create_wallet)
        dlg.exec()

    def _start_create_wallet(self, label: str, sig_scheme: str) -> None:
        if self._create_wallet_job is not None:
            return
        if not qalive(self._create_wallet_dialog):
            return

        self._create_wallet_dialog.set_busy(True)
        self._create_wallet_btn.setEnabled(False)

        started = time.perf_counter()

        def _task() -> Account:
            return self._wallet_service.create_wallet(label, sig_scheme=sig_scheme)

        job = self._runner.run_callable(_task, timeout_s=60)
        self._create_wallet_job = job

        def _on_error(_job_id: str, msg: str, _details: str) -> None:
            log.error("WalletPage: create wallet failed: %s", msg)
            if qalive(self._create_wallet_dialog):
                self._create_wallet_dialog.set_error(msg)

        def _on_finished(_job_id: str, exit_code: int, payload: object) -> None:
            self._create_wallet_btn.setEnabled(True)
            if qalive(self._create_wallet_dialog):
                self._create_wallet_dialog.set_busy(False)
            self._create_wallet_job = None

            if exit_code != 0:
                return
            if not isinstance(payload, Account):
                if qalive(self._create_wallet_dialog):
                    self._create_wallet_dialog.set_error("Wallet created but invalid response payload received.")
                return
            account = payload
            elapsed = int((time.perf_counter() - started) * 1000)
            log.info("WalletPage: wallet created label=%s address=%s scheme=%s duration_ms=%s", account.label, account.address, account.sig_scheme, elapsed)
            self._reload_accounts_list()
            self._select_account_by_id(account.id)
            self._refresh_all()
            if qalive(self._create_wallet_dialog):
                self._create_wallet_dialog.accept()
            QMessageBox.information(self, "Wallet", f"Wallet created: {account.label}")

        job.error.connect(_on_error)
        job.finished.connect(_on_finished)

    def _on_create_wallet_dialog_destroyed(self) -> None:
        self._create_wallet_dialog = None

    def _select_account_by_id(self, account_id: str) -> None:
        for row in range(self._accounts_list.count()):
            item = self._accounts_list.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == account_id:
                self._accounts_list.setCurrentRow(row)
                return

    # ------------------------------------------------------------------
    # Balance refresh
    # ------------------------------------------------------------------

    def _refresh_all(self) -> None:
        """Refresh balances for all accounts (off UI thread)."""
        rpc_url = _get_rpc_url(self._config)
        if not rpc_url:
            self._show_rpc_banner("No RPC URL configured — check your profile settings.")
            return

        # Cancel previous cycle
        self._cancel_token.cancel()
        new_token = CancelToken()
        self._cancel_token = new_token

        wallet_service = self._wallet_service

        def _task() -> dict[str, BalanceState]:
            return wallet_service.refresh_all_balances(rpc_url, cancel=new_token)

        def _on_result(results: dict[str, BalanceState]) -> None:
            # Check if any errors occurred — show/hide banner
            errors = [s for s in results.values() if s.error]
            if errors and len(errors) == len(results):
                self._show_rpc_banner(
                    f"RPC unavailable: {errors[0].error}"
                )
            elif errors:
                self._hide_rpc_banner()
            else:
                self._hide_rpc_banner()
            self._reload_accounts_list()
            # Refresh overview for selected account
            if self._selected_account:
                bal = wallet_service.get_cached_balance(self._selected_account.address)
                self._overview_tab.set_balance(bal)

        def _on_error(msg: str) -> None:
            self._show_rpc_banner(f"Balance refresh failed: {msg}")

        t = _run_in_thread(_task, _on_result, _on_error)
        self._balance_thread = t
        self._active_threads.append(t)
        # Prune finished threads
        self._active_threads = [t for t in self._active_threads if qthread_running(t)]

    def _refresh_selected(self) -> None:
        """Refresh balance for the currently selected account only."""
        if self._selected_account is None:
            return
        rpc_url = _get_rpc_url(self._config)
        if not rpc_url:
            return

        account = self._selected_account
        wallet_service = self._wallet_service

        def _task() -> BalanceState:
            return wallet_service.fetch_balance(account.address, rpc_url)

        def _on_result(state: BalanceState) -> None:
            self._overview_tab.set_balance(state)
            self._reload_accounts_list()

        _run_in_thread(_task, _on_result, None)

    def _poll_receipts(self) -> None:
        """Background receipt polling for pending txs."""
        if self._selected_account is None:
            return
        rpc_url = _get_rpc_url(self._config)
        if not rpc_url:
            return
        account = self._selected_account
        wallet_service = self._wallet_service

        def _task() -> None:
            pending = wallet_service.list_pending_txs(account.address)
            for ptx in pending:
                if ptx.status in ("PENDING", "SENT"):
                    wallet_service.poll_receipt(ptx, rpc_url)

        _run_in_thread(_task, None, None)

    # ------------------------------------------------------------------
    # Profile switch
    # ------------------------------------------------------------------

    def on_profile_changed(self, profile: Any) -> None:
        """Called by MainWindow when the active profile changes."""
        log.debug("WalletPage: profile changed — refreshing")
        # Cancel any in-flight requests
        self._cancel_token.cancel()
        self._cancel_token = CancelToken()
        # Clear stale balance cache so old-profile balances are not shown
        self._wallet_service.clear_balance_cache()
        # Re-read RPC config (config object is shared, already updated)
        QTimer.singleShot(_REFRESH_DEBOUNCE_MS, self._refresh_all)

    # ------------------------------------------------------------------
    # RPC banner helpers
    # ------------------------------------------------------------------

    def _show_rpc_banner(self, msg: str) -> None:
        self._rpc_banner.setText(f"⚠ {msg}")
        self._rpc_banner.setVisible(True)

    def _hide_rpc_banner(self) -> None:
        self._rpc_banner.setVisible(False)

    # ------------------------------------------------------------------
    # Debug bundle
    # ------------------------------------------------------------------

    def _copy_debug_bundle(self) -> None:
        from animica_studio.services.debug_bundle import collect_debug_bundle  # noqa: PLC0415
        from animica_studio.services.diagnostics import diagnostics  # noqa: PLC0415

        try:
            bundle = collect_debug_bundle(
                config=self._config,
                diagnostics=diagnostics,
                wallet_service=self._wallet_service,
            )
            QApplication.clipboard().setText(bundle)
            self._debug_btn.setText("✅ Copied!")
            QTimer.singleShot(2000, lambda: self._debug_btn.setText("📦 Copy Debug Bundle"))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Debug Bundle", f"Failed to collect bundle: {format_exception(exc)}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cancel_token.cancel()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        if self._create_wallet_job is not None:
            self._runner.cancel(self._create_wallet_job.job_id)
            self._create_wallet_job = None
        super().closeEvent(event)
