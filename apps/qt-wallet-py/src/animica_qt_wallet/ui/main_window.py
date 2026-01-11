from __future__ import annotations

import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from animica_qt_wallet.core.walletd_manager import WalletdManager


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Animica Wallet")
        self.resize(900, 600)

        self._walletd_manager = WalletdManager()
        self._walletd_task: asyncio.Task[None] | None = None
        self._walletd_status_timer = QTimer(self)
        self._walletd_status_timer.setInterval(2_000)
        self._walletd_status_timer.timeout.connect(self._refresh_walletd_status)
        self._node_status_timer = QTimer(self)
        self._node_status_timer.setInterval(2_000)
        self._node_status_timer.timeout.connect(self._refresh_node_status)
        self._node_logs_timer = QTimer(self)
        self._node_logs_timer.setInterval(2_000)
        self._node_logs_timer.timeout.connect(self._refresh_node_logs)
        self._accounts_timer = QTimer(self)
        self._accounts_timer.setInterval(5_000)
        self._accounts_timer.timeout.connect(self._refresh_accounts)
        self._chain_info_timer = QTimer(self)
        self._chain_info_timer.setInterval(3_000)
        self._chain_info_timer.timeout.connect(self._refresh_chain_info)
        self._wallet_locked = True
        self._selected_account = None

        self._build_menu()
        self._build_status_bar()
        self._build_central()

        self._walletd_task = asyncio.create_task(self._start_walletd())

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()

        file_menu = QMenu("File", self)
        settings_menu = QMenu("Settings", self)
        help_menu = QMenu("Help", self)

        menu_bar.addMenu(file_menu)
        menu_bar.addMenu(settings_menu)
        menu_bar.addMenu(help_menu)

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self._node_status = QLabel("Node: stopped", self)
        self._walletd_status = QLabel("Walletd: starting...", self)
        status.addWidget(self._node_status)
        status.addPermanentWidget(self._walletd_status)
        self.setStatusBar(status)

    def _build_central(self) -> None:
        central = QWidget(self)
        layout = QVBoxLayout(central)

        title = QLabel("Animica Wallet", self)
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        accounts_panel = self._build_accounts_panel()

        # Create tab widget
        tabs = QTabWidget(self)
        tabs.addTab(self._build_overview_tab(), "Overview")
        tabs.addTab(self._build_node_tab(), "Node")

        layout.addWidget(title)
        layout.addWidget(accounts_panel)
        layout.addWidget(tabs, stretch=1)

        self.setCentralWidget(central)

    def _build_overview_tab(self) -> QWidget:
        """Build the Overview tab showing chain status."""
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        # Chain status group
        chain_group = QGroupBox("Chain Status", self)
        chain_layout = QFormLayout(chain_group)

        self._chain_height_label = QLabel("—", self)
        self._chain_hash_label = QLabel("—", self)
        self._sync_status_label = QLabel("—", self)
        self._peer_count_label = QLabel("—", self)

        chain_layout.addRow("Height:", self._chain_height_label)
        chain_layout.addRow("Best Hash:", self._chain_hash_label)
        chain_layout.addRow("Sync Status:", self._sync_status_label)
        chain_layout.addRow("Peers:", self._peer_count_label)

        # Balance group
        balance_group = QGroupBox("Selected Account Balance", self)
        balance_layout = QVBoxLayout(balance_group)

        balance_row = QHBoxLayout()
        balance_row.addWidget(QLabel("Address:", self))
        self._balance_address_label = QLabel("—", self)
        self._balance_address_label.setStyleSheet("font-family: monospace;")
        balance_row.addWidget(self._balance_address_label)
        balance_row.addStretch(1)

        balance_value_row = QHBoxLayout()
        balance_value_row.addWidget(QLabel("Balance:", self))
        self._balance_value_label = QLabel("—", self)
        self._balance_value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        balance_value_row.addWidget(self._balance_value_label)
        balance_value_row.addStretch(1)

        balance_layout.addLayout(balance_row)
        balance_layout.addLayout(balance_value_row)

        # Status message
        self._chain_status_message = QLabel("", self)
        self._chain_status_message.setWordWrap(True)
        self._chain_status_message.setStyleSheet("color: #666;")

        layout.addWidget(chain_group)
        layout.addWidget(balance_group)
        layout.addWidget(self._chain_status_message)
        layout.addStretch(1)

        return widget

    def _build_node_tab(self) -> QWidget:
        """Build the Node tab with node controls and logs."""
        widget = QWidget(self)
        layout = QVBoxLayout(widget)

        subtitle = QLabel("Control your local node.", self)
        subtitle.setStyleSheet("color: #6b6b6b;")

        controls = QHBoxLayout()
        self._network_selector = QComboBox(self)
        self._network_selector.addItems(["mainnet", "testnet"])
        self._start_node_button = QPushButton("Start Node", self)
        self._stop_node_button = QPushButton("Stop Node", self)
        self._start_node_button.clicked.connect(self._handle_start_node)
        self._stop_node_button.clicked.connect(self._handle_stop_node)
        controls.addWidget(QLabel("Network:", self))
        controls.addWidget(self._network_selector)
        controls.addStretch(1)
        controls.addWidget(self._start_node_button)
        controls.addWidget(self._stop_node_button)

        self._logs_view = QPlainTextEdit(self)
        self._logs_view.setReadOnly(True)
        self._logs_view.setPlaceholderText("Node logs will appear here once the node starts.")

        layout.addWidget(subtitle)
        layout.addLayout(controls)
        layout.addWidget(QLabel("Node Logs", self))
        layout.addWidget(self._logs_view, stretch=2)

        return widget

    async def _start_walletd(self) -> None:
        status = await self._walletd_manager.ensure_running()
        if status.running:
            self._walletd_status.setText("Walletd: OK")
            self._walletd_status_timer.start()
            self._node_status_timer.start()
            self._node_logs_timer.start()
            self._accounts_timer.start()
            self._chain_info_timer.start()
            await self._update_accounts()
        else:
            self._walletd_status.setText("Walletd: unavailable")

    def _refresh_walletd_status(self) -> None:
        asyncio.create_task(self._update_walletd_status())

    async def _update_walletd_status(self) -> None:
        status = await self._walletd_manager.ensure_running()
        if status.running:
            self._walletd_status.setText("Walletd: OK")
        else:
            self._walletd_status.setText("Walletd: unavailable")

    def _refresh_node_status(self) -> None:
        asyncio.create_task(self._update_node_status())

    async def _update_node_status(self) -> None:
        try:
            status = await self._walletd_manager.get_node_status()
        except Exception:
            self._node_status.setText("Node: unavailable")
            return
        if status.running:
            label = f"Node: running ({status.network})"
        elif status.restarting:
            label = f"Node: restarting ({status.network})"
        else:
            label = "Node: stopped"
        self._node_status.setText(label)

    def _refresh_node_logs(self) -> None:
        asyncio.create_task(self._update_node_logs())

    async def _update_node_logs(self) -> None:
        try:
            lines = await self._walletd_manager.get_node_logs_tail(200)
        except Exception:
            return
        self._logs_view.setPlainText("\n".join(lines))
        self._logs_view.verticalScrollBar().setValue(self._logs_view.verticalScrollBar().maximum())

    def _handle_start_node(self) -> None:
        network = self._network_selector.currentText()
        asyncio.create_task(self._walletd_manager.start_node(network))

    def _handle_stop_node(self) -> None:
        asyncio.create_task(self._walletd_manager.stop_node())

    def _refresh_accounts(self) -> None:
        asyncio.create_task(self._update_accounts())

    async def _update_accounts(self) -> None:
        try:
            accounts = await self._walletd_manager.wallet_list_accounts()
        except Exception:
            self._wallet_locked = True
            self._set_accounts([])
            self._set_wallet_controls_locked(True)
            return
        self._wallet_locked = False
        self._set_accounts(accounts)
        self._set_wallet_controls_locked(False)

    def _set_accounts(self, accounts: list[dict[str, str]]) -> None:
        self._accounts_table.setRowCount(0)
        for row, acct in enumerate(accounts):
            self._accounts_table.insertRow(row)
            label_item = QTableWidgetItem(acct.get("label", ""))
            address_item = QTableWidgetItem(acct.get("address", ""))
            label_item.setFlags(label_item.flags() ^ Qt.ItemIsEditable)
            address_item.setFlags(address_item.flags() ^ Qt.ItemIsEditable)
            self._accounts_table.setItem(row, 0, label_item)
            self._accounts_table.setItem(row, 1, address_item)
        # Auto-select first account if available
        if accounts and self._selected_account is None:
            self._accounts_table.selectRow(0)
            self._selected_account = accounts[0].get("address")
            asyncio.create_task(self._refresh_selected_balance())

    def _set_wallet_controls_locked(self, locked: bool) -> None:
        self._unlock_button.setEnabled(locked)
        self._lock_button.setEnabled(not locked)
        self._create_account_button.setEnabled(not locked)
        self._import_account_button.setEnabled(not locked)
        self._show_secret_button.setEnabled(not locked)
        self._wallet_lock_status.setText("Locked" if locked else "Unlocked")

    def _handle_account_selection(self) -> None:
        """Handle account selection change."""
        address = self._selected_account_address()
        if address:
            self._selected_account = address
            asyncio.create_task(self._refresh_selected_balance())

    def _handle_unlock_wallet(self) -> None:
        dialog = UnlockDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        password = dialog.password
        asyncio.create_task(self._unlock_wallet(password))

    async def _unlock_wallet(self, password: str) -> None:
        try:
            await self._walletd_manager.wallet_unlock(password)
        except Exception as exc:
            QMessageBox.warning(self, "Unlock failed", str(exc))
            return
        await self._update_accounts()

    def _handle_lock_wallet(self) -> None:
        asyncio.create_task(self._lock_wallet())

    async def _lock_wallet(self) -> None:
        try:
            await self._walletd_manager.wallet_lock()
        except Exception as exc:
            QMessageBox.warning(self, "Lock failed", str(exc))
            return
        self._wallet_locked = True
        self._set_accounts([])
        self._set_wallet_controls_locked(True)

    def _handle_create_account(self) -> None:
        dialog = CreateAccountDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        asyncio.create_task(self._create_account(dialog.label))

    async def _create_account(self, label: str | None) -> None:
        try:
            await self._walletd_manager.wallet_create_account(label)
        except Exception as exc:
            QMessageBox.warning(self, "Create account failed", str(exc))
            return
        await self._update_accounts()

    def _handle_import_account(self) -> None:
        dialog = ImportAccountDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        asyncio.create_task(self._import_account(dialog.label, dialog.secret))

    async def _import_account(self, label: str | None, secret: str) -> None:
        try:
            await self._walletd_manager.wallet_import_account(label, secret)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", str(exc))
            return
        await self._update_accounts()

    def _handle_show_secret(self) -> None:
        address = self._selected_account_address()
        if not address:
            QMessageBox.information(self, "Show secret", "Select an account first.")
            return
        dialog = ShowSecretConfirmDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        asyncio.create_task(self._show_secret(address, dialog.password))

    async def _show_secret(self, address: str, password: str) -> None:
        try:
            await self._walletd_manager.wallet_unlock(password)
            account = await self._walletd_manager.wallet_export_account(address)
        except Exception as exc:
            QMessageBox.warning(self, "Show secret failed", str(exc))
            return
        secret = account.get("secret_key_hex", "")
        dialog = SecretRevealDialog(secret, self)
        dialog.exec()

    def _selected_account_address(self) -> str | None:
        row = self._accounts_table.currentRow()
        if row < 0:
            return None
        item = self._accounts_table.item(row, 1)
        if not item:
            return None
        return item.text().strip() or None

    def _refresh_chain_info(self) -> None:
        asyncio.create_task(self._update_chain_info())

    async def _update_chain_info(self) -> None:
        """Update chain status information."""
        try:
            node_status = await self._walletd_manager.get_node_status()
            if not node_status.running:
                self._chain_status_message.setText("Node is not running. Start the node to see chain status.")
                self._chain_height_label.setText("—")
                self._chain_hash_label.setText("—")
                self._sync_status_label.setText("Not connected")
                self._peer_count_label.setText("—")
                return

            # Get chain head
            try:
                head = await self._walletd_manager.chain_get_head()
                height = head.get("height", "—")
                block_hash = head.get("hash", "—")
                if isinstance(block_hash, str) and len(block_hash) > 20:
                    block_hash = block_hash[:10] + "..." + block_hash[-8:]
                
                self._chain_height_label.setText(str(height))
                self._chain_hash_label.setText(str(block_hash))
                self._sync_status_label.setText("Synced")
            except Exception as exc:
                self._chain_height_label.setText("—")
                self._chain_hash_label.setText("—")
                self._sync_status_label.setText("Error")
                self._chain_status_message.setText(f"Chain RPC error: {exc}")

            # Get peer count
            try:
                peer_count = await self._walletd_manager.net_peer_count()
                self._peer_count_label.setText(str(peer_count))
            except Exception:
                self._peer_count_label.setText("—")

            # Update balance for selected account
            await self._refresh_selected_balance()

        except Exception as exc:
            self._chain_status_message.setText(f"Failed to update chain info: {exc}")

    async def _refresh_selected_balance(self) -> None:
        """Update the balance for the currently selected account."""
        address = self._selected_account or self._selected_account_address()
        if not address:
            self._balance_address_label.setText("—")
            self._balance_value_label.setText("—")
            return

        self._balance_address_label.setText(address[:10] + "..." + address[-8:] if len(address) > 20 else address)

        try:
            node_status = await self._walletd_manager.get_node_status()
            if not node_status.running:
                self._balance_value_label.setText("—")
                return

            balance_hex = await self._walletd_manager.state_get_balance(address)
            # Convert hex balance to decimal (in nANM)
            balance = int(balance_hex, 16) if balance_hex.startswith("0x") else int(balance_hex)
            # Format as ANM (1 ANM = 10^9 nANM)
            balance_anm = balance / 1_000_000_000
            self._balance_value_label.setText(f"{balance_anm:.9f} ANM")
        except Exception as exc:
            self._balance_value_label.setText(f"Error: {exc}")

    def _build_accounts_panel(self) -> QGroupBox:
        group = QGroupBox("Accounts", self)
        layout = QVBoxLayout(group)

        status_row = QHBoxLayout()
        self._wallet_lock_status = QLabel("Locked", self)
        self._wallet_lock_status.setStyleSheet("font-weight: 600;")
        status_row.addWidget(QLabel("Wallet:", self))
        status_row.addWidget(self._wallet_lock_status)
        status_row.addStretch(1)
        self._unlock_button = QPushButton("Unlock", self)
        self._lock_button = QPushButton("Lock", self)
        self._unlock_button.clicked.connect(self._handle_unlock_wallet)
        self._lock_button.clicked.connect(self._handle_lock_wallet)
        status_row.addWidget(self._unlock_button)
        status_row.addWidget(self._lock_button)

        self._accounts_table = QTableWidget(0, 2, self)
        self._accounts_table.setHorizontalHeaderLabels(["Label", "Address"])
        header = self._accounts_table.horizontalHeader()
        header.setStretchLastSection(True)
        self._accounts_table.verticalHeader().setVisible(False)
        self._accounts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._accounts_table.setSelectionMode(QTableWidget.SingleSelection)
        self._accounts_table.itemSelectionChanged.connect(self._handle_account_selection)

        action_row = QHBoxLayout()
        self._create_account_button = QPushButton("Create Account", self)
        self._import_account_button = QPushButton("Import Account", self)
        self._show_secret_button = QPushButton("Show Secret", self)
        self._create_account_button.clicked.connect(self._handle_create_account)
        self._import_account_button.clicked.connect(self._handle_import_account)
        self._show_secret_button.clicked.connect(self._handle_show_secret)
        action_row.addWidget(self._create_account_button)
        action_row.addWidget(self._import_account_button)
        action_row.addStretch(1)
        action_row.addWidget(self._show_secret_button)

        layout.addLayout(status_row)
        layout.addWidget(self._accounts_table)
        layout.addLayout(action_row)

        self._set_wallet_controls_locked(True)
        return group

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._walletd_task:
            self._walletd_task.cancel()
        asyncio.create_task(self._walletd_manager.shutdown())
        super().closeEvent(event)


class UnlockDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Unlock Wallet")
        self.password = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._password_input = QLineEdit(self)
        self._password_input.setEchoMode(QLineEdit.Password)
        form.addRow("Password", self._password_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.password = self._password_input.text()
        if not self.password:
            QMessageBox.warning(self, "Missing password", "Enter your wallet password.")
            return
        self.accept()


class CreateAccountDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Account")
        self.label = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._label_input = QLineEdit(self)
        form.addRow("Label", self._label_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.label = self._label_input.text().strip()
        self.accept()


class ImportAccountDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Account")
        self.label = ""
        self.secret = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._label_input = QLineEdit(self)
        form.addRow("Label", self._label_input)
        layout.addLayout(form)

        instructions = QLabel(
            "Paste exported JSON or secret_key_hex:public_key_hex[:alg].", self
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self._secret_input = QPlainTextEdit(self)
        self._secret_input.setPlaceholderText("Secret (never logged)")
        layout.addWidget(self._secret_input)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.label = self._label_input.text().strip()
        self.secret = self._secret_input.toPlainText().strip()
        if not self.secret:
            QMessageBox.warning(self, "Missing secret", "Provide a secret to import.")
            return
        self.accept()


class ShowSecretConfirmDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Show Secret")
        self.password = ""

        layout = QVBoxLayout(self)
        warning = QLabel(
            "Warning: Revealing this secret grants full control of the account.\n"
            "Do not share it and close this window immediately after copying.",
            self,
        )
        warning.setStyleSheet("color: #b00020; font-weight: 600;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        form = QFormLayout()
        self._password_input = QLineEdit(self)
        self._password_input.setEchoMode(QLineEdit.Password)
        form.addRow("Re-enter password", self._password_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        self.password = self._password_input.text()
        if not self.password:
            QMessageBox.warning(self, "Missing password", "Re-enter your password.")
            return
        self.accept()


class SecretRevealDialog(QDialog):
    def __init__(self, secret: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Secret Key")
        self._seconds_left = 30

        layout = QVBoxLayout(self)
        self._countdown = QLabel("Secret will hide in 30s.", self)
        self._countdown.setStyleSheet("color: #b00020; font-weight: 600;")
        layout.addWidget(self._countdown)

        self._secret_view = QPlainTextEdit(self)
        self._secret_view.setReadOnly(True)
        self._secret_view.setPlainText(secret)
        layout.addWidget(self._secret_view)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1_000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._seconds_left -= 1
        if self._seconds_left <= 0:
            self._secret_view.setPlainText("")
            self._countdown.setText("Secret hidden.")
            self._timer.stop()
            return
        self._countdown.setText(f"Secret will hide in {self._seconds_left}s.")
