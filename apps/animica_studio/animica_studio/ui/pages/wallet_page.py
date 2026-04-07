from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import RpcProfile
from animica_studio.models.wallet_models import shorten_address
from animica_studio.services.balance_service import BalanceResult, BalanceService
from animica_studio.services.wallet_service import WalletService
from animica_studio.services.wallet_repository import WalletRecord, WalletRepository
from animica_studio.services.workers import WorkerThread
from animica_studio.storage.config import Config, load_config
from animica_studio.util.threading_guard import assert_ui_thread
from animica_studio.util.paths import animica_wallets_file

log = logging.getLogger(__name__)


@dataclass
class _WalletUiState:
    wallet: WalletRecord
    balance_text: str = "Unavailable"
    reason: str = "Not fetched yet"


class _CreateWalletDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Wallet")
        self.setModal(True)
        self.resize(460, 220)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(10)

        self._label_edit = QLineEdit()
        self._label_edit.setPlaceholderText("wallet_01")
        form.addRow("Label", self._label_edit)

        self._alg_combo = QComboBox()
        self._alg_combo.addItem("Dilithium3", "dilithium3")
        self._alg_combo.addItem("SPHINCS+ 128s", "sphincs_shake_128s")
        form.addRow("Algorithm", self._alg_combo)

        self._allow_insecure_fallback = QCheckBox("Allow insecure fallback when native PQ libs are unavailable")
        form.addRow("", self._allow_insecure_fallback)

        self._wallet_path = QLabel(str(animica_wallets_file()))
        self._wallet_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._wallet_path.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; color: #8f99a5;")
        form.addRow("Wallet Store", self._wallet_path)

        self._validation = QLabel("")
        self._validation.setStyleSheet("color: #d9534f;")
        form.addRow("", self._validation)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._create_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._create_btn.setText("Create Wallet")
        self._create_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._label_edit.textChanged.connect(self._update_validation)
        self._alg_combo.currentIndexChanged.connect(self._update_validation)
        self._update_validation()

    def _update_validation(self) -> None:
        try:
            WalletService(Config()).validate_wallet_create_request(
                self._label_edit.text(),
                self.signature_scheme(),
            )
        except ValueError as exc:
            self._validation.setText(str(exc))
            self._create_btn.setEnabled(False)
            return
        self._validation.setText("")
        self._create_btn.setEnabled(True)

    def wallet_label(self) -> str:
        return self._label_edit.text().strip()

    def signature_scheme(self) -> str:
        return str(self._alg_combo.currentData() or "dilithium3")

    def allow_insecure_fallback(self) -> bool:
        return self._allow_insecure_fallback.isChecked()


