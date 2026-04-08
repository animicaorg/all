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
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.wallet_models import shorten_address
from animica_studio.services.activity_store import ActivityStore
from animica_studio.services.balance_service import BalanceResult, BalanceService
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.services.tx_service import TxService
from animica_studio.services.wallet_repository import WalletRecord, WalletRepository
from animica_studio.services.wallet_service import WalletService
from animica_studio.services.workers import WorkerThread, run_in_threadpool
from animica_studio.storage.config import Config, load_config
from animica_studio.util.paths import animica_wallets_file
from animica_studio.util.threading_guard import assert_ui_thread

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
        self.resize(480, 280)

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

        self._alg_help = QLabel("")
        self._alg_help.setWordWrap(True)
        self._alg_help.setStyleSheet("color: #8f99a5;")
        form.addRow("", self._alg_help)

        self._allow_insecure_fallback = QCheckBox("Allow insecure fallback when native PQ libraries are unavailable")
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
        else:
            self._validation.setText("")
            self._create_btn.setEnabled(True)

        if self.signature_scheme() == "dilithium3":
            self._alg_help.setText("Balanced default. Fast enough for normal desktop use and the recommended first choice.")
        else:
            self._alg_help.setText("More conservative but heavier. Use this if you specifically need a SPHINCS+ wallet.")

    def wallet_label(self) -> str:
        return self._label_edit.text().strip()

    def signature_scheme(self) -> str:
        return str(self._alg_combo.currentData() or "dilithium3")

    def allow_insecure_fallback(self) -> bool:
        return self._allow_insecure_fallback.isChecked()


class _WalletRowWidget(QFrame):
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

        middle = QVBoxLayout()
        label = QLabel(row.wallet.label)
        label.setStyleSheet("font-weight: 700; font-size: 14px;")
        middle.addWidget(label)
        address = QLabel(shorten_address(row.wallet.address))
        address.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; color: #8f99a5;")
        middle.addWidget(address)
        layout.addLayout(middle, 1)

        right = QVBoxLayout()
        balance = QLabel(row.balance_text)
        balance.setStyleSheet("font-size: 15px; font-weight: 700;")
        if row.reason:
            balance.setToolTip(row.reason)
        right.addWidget(balance, alignment=Qt.AlignmentFlag.AlignRight)
        scheme = QLabel(self._scheme_label(row.wallet.sig_scheme))
        scheme.setObjectName("schemeBadge")
        right.addWidget(scheme, alignment=Qt.AlignmentFlag.AlignRight)
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


