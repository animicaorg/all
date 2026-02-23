from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer, Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
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
from animica_studio.models.wallet_models import is_valid_address, shorten_address
from animica_studio.services.balance_service import BalanceResult, BalanceService
from animica_studio.services.wallet_repository import WalletRecord, WalletRepository
from animica_studio.storage.config import Config, load_config

log = logging.getLogger(__name__)


class _CreateWalletDialog(QDialog):
    """Kept minimal for compatibility + validation tests."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Wallet")
        layout = QVBoxLayout(self)
        self._label_edit = QLineEdit()
        self._label_edit.textChanged.connect(self._update_create_button_state)
        layout.addWidget(self._label_edit)
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._create_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._create_btn.setText("Create")
        self._buttons.rejected.connect(self.reject)
        self._buttons.accepted.connect(self.accept)
        layout.addWidget(self._buttons)
        self._update_create_button_state()

    def _update_create_button_state(self) -> None:
        text = self._label_edit.text().strip()
        valid = bool(text) and all(c.isalnum() or c in " _-" for c in text)
        self._create_btn.setEnabled(valid)


@dataclass
class _WalletUiState:
    wallet: WalletRecord
    balance_text: str = "Unavailable"
    reason: str = "Not fetched yet"


class WalletPageController(QWidget):
    wallets_loaded = Signal(list)


class WalletPage(QWidget):
    open_settings_requested = Signal()
    run_in_console_requested = Signal(str)

    def __init__(self, config: Config | None = None, parent: QWidget | None = None, *, safe_mode: bool = False) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        self._safe_mode = safe_mode
        self._repository = WalletRepository()
        self._balance_service = BalanceService(self)
        self._wallet_rows: list[_WalletUiState] = []
        self._selected_address: str | None = None

        self._refresh_tail_timer = QTimer(self)
        self._refresh_tail_timer.setSingleShot(True)
        self._refresh_tail_timer.timeout.connect(self._fetch_remaining)

        self._watcher = QFileSystemWatcher(self)
        wallets_file = str(self._repository.wallets_path)
        if Path(wallets_file).exists():
            self._watcher.addPath(wallets_file)
        self._watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self.refresh_wallets))

        self._build_ui()
        self._balance_service.balance_ready.connect(self._on_balance_ready)
        self._balance_service.rpc_status_changed.connect(self._on_rpc_status)
        QTimer.singleShot(0, self.refresh_wallets)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("💳 Wallets"))
        self._status_chip = QLabel("RPC Unknown")
        top.addWidget(self._status_chip)
        top.addStretch()
        self._refresh_wallets_btn = QPushButton("Refresh wallets")
        self._refresh_wallets_btn.clicked.connect(self.refresh_wallets)
        top.addWidget(self._refresh_wallets_btn)
        self._refresh_all_btn = QPushButton("Refresh all balances")
        self._refresh_all_btn.clicked.connect(lambda: self.refresh_all_balances(force=True))
        top.addWidget(self._refresh_all_btn)
        root.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search label or address")
        self._search.textChanged.connect(self._render_wallet_list)
        left_layout.addWidget(self._search)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selected)
        left_layout.addWidget(self._list, 1)
        split.addWidget(left)

        right = QWidget()
        r = QVBoxLayout(right)
        self._empty = QLabel("")
        self._empty.setWordWrap(True)
        r.addWidget(self._empty)
        self._detail_label = QLabel("No wallet selected")
        self._detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        r.addWidget(self._detail_label)
        self._balance_big = QLabel("Unavailable")
        self._balance_big.setStyleSheet("font-size: 24px; font-weight: 600;")
        r.addWidget(self._balance_big)
        self._reason = QLabel("Select an account")
        r.addWidget(self._reason)
        row = QHBoxLayout()
        self._retry_one = QPushButton("Refresh balance")
        self._retry_one.clicked.connect(self._refresh_selected_balance)
        row.addWidget(self._retry_one)
        self._explorer_btn = QPushButton("View on Explorer")
        self._explorer_btn.clicked.connect(self._open_explorer)
        row.addWidget(self._explorer_btn)
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._send_selected)
        row.addWidget(self._send_btn)
        r.addLayout(row)
        split.addWidget(right)
        split.setSizes([430, 530])

    def on_profile_changed(self, profile: RpcProfile) -> None:  # called by main window
        _ = profile
        self.refresh_all_balances(force=True)

    def refresh_wallets(self) -> None:
        wallets = self._repository.load_wallets()
        self._wallet_rows = [_WalletUiState(w) for w in wallets]
        if self._repository.last_error and not wallets:
            self._empty.setText(f"Wallet file invalid. {self._repository.last_error}")
        elif not wallets:
            self._empty.setText("No wallets found")
        else:
            self._empty.setText("")
        self._render_wallet_list()
        self._staged_balance_fetch()

    def _render_wallet_list(self) -> None:
        needle = self._search.text().strip().lower()
        self._list.clear()
        filtered = [
            row for row in self._wallet_rows if not needle or needle in row.wallet.label.lower() or needle in row.wallet.address.lower()
        ]
        for row in filtered:
            scheme = (row.wallet.sig_scheme or "unknown").lower()
            if "dilith" in scheme:
                scheme_label = "Dilithium3"
            elif "sphincs" in scheme:
                scheme_label = "SPHINCS+ 128s"
            else:
                scheme_label = row.wallet.sig_scheme or "Unknown"
            text = f"{row.wallet.label}\n{shorten_address(row.wallet.address)}   {scheme_label}\n{row.balance_text}"
            item = QListWidgetItem(text)
            if row.reason:
                item.setToolTip(row.reason)
            item.setData(Qt.ItemDataRole.UserRole, row.wallet.address)
            self._list.addItem(item)

    def _on_selected(self, index: int) -> None:
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
        self._detail_label.setText(row.wallet.address)
        self._balance_big.setText(row.balance_text)
        self._reason.setText(row.reason if row.balance_text == "Unavailable" else "")

    def _active_profile(self) -> RpcProfile:
        return self._config.get_active_profile()

    def _staged_balance_fetch(self) -> None:
        if not self._wallet_rows:
            return
        profile = self._active_profile()
        selected = self._selected_address or self._wallet_rows[0].wallet.address
        self._balance_service.get_balance(selected, profile, force_refresh=False)
        top = [w.wallet.address for w in self._wallet_rows[:5] if w.wallet.address != selected]
        for addr in top:
            QTimer.singleShot(120, lambda a=addr: self._balance_service.get_balance(a, profile, force_refresh=False))
        self._refresh_tail_timer.start(500)

    def _fetch_remaining(self) -> None:
        profile = self._active_profile()
        for idx, row in enumerate(self._wallet_rows[5:], start=0):
            QTimer.singleShot(180 * idx, lambda a=row.wallet.address: self._balance_service.get_balance(a, profile, force_refresh=False))

    def refresh_all_balances(self, *, force: bool) -> None:
        profile = self._active_profile()
        for idx, row in enumerate(self._wallet_rows):
            QTimer.singleShot(100 * idx, lambda a=row.wallet.address: self._balance_service.get_balance(a, profile, force_refresh=force))

    def _refresh_selected_balance(self) -> None:
        if not self._selected_address:
            return
        self._balance_service.get_balance(self._selected_address, self._active_profile(), force_refresh=True)

    def _on_balance_ready(self, address: str, result: BalanceResult) -> None:
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
        self._status_chip.setText("RPC Online" if ok else "RPC Offline")
        self._status_chip.setToolTip(reason)

    def _open_explorer(self) -> None:
        if not self._selected_address:
            return
        profile = self._active_profile()
        base = (profile.explorer_base_url or "").strip().rstrip("/")
        if not base:
            QMessageBox.information(self, "Explorer", "Explorer URL not configured")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/address/{self._selected_address}"))

    def _send_selected(self) -> None:
        if self._selected_address:
            self.run_in_console_requested.emit(f"tx send --from {self._selected_address}")

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self._refresh_tail_timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._refresh_tail_timer.stop()
        self._balance_service.shutdown()
        super().closeEvent(event)