class _WalletRowWidget(QFrame):
    send_clicked = Signal(str)

    def __init__(self, row: _WalletUiState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = row
        self.setObjectName("walletRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        avatar = QLabel("●")
        avatar.setStyleSheet(f"color: {self._avatar_color(row.wallet.address)}; font-size: 16px;")
        layout.addWidget(avatar)

        mid = QVBoxLayout()
        mid.setSpacing(3)
        label = QLabel(row.wallet.label)
        label.setStyleSheet("font-weight: 700; font-size: 14px;")
        mid.addWidget(label)

        meta = QHBoxLayout()
        addr = QLabel(shorten_address(row.wallet.address))
        addr.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; color: #93a0ad;")
        meta.addWidget(addr)

        scheme = QLabel(self._scheme_label(row.wallet.sig_scheme))
        scheme.setObjectName("schemeBadge")
        meta.addWidget(scheme)
        meta.addStretch(1)
        mid.addLayout(meta)
        layout.addLayout(mid, 1)

        right = QVBoxLayout()
        right.setSpacing(3)
        bal = QLabel(row.balance_text)
        bal.setStyleSheet("font-size: 16px; font-weight: 700;")
        if row.balance_text == "Unavailable":
            bal.setToolTip(row.reason)
        right.addWidget(bal, alignment=Qt.AlignmentFlag.AlignRight)

        action_row = QHBoxLayout()
        copy_btn = QToolButton()
        copy_btn.setText("Copy")
        copy_btn.clicked.connect(self._copy_address)
        action_row.addWidget(copy_btn)
        send_btn = QToolButton()
        send_btn.setText("Send")
        send_btn.clicked.connect(lambda: self.send_clicked.emit(self._row.wallet.address))
        action_row.addWidget(send_btn)
        right.addLayout(action_row)
        layout.addLayout(right)

    @staticmethod
    def _scheme_label(sig_scheme: str | None) -> str:
        scheme = (sig_scheme or "unknown").lower()
        if "dilith" in scheme:
            return "Dilithium3"
        if "sphincs" in scheme:
            return "SPHINCS+ 128s"
        return sig_scheme or "Unknown"

    @staticmethod
    def _avatar_color(address: str) -> str:
        digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
        hue = int(digest[:2], 16)
        color = QColor()
        color.setHsv(hue, 160, 220)
        return color.name()

    def _copy_address(self) -> None:
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._row.wallet.address)


class WalletPage(QWidget):
    open_settings_requested = Signal()
    send_requested = Signal(str)

    def __init__(self, config: Config | None = None, parent: QWidget | None = None, *, safe_mode: bool = False) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        self._safe_mode = safe_mode
        self._repository = WalletRepository()
        self._wallet_service = WalletService(self._config)
        self._balance_service = BalanceService(self)
        self._wallet_rows: list[_WalletUiState] = []
        self._selected_address: str | None = None
        self._create_wallet_thread: WorkerThread | None = None

        self._refresh_tail_timer = QTimer(self)
        self._refresh_tail_timer.setSingleShot(True)
        self._refresh_tail_timer.timeout.connect(self._fetch_remaining)

        self._watcher = QFileSystemWatcher(self)
        wallets_file = str(animica_wallets_file())
        if Path(wallets_file).exists():
            self._watcher.addPath(wallets_file)
        self._watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self.refresh_wallets))

        self._build_ui()
        self._balance_service.balance_ready.connect(self._on_balance_ready)
        self._balance_service.rpc_status_changed.connect(self._on_rpc_status)
        self._startup_refresh_scheduled = False
        self._refresh_in_flight = False

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_wrap = QVBoxLayout()
        title = QLabel("Wallet")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel("Manage local wallets, balances, and quick actions.")
        subtitle.setStyleSheet("color: #8f99a5;")
        title_wrap.addWidget(title)
        title_wrap.addWidget(subtitle)
        header.addLayout(title_wrap)
        header.addStretch(1)

        self._status_chip = QLabel("RPC Unknown")
        self._status_chip.setObjectName("statusChip")
        header.addWidget(self._status_chip)

        self._refresh_wallets_btn = QPushButton("Refresh")
        self._refresh_wallets_btn.clicked.connect(self.refresh_wallets)
        header.addWidget(self._refresh_wallets_btn)

        self._refresh_all_btn = QPushButton("Refresh Balances")
        self._refresh_all_btn.clicked.connect(lambda: self.refresh_all_balances(force=True))
        header.addWidget(self._refresh_all_btn)

        self._create_wallet_btn = QPushButton("Create Wallet")
        self._create_wallet_btn.clicked.connect(self._on_create_wallet)
        header.addWidget(self._create_wallet_btn)
        root.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        root.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search by label or address")
        self._search.textChanged.connect(self._render_wallet_list)
        left_layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("walletList")
        self._list.currentRowChanged.connect(self._on_selected)
        left_layout.addWidget(self._list, 1)
        split.addWidget(left)

        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(8, 0, 0, 0)
        r.setSpacing(12)

        self._detail_label = QLabel("No wallet selected")
        self._detail_label.setStyleSheet("font-size: 22px; font-weight: 700;")
        r.addWidget(self._detail_label)

        addr_row = QHBoxLayout()
        self._detail_address = QLabel("Select an account")
        self._detail_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_address.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; color: #9aa6b2;")
        addr_row.addWidget(self._detail_address, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_selected)
        addr_row.addWidget(copy_btn)
        r.addLayout(addr_row)

        self._balance_big = QLabel("Unavailable")
        self._balance_big.setStyleSheet("font-size: 34px; font-weight: 700;")
        r.addWidget(self._balance_big)

        self._reason = QLabel("Select an account")
        self._reason.setStyleSheet("color: #9aa6b2;")
        r.addWidget(self._reason)

        btns = QHBoxLayout()
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._send_selected)
        btns.addWidget(self._send_btn)

        self._retry_one = QPushButton("Refresh")
        self._retry_one.clicked.connect(self._refresh_selected_balance)
        btns.addWidget(self._retry_one)

        self._explorer_btn = QPushButton("View on Explorer")
        self._explorer_btn.clicked.connect(self._open_explorer)
        btns.addWidget(self._explorer_btn)
        btns.addStretch(1)
        r.addLayout(btns)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #2d3742;")
        r.addWidget(separator)

        activity_title = QLabel("Recent Activity")
        activity_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        r.addWidget(activity_title)
        self._activity_placeholder = QLabel("No recent activity available for this wallet yet.")
        self._activity_placeholder.setStyleSheet("color: #8f99a5;")
        self._activity_placeholder.setWordWrap(True)
        r.addWidget(self._activity_placeholder)
        r.addStretch(1)

        split.addWidget(right)
        split.setSizes([500, 620])

        self.setStyleSheet(
            """
            #walletList { border: 1px solid #2a333e; border-radius: 12px; background: #11161e; }
            #walletRow { border: 1px solid #2a333e; border-radius: 10px; background: #151c25; }
            #walletRow:hover { background: #1b2330; border-color: #3a4553; }
            #schemeBadge { background: #243042; color: #d4def0; border-radius: 9px; padding: 2px 8px; font-size: 11px; }
            #statusChip { background: #202a35; border: 1px solid #334255; border-radius: 10px; padding: 4px 10px; font-weight: 600; }
            """
        )


    def _ensure_ui_thread(self, fn, *args) -> bool:
        if assert_ui_thread():
            return True
        QTimer.singleShot(0, lambda: fn(*args))
        return False
    def on_profile_changed(self, profile: RpcProfile) -> None:
        _ = profile
        self.refresh_all_balances(force=True)

    def refresh_wallets(self) -> None:
        if not self._ensure_ui_thread(self.refresh_wallets):
            return
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        wallets_path = str(self._repository.wallets_path)
        if Path(wallets_path).exists() and wallets_path not in self._watcher.files():
            self._watcher.addPath(wallets_path)
        wallets = self._repository.load_wallets()
        self._wallet_rows = [_WalletUiState(w) for w in wallets]
        if not self._wallet_rows:
            self._selected_address = None
            self._detail_label.setText("No wallet selected")
            self._detail_address.setText("Create or import a wallet to begin")
            self._balance_big.setText("Unavailable")
            empty_reason = self._repository.last_error or "No wallets found. Create one to begin."
            self._reason.setText(empty_reason)
        self._render_wallet_list()
        if self._wallet_rows and not self._selected_address:
            self._selected_address = self._wallet_rows[0].wallet.address
        self._staged_balance_fetch()
        self._refresh_in_flight = False

    def _render_wallet_list(self) -> None:
        needle = self._search.text().strip().lower()
        self._list.clear()
        filtered = [
            row for row in self._wallet_rows if not needle or needle in row.wallet.label.lower() or needle in row.wallet.address.lower()
        ]
        for row in filtered:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.wallet.address)
            self._list.addItem(item)
            widget = _WalletRowWidget(row)
            widget.send_clicked.connect(self.send_requested.emit)
            item.setSizeHint(widget.sizeHint())
            self._list.setItemWidget(item, widget)
            if row.reason:
                item.setToolTip(row.reason)

        if self._selected_address:
            for idx in range(self._list.count()):
                item = self._list.item(idx)
                if str(item.data(Qt.ItemDataRole.UserRole)) == self._selected_address:
                    self._list.setCurrentRow(idx)
                    break

    def _on_selected(self, index: int) -> None:
        if not self._ensure_ui_thread(self._on_selected, index):
            return
        if index < 0:
            return
        item = self._list.item(index)
        if not item:
            return
        addr = str(item.data(Qt.ItemDataRole.UserRole))
        self._selected_address = addr
        row = next((r for r in self._wallet_rows if r.wallet.address == addr), None)
        if not row:
            return
        self._detail_label.setText(row.wallet.label)
        self._detail_address.setText(row.wallet.address)
        self._balance_big.setText(row.balance_text)
        self._reason.setText(row.reason if row.balance_text == "Unavailable" else "")

    def _active_profile(self):
        return self._config.get_active_profile()

    def _staged_balance_fetch(self) -> None:
        if not self._wallet_rows:
            self._reason.setText("No wallets found. Create one to begin.")
            return
        profile = self._active_profile()
        selected = self._selected_address or self._wallet_rows[0].wallet.address
        self._balance_service.get_balance(selected, profile, force_refresh=False)
        top = [w.wallet.address for w in self._wallet_rows[:5] if w.wallet.address != selected]
        for i, addr in enumerate(top):
            QTimer.singleShot(140 * i, lambda a=addr: self._balance_service.get_balance(a, profile, force_refresh=False))
        self._refresh_tail_timer.start(650)

    def _fetch_remaining(self) -> None:
        profile = self._active_profile()
        for idx, row in enumerate(self._wallet_rows[5:], start=0):
            QTimer.singleShot(200 * idx, lambda a=row.wallet.address: self._balance_service.get_balance(a, profile, force_refresh=False))

    def refresh_all_balances(self, *, force: bool) -> None:
        profile = self._active_profile()
        for idx, row in enumerate(self._wallet_rows):
            QTimer.singleShot(120 * idx, lambda a=row.wallet.address: self._balance_service.get_balance(a, profile, force_refresh=force))

    def _refresh_selected_balance(self) -> None:
        if not self._selected_address:
            return
        self._balance_service.get_balance(self._selected_address, self._active_profile(), force_refresh=True)

    def _on_balance_ready(self, address: str, result: BalanceResult) -> None:
        if not self._ensure_ui_thread(self._on_balance_ready, address, result):
            return
        for row in self._wallet_rows:
            if row.wallet.address != address:
                continue
            if result.ok:
                row.balance_text = result.formatted or "Unavailable"
                row.reason = ""
            else:
                row.balance_text = "Unavailable"
                row.reason = result.error_reason or "Balance unavailable"
            break
        self._render_wallet_list()
        if self._selected_address == address:
            self._on_selected(self._list.currentRow())

    def _on_rpc_status(self, ok: bool, reason: str) -> None:
        if not self._ensure_ui_thread(self._on_rpc_status, ok, reason):
            return
        self._status_chip.setText("RPC Online" if ok else "RPC Offline")
        self._status_chip.setToolTip(reason)

    def _open_explorer(self) -> None:
        if not self._selected_address:
            return
        profile = self._active_profile()
        base = str(getattr(profile, "explorer_base_url", "")).strip().rstrip("/")
        if not base:
            QMessageBox.information(self, "Explorer", "Explorer URL not configured")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/address/{self._selected_address}"))

    def _copy_selected(self) -> None:
        if not self._selected_address:
            return
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._selected_address)

    def _send_selected(self) -> None:
        if self._selected_address:
            self.send_requested.emit(self._selected_address)

    def _on_create_wallet(self) -> None:
        dlg = _CreateWalletDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        self._create_wallet_btn.setEnabled(False)
        self._status_chip.setText("Creating wallet…")
        self._status_chip.setToolTip("Launching animica wallet create")
        self._create_wallet_thread = WorkerThread(
            self._wallet_service.create_wallet,
            dlg.wallet_label(),
            dlg.signature_scheme(),
            allow_insecure_fallback=dlg.allow_insecure_fallback(),
        )
        self._create_wallet_thread.worker.result.connect(self._on_wallet_created)
        self._create_wallet_thread.worker.error.connect(self._on_wallet_create_error)
        self._create_wallet_thread.worker.finished.connect(self._on_wallet_create_finished)
        self._create_wallet_thread.start()

    def _on_wallet_created(self, account) -> None:  # noqa: ANN001
        self._selected_address = getattr(account, "address", None)
        self.refresh_wallets()
        self.refresh_all_balances(force=True)
        self._status_chip.setText("RPC Unknown")
        self._status_chip.setToolTip("Wallet created successfully")

    def _on_wallet_create_error(self, message: str, _traceback: str) -> None:
        self._status_chip.setText("Create Failed")
        self._status_chip.setToolTip(message)
        QMessageBox.critical(self, "Create Wallet Failed", message)

    def _on_wallet_create_finished(self) -> None:
        self._create_wallet_btn.setEnabled(True)
        self._create_wallet_thread = None


    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self._startup_refresh_scheduled:
            self._startup_refresh_scheduled = True
            QTimer.singleShot(0, self.refresh_wallets)

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._refresh_tail_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._refresh_tail_timer.stop()
        self._balance_service.shutdown()
        super().closeEvent(event)