class WalletPage(QWidget):
    open_settings_requested = Signal()
    send_requested = Signal(str)

    def __init__(
        self,
        config: Config | None = None,
        parent: QWidget | None = None,
        *,
        safe_mode: bool = False,
        status_service: StudioStatusService | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or load_config()
        self._safe_mode = safe_mode
        self._settings = settings_service or SettingsService(self._config)
        self._status_service = status_service or StudioStatusService(self._config, self._settings)
        self._repository = WalletRepository()
        self._wallet_service = WalletService(self._config)
        self._balance_service = BalanceService(self)
        self._wallet_rows: list[_WalletUiState] = []
        self._selected_address: str | None = self._settings.last_selected_wallet()
        self._create_wallet_thread: WorkerThread | None = None
        self._send_thread: WorkerThread | None = None
        self._history_job = None

        self._watcher = QFileSystemWatcher(self)
        wallets_file = str(animica_wallets_file())
        if Path(wallets_file).exists():
            self._watcher.addPath(wallets_file)
        self._watcher.fileChanged.connect(lambda _p: QTimer.singleShot(300, self.refresh_wallets))

        self._build_ui()
        self._balance_service.balance_ready.connect(self._on_balance_ready)
        self._balance_service.rpc_status_changed.connect(self._on_rpc_status)
        self._startup_refresh_scheduled = False

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Wallet")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel("Create, import, send, receive, and review recent transactions in one place.")
        subtitle.setStyleSheet("color: #8f99a5;")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self._status_chip = QLabel("RPC Unknown")
        self._status_chip.setObjectName("statusChip")
        header.addWidget(self._status_chip)

        self._total_balance = QLabel("Total: —")
        self._total_balance.setStyleSheet("font-size: 16px; font-weight: 700;")
        header.addWidget(self._total_balance)

        for label, slot in [
            ("Refresh", self.refresh_wallets),
            ("Import Wallet File", self._on_import_wallet),
            ("Create Wallet", self._on_create_wallet),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            header.addWidget(btn)
            if label == "Create Wallet":
                self._create_wallet_btn = btn
        root.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(1)
        root.addWidget(split, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search wallets by label or address")
        self._search.textChanged.connect(self._render_wallet_list)
        left_layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setObjectName("walletList")
        self._list.currentRowChanged.connect(self._on_selected)
        left_layout.addWidget(self._list, 1)
        split.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(12)

        self._detail_label = QLabel("No wallet selected")
        self._detail_label.setStyleSheet("font-size: 24px; font-weight: 700;")
        right_layout.addWidget(self._detail_label)

        addr_row = QHBoxLayout()
        self._detail_address = QLabel("Create or import a wallet to begin")
        self._detail_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail_address.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; color: #8f99a5;")
        addr_row.addWidget(self._detail_address, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_selected)
        addr_row.addWidget(copy_btn)
        right_layout.addLayout(addr_row)

        self._balance_big = QLabel("Unavailable")
        self._balance_big.setStyleSheet("font-size: 34px; font-weight: 700;")
        right_layout.addWidget(self._balance_big)

        self._reason = QLabel("Select an account to see details.")
        self._reason.setStyleSheet("color: #8f99a5;")
        self._reason.setWordWrap(True)
        right_layout.addWidget(self._reason)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        self._tabs.addTab(self._build_send_tab(), "Send")
        self._tabs.addTab(self._build_receive_tab(), "Receive")
        self._tabs.addTab(self._build_history_tab(), "History")
        self._tabs.addTab(self._build_contacts_tab(), "Contacts")
        right_layout.addWidget(self._tabs, 1)

        split.addWidget(right)
        split.setSizes([420, 760])

        self.setStyleSheet(
            """
            #walletList { border: 1px solid #2a333e; border-radius: 12px; background: #11161e; }
            #walletRow { border: 1px solid #2a333e; border-radius: 10px; background: #151c25; }
            #walletRow:hover { background: #1b2330; border-color: #3a4553; }
            #schemeBadge { background: #243042; color: #d4def0; border-radius: 9px; padding: 2px 8px; font-size: 11px; }
            #statusChip { background: #202a35; border: 1px solid #334255; border-radius: 10px; padding: 4px 10px; font-weight: 600; }
            """
        )

    def _build_overview_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        self._overview_label = QLabel("Choose a wallet to see its address and recent activity.")
        self._overview_label.setWordWrap(True)
        self._overview_label.setStyleSheet("color: #8f99a5;")
        layout.addWidget(self._overview_label)
        btn_row = QHBoxLayout()
        for label, slot in [
            ("Send", self.focus_send),
            ("Receive", self.focus_receive),
            ("View on Explorer", self._open_explorer),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(1)
        return page

    def _build_send_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()

        self._send_from = QLabel("No wallet selected")
        self._send_from.setStyleSheet("font-weight: 600;")
        form.addRow("From", self._send_from)

        self._contact_combo = QComboBox()
        self._contact_combo.addItem("Choose a saved contact (optional)", "")
        self._contact_combo.currentIndexChanged.connect(self._apply_selected_contact)
        form.addRow("Saved contact", self._contact_combo)

        self._send_to = QLineEdit()
        self._send_to.setPlaceholderText("anim1… recipient address")
        form.addRow("To", self._send_to)

        self._send_amount = QLineEdit()
        self._send_amount.setPlaceholderText("0.0")
        form.addRow("Amount", self._send_amount)

        self._save_contact_check = QCheckBox("Save this recipient to Contacts after a successful send")
        form.addRow("", self._save_contact_check)

        self._send_validation = QLabel("")
        self._send_validation.setWordWrap(True)
        self._send_validation.setStyleSheet("color: #d9534f;")
        form.addRow("", self._send_validation)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._on_send)
        buttons.addWidget(self._send_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_send_form)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._send_log = QTextEdit()
        self._send_log.setReadOnly(True)
        self._send_log.setMinimumHeight(160)
        layout.addWidget(self._send_log, 1)
        return page

    def _build_receive_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._receive_text = QLabel("Select a wallet to receive funds.")
        self._receive_text.setWordWrap(True)
        self._receive_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._receive_text.setStyleSheet("font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 18px;")
        layout.addWidget(self._receive_text)
        row = QHBoxLayout()
        for label, slot in [
            ("Copy Address", self._copy_selected),
            ("View on Explorer", self._open_explorer),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        refresh_btn = QPushButton("Refresh History")
        refresh_btn.clicked.connect(self._refresh_history)
        layout.addWidget(refresh_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        self._history_text = QTextEdit()
        self._history_text.setReadOnly(True)
        layout.addWidget(self._history_text, 1)
        return page

    def _build_contacts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._contacts_list = QListWidget()
        layout.addWidget(self._contacts_list, 1)

        form = QFormLayout()
        self._contact_label_edit = QLineEdit()
        self._contact_address_edit = QLineEdit()
        self._contact_address_edit.setPlaceholderText("anim1…")
        form.addRow("Label", self._contact_label_edit)
        form.addRow("Address", self._contact_address_edit)
        layout.addLayout(form)

        row = QHBoxLayout()
        add_btn = QPushButton("Add Contact")
        add_btn.clicked.connect(self._add_contact)
        row.addWidget(add_btn)
        use_btn = QPushButton("Use in Send Tab")
        use_btn.clicked.connect(self._use_selected_contact)
        row.addWidget(use_btn)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_selected_contact)
        row.addWidget(remove_btn)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _ensure_ui_thread(self, fn, *args) -> bool:
        if assert_ui_thread():
            return True
        QTimer.singleShot(0, lambda: fn(*args))
        return False

    def on_profile_changed(self, profile) -> None:  # noqa: ANN001
        _ = profile
        self.refresh_wallets()

    def focus_create_wallet(self) -> None:
        self._on_create_wallet()

    def focus_send(self) -> None:
        self._tabs.setCurrentIndex(1)

    def focus_receive(self) -> None:
        self._tabs.setCurrentIndex(2)

    def set_from_wallet(self, address: str | None) -> None:
        if address:
            self._selected_address = address
            self._settings.set_last_selected_wallet(address)
        self._tabs.setCurrentIndex(1)
        self.refresh_wallets()

    def refresh_wallets(self) -> None:
        if not self._ensure_ui_thread(self.refresh_wallets):
            return
        wallets_path = str(self._repository.wallets_path)
        if Path(wallets_path).exists() and wallets_path not in self._watcher.files():
            self._watcher.addPath(wallets_path)
        wallets = self._repository.load_wallets()
        self._wallet_rows = [_WalletUiState(w) for w in wallets]
        if wallets and not self._selected_address:
            self._selected_address = wallets[0].address
        self._render_wallet_list()
        self._refresh_contacts()
        self._refresh_all_balances()
        self._refresh_history()
        self._update_selected_wallet_ui()

    def _render_wallet_list(self) -> None:
        needle = self._search.text().strip().lower()
        self._list.clear()
        filtered = [
            row for row in self._wallet_rows
            if not needle or needle in row.wallet.label.lower() or needle in row.wallet.address.lower()
        ]
        for row in filtered:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, row.wallet.address)
            widget = _WalletRowWidget(row)
            item.setSizeHint(widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)
            if row.reason:
                item.setToolTip(row.reason)
        if self._selected_address:
            for idx in range(self._list.count()):
                item = self._list.item(idx)
                if str(item.data(Qt.ItemDataRole.UserRole)) == self._selected_address:
                    self._list.setCurrentRow(idx)
                    break
        self._total_balance.setText(self._compute_total_balance_label())

    def _compute_total_balance_label(self) -> str:
        total = 0
        ok = False
        for row in self._wallet_rows:
            if not row.balance_text.endswith(" ANM"):
                continue
            number = row.balance_text.replace(" ANM", "").strip()
            try:
                total += int(float(number) * 1_000_000_000)
                ok = True
            except ValueError:
                continue
        if not self._wallet_rows:
            return "Total: 0 ANM"
        if not ok:
            return "Total: Unavailable"
        return f"Total: {total / 1_000_000_000:g} ANM"

    def _update_selected_wallet_ui(self) -> None:
        row = next((r for r in self._wallet_rows if r.wallet.address == self._selected_address), None)
        if row is None:
            self._detail_label.setText("No wallet selected")
            self._detail_address.setText("Create or import a wallet to begin")
            self._balance_big.setText("Unavailable")
            self._reason.setText(self._repository.last_error or "No wallets found yet.")
            self._overview_label.setText("Create or import a wallet to start using Studio.")
            self._send_from.setText("No wallet selected")
            self._receive_text.setText("Select a wallet to receive funds.")
            return
        self._detail_label.setText(row.wallet.label)
        self._detail_address.setText(row.wallet.address)
        self._balance_big.setText(row.balance_text)
        self._reason.setText(row.reason or f"Algorithm: {row.wallet.sig_scheme or 'unknown'}")
        self._overview_label.setText(
            f"Selected wallet: {row.wallet.label}\n"
            f"Address: {row.wallet.address}\n"
            f"Algorithm: {row.wallet.sig_scheme or 'unknown'}"
        )
        self._send_from.setText(f"{row.wallet.label} ({shorten_address(row.wallet.address)})")
        self._receive_text.setText(row.wallet.address)

    def _on_selected(self, index: int) -> None:
        if not self._ensure_ui_thread(self._on_selected, index):
            return
        if index < 0:
            return
        item = self._list.item(index)
        if not item:
            return
        self._selected_address = str(item.data(Qt.ItemDataRole.UserRole))
        self._settings.set_last_selected_wallet(self._selected_address)
        self._update_selected_wallet_ui()
        self._refresh_history()

    def _refresh_all_balances(self) -> None:
        profile = self._config.get_active_profile()
        for idx, row in enumerate(self._wallet_rows):
            QTimer.singleShot(
                120 * idx,
                lambda address=row.wallet.address: self._balance_service.get_balance(address, profile, force_refresh=True),
            )

    def _on_balance_ready(self, address: str, result: BalanceResult) -> None:
        if not self._ensure_ui_thread(self._on_balance_ready, address, result):
            return
        for row in self._wallet_rows:
            if row.wallet.address == address:
                row.balance_text = result.formatted or "Unavailable"
                row.reason = result.error_reason or ""
                break
        self._render_wallet_list()
        self._update_selected_wallet_ui()

    def _on_rpc_status(self, ok: bool, reason: str) -> None:
        if not self._ensure_ui_thread(self._on_rpc_status, ok, reason):
            return
        self._status_chip.setText("RPC Online" if ok else "RPC Offline")
        self._status_chip.setToolTip(reason)

    def _on_create_wallet(self) -> None:
        dlg = _CreateWalletDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        self._create_wallet_btn.setEnabled(False)
        self._status_chip.setText("Creating wallet…")
        self._create_wallet_thread = WorkerThread(
            self._status_service.create_wallet,
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
        self._settings.set_last_selected_wallet(self._selected_address)
        ActivityStore.instance().record_wallet_load("Wallet created", ok=True, detail=self._selected_address or "")
        self.refresh_wallets()

    def _on_wallet_create_error(self, message: str, _traceback: str) -> None:
        self._status_chip.setText("Create Failed")
        self._status_chip.setToolTip(message)
        QMessageBox.critical(self, "Create Wallet Failed", message)

    def _on_wallet_create_finished(self) -> None:
        self._create_wallet_btn.setEnabled(True)
        self._create_wallet_thread = None

    def _on_import_wallet(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self, "Select wallets.json", str(Path.home()), "JSON Files (*.json)")
        if not selected:
            return
        result = self._status_service.import_wallet_store(selected)
        if not result.ok:
            QMessageBox.warning(self, "Import Wallets", result.details or result.summary)
            return
        self._status_chip.setText("Wallets Imported")
        self._status_chip.setToolTip(result.details)
        self.refresh_wallets()

    def _validate_send_inputs(self) -> tuple[str, int] | None:
        if not self._selected_address:
            self._send_validation.setText("Select a source wallet first.")
            return None
        to_addr = self._send_to.text().strip()
        if not TxService.validate_to_address(to_addr):
            self._send_validation.setText("Recipient must be a valid Animica address.")
            return None
        try:
            amount_wei = TxService.parse_amount(self._send_amount.text().strip())
        except ValueError as exc:
            self._send_validation.setText(str(exc))
            return None
        self._send_validation.setText("")
        return to_addr, amount_wei

    def _on_send(self) -> None:
        validated = self._validate_send_inputs()
        if not validated:
            return
        to_addr, amount_wei = validated
        profile = self._config.get_active_profile()
        self._send_log.setPlainText("Building, signing, and submitting transaction…")
        self._send_btn.setEnabled(False)
        self._send_thread = WorkerThread(
            self._wallet_service.build_and_send,
            rpc_url=profile.rpc_url,
            chain_id=profile.chain_id_expected,
            from_addr=self._selected_address,
            to_addr=to_addr,
            amount_wei=amount_wei,
        )
        self._send_thread.worker.result.connect(self._on_send_result)
        self._send_thread.worker.error.connect(self._on_send_error)
        self._send_thread.worker.finished.connect(self._on_send_finished)
        self._send_thread.start()

    def _on_send_result(self, pending_tx) -> None:  # noqa: ANN001
        status = getattr(pending_tx, "status", "FAILED")
        tx_hash = getattr(pending_tx, "tx_hash", "")
        error = getattr(pending_tx, "error", "")
        if status in {"PENDING", "SENT", "CONFIRMED"}:
            self._send_log.setPlainText(f"Transaction submitted.\nStatus: {status}\nHash: {tx_hash or 'not reported yet'}")
            ActivityStore.instance().record_tx_send("Transaction submitted", ok=True, detail=tx_hash or "")
            if self._save_contact_check.isChecked():
                self._save_contact_from_send()
        else:
            self._send_log.setPlainText(f"Send failed.\n{error or 'Unknown error'}")
            ActivityStore.instance().record_tx_send("Transaction failed", ok=False, detail=error or "")
        self._refresh_history()

    def _on_send_error(self, message: str, _traceback: str) -> None:
        self._send_log.setPlainText(message)

    def _on_send_finished(self) -> None:
        self._send_btn.setEnabled(True)
        self._send_thread = None

    def _save_contact_from_send(self) -> None:
        address = self._send_to.text().strip()
        label = self._contact_label_edit.text().strip() or f"Recipient {address[:8]}"
        self._settings.add_wallet_contact(label, address)
        self._refresh_contacts()

    def _clear_send_form(self) -> None:
        self._send_to.clear()
        self._send_amount.clear()
        self._send_validation.setText("")
        self._send_log.clear()

    def _refresh_history(self) -> None:
        if self._history_job is not None or not self._selected_address:
            if not self._selected_address:
                self._history_text.setPlainText("Select a wallet to see recent transactions.")
            return
        self._history_job = run_in_threadpool(self._load_history_for_selected)
        self._history_job.signals.result.connect(self._apply_history)
        self._history_job.signals.error.connect(lambda message, _tb: self._history_text.setPlainText(message))
        self._history_job.signals.finished.connect(lambda: setattr(self, "_history_job", None))

    def _load_history_for_selected(self) -> str:
        profile = self._config.get_active_profile()
        txs = self._wallet_service.list_pending_txs(self._selected_address)
        lines: list[str] = []
        if not txs:
            return "No recent transactions for this wallet yet."
        for tx in txs[:12]:
            refreshed = self._wallet_service.poll_receipt(tx, profile.rpc_url)
            lines.append(
                f"[{refreshed.status}] {refreshed.amount_wei / 1_000_000_000:g} ANM → {refreshed.to_addr}\n"
                f"hash: {refreshed.tx_hash or 'pending'}"
            )
            if refreshed.error:
                lines.append(f"error: {refreshed.error}")
            lines.append("")
        return "\n".join(lines).strip()

    def _apply_history(self, text: str) -> None:
        self._history_text.setPlainText(text)

    def _refresh_contacts(self) -> None:
        contacts = self._settings.list_wallet_contacts()
        self._contact_combo.blockSignals(True)
        self._contact_combo.clear()
        self._contact_combo.addItem("Choose a saved contact (optional)", "")
        for row in contacts:
            self._contact_combo.addItem(f"{row['label']} ({shorten_address(row['address'])})", row["address"])
        self._contact_combo.blockSignals(False)
        self._contacts_list.clear()
        for row in contacts:
            self._contacts_list.addItem(f"{row['label']} — {row['address']}")

    def _apply_selected_contact(self) -> None:
        data = str(self._contact_combo.currentData() or "").strip()
        if data:
            self._send_to.setText(data)

    def _add_contact(self) -> None:
        label = self._contact_label_edit.text().strip()
        address = self._contact_address_edit.text().strip()
        if not label or not TxService.validate_to_address(address):
            QMessageBox.warning(self, "Contact", "Enter a label and a valid Animica address.")
            return
        self._settings.add_wallet_contact(label, address)
        self._contact_label_edit.clear()
        self._contact_address_edit.clear()
        self._refresh_contacts()

    def _remove_selected_contact(self) -> None:
        item = self._contacts_list.currentItem()
        if not item:
            return
        text = item.text()
        _, _, address = text.partition(" — ")
        self._settings.remove_wallet_contact(address.strip())
        self._refresh_contacts()

    def _use_selected_contact(self) -> None:
        item = self._contacts_list.currentItem()
        if not item:
            return
        text = item.text()
        _, _, address = text.partition(" — ")
        self._send_to.setText(address.strip())
        self.focus_send()

    def _copy_selected(self) -> None:
        if not self._selected_address:
            return
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._selected_address)

    def _open_explorer(self) -> None:
        if not self._selected_address:
            return
        profile = self._status_service.active_profile()
        base = str(getattr(profile, "explorer_base_url", "")).strip().rstrip("/")
        if not base:
            QMessageBox.information(self, "Explorer", "Explorer URL is not configured for the active profile.")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/address/{self._selected_address}"))

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self._startup_refresh_scheduled:
            self._startup_refresh_scheduled = True
            QTimer.singleShot(0, self.refresh_wallets)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        self._balance_service.shutdown()
        super().closeEvent(event)
