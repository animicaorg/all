from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from animica_qt_wallet.core.walletd_manager import WalletdManager


def _truncate_hash(h: str, prefix_len: int = 10, suffix_len: int = 8) -> str:
    """Truncate a hash for display (e.g., '0x1234...abcd')."""
    if len(h) <= prefix_len + suffix_len:
        return h
    return f"{h[:prefix_len]}...{h[-suffix_len:]}"


def _format_value(value: int) -> str:
    """Format a value in wei as a human-readable string."""
    # Convert wei to Animica (assuming 18 decimals like Ethereum)
    animica = value / 1_000_000_000_000_000_000
    return f"{animica:.6f} ANIM"


class TxDetailsDialog(QDialog):
    """Dialog to show full transaction details."""
    
    def __init__(self, tx_data: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transaction Details")
        self.resize(600, 400)
        
        layout = QVBoxLayout(self)
        
        # Create form with all details
        form = QFormLayout()
        
        # Transaction hash
        hash_label = QLabel(tx_data.get("tx_hash", ""), self)
        hash_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        hash_label.setStyleSheet("font-family: monospace;")
        form.addRow("Hash:", hash_label)
        
        # Status
        status = tx_data.get("status", "unknown")
        status_label = QLabel(status.upper(), self)
        if status == "confirmed":
            status_label.setStyleSheet("color: green; font-weight: bold;")
        elif status == "pending":
            status_label.setStyleSheet("color: orange; font-weight: bold;")
        elif status == "failed":
            status_label.setStyleSheet("color: red; font-weight: bold;")
        form.addRow("Status:", status_label)
        
        # Block number
        if tx_data.get("block_number") is not None:
            form.addRow("Block:", QLabel(str(tx_data["block_number"]), self))
        
        # From address
        from_label = QLabel(tx_data.get("from", ""), self)
        from_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        from_label.setStyleSheet("font-family: monospace;")
        form.addRow("From:", from_label)
        
        # To address
        to_addr = tx_data.get("to")
        if to_addr:
            to_label = QLabel(to_addr, self)
            to_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            to_label.setStyleSheet("font-family: monospace;")
            form.addRow("To:", to_label)
        else:
            form.addRow("To:", QLabel("(Contract Creation)", self))
        
        # Value
        value = tx_data.get("value", 0)
        form.addRow("Value:", QLabel(_format_value(value), self))
        
        # Gas details
        if tx_data.get("gas_limit"):
            form.addRow("Gas Limit:", QLabel(str(tx_data["gas_limit"]), self))
        if tx_data.get("max_fee"):
            form.addRow("Max Fee:", QLabel(f"{tx_data['max_fee']} wei", self))
        if tx_data.get("nonce") is not None:
            form.addRow("Nonce:", QLabel(str(tx_data["nonce"]), self))
        
        # Error
        if tx_data.get("error"):
            error_label = QLabel(tx_data["error"], self)
            error_label.setStyleSheet("color: red;")
            error_label.setWordWrap(True)
            form.addRow("Error:", error_label)
        
        # Timestamp
        import datetime
        ts = tx_data.get("timestamp")
        if ts:
            dt = datetime.datetime.fromtimestamp(ts)
            form.addRow("Time:", QLabel(dt.strftime("%Y-%m-%d %H:%M:%S"), self))
        
        layout.addLayout(form)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok, self)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class TransactionsTab(QWidget):
    """Transaction history tab."""
    
    def __init__(self, walletd_manager: WalletdManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._walletd_manager = walletd_manager
        self._current_address: str | None = None
        self._current_filter = "all"
        
        self._build_ui()
        
        # Auto-refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5_000)  # 5 seconds
        self._refresh_timer.timeout.connect(self._refresh_transactions)
        self._refresh_timer.start()
    
    def set_current_address(self, address: str | None) -> None:
        """Set the current address to filter transactions."""
        self._current_address = address
        self._refresh_transactions()
    
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        # Title
        title = QLabel("Transaction History", self)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        
        # Controls row
        controls = QHBoxLayout()
        
        # Status filter
        controls.addWidget(QLabel("Filter:", self))
        self._status_filter = QComboBox(self)
        self._status_filter.addItems(["All", "Pending", "Confirmed", "Failed"])
        self._status_filter.currentTextChanged.connect(self._on_filter_changed)
        controls.addWidget(self._status_filter)
        
        controls.addStretch(1)
        
        # Search by hash
        controls.addWidget(QLabel("Search:", self))
        self._search_input = QLineEdit(self)
        self._search_input.setPlaceholderText("Enter tx hash...")
        self._search_input.setMinimumWidth(200)
        controls.addWidget(self._search_input)
        
        self._search_button = QPushButton("Search", self)
        self._search_button.clicked.connect(self._on_search)
        controls.addWidget(self._search_button)
        
        # Refresh button
        self._refresh_button = QPushButton("Refresh", self)
        self._refresh_button.clicked.connect(self._refresh_transactions)
        controls.addWidget(self._refresh_button)
        
        # Resync button
        self._resync_button = QPushButton("Resync", self)
        self._resync_button.clicked.connect(self._on_resync)
        controls.addWidget(self._resync_button)
        
        # Transaction table
        self._table = QTableWidget(self)
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Hash",
            "From",
            "To",
            "Value",
            "Status",
            "Block",
        ])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.doubleClicked.connect(self._on_row_double_clicked)
        
        # Status message
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: #666;")
        
        layout.addWidget(title)
        layout.addLayout(controls)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._status_label)
    
    def _on_filter_changed(self, text: str) -> None:
        """Handle status filter change."""
        self._current_filter = text.lower()
        if self._current_filter == "all":
            self._current_filter = None
        self._refresh_transactions()
    
    def _on_search(self) -> None:
        """Handle search by hash."""
        search_text = self._search_input.text().strip()
        if not search_text:
            return
        
        asyncio.create_task(self._search_tx(search_text))
    
    async def _search_tx(self, tx_hash: str) -> None:
        """Search for a transaction by hash."""
        try:
            result = await self._walletd_manager.call_rpc("wallet.txs.lookup", {"hash": tx_hash})
            if result:
                # Show details dialog
                dialog = TxDetailsDialog(result, self)
                dialog.exec()
            else:
                QMessageBox.information(
                    self,
                    "Not Found",
                    f"Transaction {_truncate_hash(tx_hash)} not found.",
                )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to search transaction: {e}")
    
    def _on_resync(self) -> None:
        """Handle resync button click."""
        if not self._current_address:
            QMessageBox.information(
                self,
                "No Address",
                "Please select an account to resync transactions.",
            )
            return
        
        asyncio.create_task(self._resync_transactions())
    
    async def _resync_transactions(self) -> None:
        """Resync transactions from the blockchain."""
        if not self._current_address:
            return
        
        try:
            self._resync_button.setEnabled(False)
            self._status_label.setText("Rescanning blockchain...")
            
            result = await self._walletd_manager.call_rpc(
                "wallet.txs.resync",
                {"address": self._current_address, "window": 1000},
            )
            
            updated = result.get("updated", 0)
            self._status_label.setText(f"Resync complete. Updated {updated} transactions.")
            
            # Refresh the table
            self._refresh_transactions()
        except Exception as e:
            self._status_label.setText(f"Resync failed: {e}")
            QMessageBox.warning(self, "Error", f"Failed to resync transactions: {e}")
        finally:
            self._resync_button.setEnabled(True)
    
    def _refresh_transactions(self) -> None:
        """Refresh the transaction list."""
        asyncio.create_task(self._load_transactions())
    
    async def _load_transactions(self) -> None:
        """Load transactions from walletd."""
        try:
            params: dict[str, Any] = {"limit": 100}
            
            if self._current_address:
                params["address"] = self._current_address
            
            if self._current_filter and self._current_filter != "all":
                params["status"] = self._current_filter
            
            result = await self._walletd_manager.call_rpc("wallet.txs.list", params)
            transactions = result.get("transactions", [])
            
            self._update_table(transactions)
            
            if transactions:
                self._status_label.setText(f"Showing {len(transactions)} transactions")
            else:
                self._status_label.setText("No transactions found")
        except Exception as e:
            self._status_label.setText(f"Error loading transactions: {e}")
    
    def _update_table(self, transactions: list[dict[str, Any]]) -> None:
        """Update the table with transaction data."""
        self._table.setRowCount(len(transactions))
        
        for row, tx in enumerate(transactions):
            # Hash (clickable)
            hash_item = QTableWidgetItem(_truncate_hash(tx.get("tx_hash", "")))
            hash_item.setData(Qt.UserRole, tx)
            self._table.setItem(row, 0, hash_item)
            
            # From
            from_item = QTableWidgetItem(_truncate_hash(tx.get("from", ""), 8, 6))
            self._table.setItem(row, 1, from_item)
            
            # To
            to_addr = tx.get("to")
            to_item = QTableWidgetItem(_truncate_hash(to_addr, 8, 6) if to_addr else "(contract)")
            self._table.setItem(row, 2, to_item)
            
            # Value
            value_item = QTableWidgetItem(_format_value(tx.get("value", 0)))
            self._table.setItem(row, 3, value_item)
            
            # Status
            status = tx.get("status", "unknown")
            status_item = QTableWidgetItem(status.upper())
            if status == "confirmed":
                status_item.setForeground(Qt.green)
            elif status == "pending":
                status_item.setForeground(Qt.yellow)
            elif status == "failed":
                status_item.setForeground(Qt.red)
            self._table.setItem(row, 4, status_item)
            
            # Block
            block_num = tx.get("block_number")
            block_item = QTableWidgetItem(str(block_num) if block_num is not None else "—")
            self._table.setItem(row, 5, block_item)
        
        # Resize columns to content
        self._table.resizeColumnsToContents()
    
    def _on_row_double_clicked(self, index) -> None:
        """Handle double-click on a row to show details."""
        row = index.row()
        item = self._table.item(row, 0)
        if item:
            tx_data = item.data(Qt.UserRole)
            if tx_data:
                dialog = TxDetailsDialog(tx_data, self)
                dialog.exec()
