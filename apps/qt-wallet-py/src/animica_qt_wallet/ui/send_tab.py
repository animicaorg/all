from __future__ import annotations

import asyncio
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class SendTab(QWidget):
    """Send transaction tab for the wallet."""

    def __init__(self, walletd_manager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._walletd_manager = walletd_manager
        self._accounts: list[dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Title and description
        title = QLabel("Send ANM", self)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        desc = QLabel("Send ANM tokens to another address.", self)
        desc.setStyleSheet("color: #6b6b6b;")

        # Form group
        form_group = QGroupBox("Transaction Details", self)
        form_layout = QFormLayout(form_group)

        # From account selector
        self._from_combo = QComboBox(self)
        self._from_combo.setPlaceholderText("Select an account...")
        form_layout.addRow("From:", self._from_combo)

        # To address
        self._to_input = QLineEdit(self)
        self._to_input.setPlaceholderText("anim1...")
        form_layout.addRow("To:", self._to_input)

        # Amount
        amount_layout = QHBoxLayout()
        self._amount_input = QLineEdit(self)
        self._amount_input.setPlaceholderText("0.0")
        amount_layout.addWidget(self._amount_input)
        amount_label = QLabel("ANM", self)
        amount_layout.addWidget(amount_label)
        form_layout.addRow("Amount:", amount_layout)

        # Advanced section (collapsible)
        self._advanced_checkbox = QCheckBox("Advanced Options", self)
        self._advanced_checkbox.stateChanged.connect(self._toggle_advanced)
        
        self._advanced_group = QGroupBox("Advanced", self)
        self._advanced_group.setVisible(False)
        advanced_layout = QFormLayout(self._advanced_group)

        # Gas limit
        self._gas_limit_input = QLineEdit(self)
        self._gas_limit_input.setPlaceholderText("21000")
        self._gas_limit_input.setText("21000")
        advanced_layout.addRow("Gas Limit:", self._gas_limit_input)

        # Max fee
        self._max_fee_input = QLineEdit(self)
        self._max_fee_input.setPlaceholderText("1000000000")
        self._max_fee_input.setText("1000000000")
        advanced_layout.addRow("Max Fee (nANM):", self._max_fee_input)

        # Nonce (optional)
        self._nonce_input = QLineEdit(self)
        self._nonce_input.setPlaceholderText("Auto")
        advanced_layout.addRow("Nonce (optional):", self._nonce_input)

        # Estimate fees button
        self._estimate_btn = QPushButton("Estimate Fees", self)
        self._estimate_btn.clicked.connect(self._handle_estimate_fees)
        advanced_layout.addRow("", self._estimate_btn)

        # Fee estimate display
        self._fee_estimate_label = QLabel("", self)
        self._fee_estimate_label.setStyleSheet("color: #666; font-style: italic;")
        advanced_layout.addRow("", self._fee_estimate_label)

        # Status message
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #666;")

        # Send button
        self._send_btn = QPushButton("Send", self)
        self._send_btn.clicked.connect(self._handle_send)
        self._send_btn.setEnabled(False)
        send_layout = QHBoxLayout()
        send_layout.addStretch(1)
        send_layout.addWidget(self._send_btn)

        # Add to main layout
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(form_group)
        layout.addWidget(self._advanced_checkbox)
        layout.addWidget(self._advanced_group)
        layout.addWidget(self._status_label)
        layout.addLayout(send_layout)
        layout.addStretch(1)

        # Connect signals
        self._from_combo.currentIndexChanged.connect(self._update_send_button)
        self._to_input.textChanged.connect(self._update_send_button)
        self._amount_input.textChanged.connect(self._update_send_button)

    def _toggle_advanced(self, state: int) -> None:
        """Toggle advanced options visibility."""
        self._advanced_group.setVisible(state == Qt.Checked)

    def _update_send_button(self) -> None:
        """Enable/disable send button based on form validity."""
        from_valid = self._from_combo.currentIndex() >= 0
        to_valid = len(self._to_input.text().strip()) > 0
        amount_valid = self._is_valid_amount(self._amount_input.text())
        self._send_btn.setEnabled(from_valid and to_valid and amount_valid)

    def _is_valid_amount(self, amount_str: str) -> bool:
        """Check if amount is a valid positive number."""
        try:
            amount = float(amount_str.strip())
            return amount > 0
        except (ValueError, AttributeError):
            return False

    def refresh_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """Update the account selector with available accounts."""
        self._accounts = accounts
        current_text = self._from_combo.currentText()
        self._from_combo.clear()
        
        for acct in accounts:
            label = acct.get("label", "Account")
            address = acct.get("address", "")
            display = f"{label} ({address[:10]}...{address[-6:]})"
            self._from_combo.addItem(display, userData=address)
        
        # Try to restore previous selection
        for i in range(self._from_combo.count()):
            if current_text in self._from_combo.itemText(i):
                self._from_combo.setCurrentIndex(i)
                break
        
        self._update_send_button()

    def _handle_estimate_fees(self) -> None:
        """Estimate transaction fees."""
        asyncio.create_task(self._estimate_fees())

    async def _estimate_fees(self) -> None:
        """Estimate transaction fees asynchronously."""
        try:
            gas_limit_text = self._gas_limit_input.text().strip()
            gas_limit = int(gas_limit_text) if gas_limit_text else 21000
            
            result = await self._walletd_manager.tx_estimate_fees(gas_limit=gas_limit)
            
            max_fee = result.get("max_fee", 0)
            estimated_total = result.get("estimated_total", 0)
            
            # Convert nANM to ANM for display
            total_anm = estimated_total / 1_000_000_000
            
            self._fee_estimate_label.setText(
                f"Estimated fee: ~{total_anm:.9f} ANM (max_fee: {max_fee} nANM/gas)"
            )
            self._max_fee_input.setText(str(max_fee))
        except Exception as exc:
            self._status_label.setText(f"Fee estimation failed: {exc}")
            self._status_label.setStyleSheet("color: #b00020;")

    def _handle_send(self) -> None:
        """Show confirmation dialog before sending."""
        asyncio.create_task(self._show_confirm_dialog())

    async def _show_confirm_dialog(self) -> None:
        """Build transaction and show confirmation dialog."""
        try:
            # Get form values
            from_index = self._from_combo.currentIndex()
            if from_index < 0:
                return
            
            from_addr = self._from_combo.itemData(from_index)
            to_addr = self._to_input.text().strip()
            amount_anm = float(self._amount_input.text().strip())
            amount_nanm = int(amount_anm * 1_000_000_000)  # Convert ANM to nANM
            
            # Validate addresses
            if not from_addr or not to_addr:
                raise ValueError("From and To addresses are required")
            
            if not to_addr.startswith("anim1"):
                raise ValueError("To address must start with 'anim1'")
            
            # Get gas parameters
            gas_limit_text = self._gas_limit_input.text().strip()
            gas_limit = int(gas_limit_text) if gas_limit_text else 21000
            
            max_fee_text = self._max_fee_input.text().strip()
            max_fee = int(max_fee_text) if max_fee_text else 1_000_000_000
            
            nonce_text = self._nonce_input.text().strip()
            nonce = int(nonce_text) if nonce_text else None
            
            # Build the transaction
            self._status_label.setText("Building transaction...")
            self._status_label.setStyleSheet("color: #666;")
            
            tx = await self._walletd_manager.tx_build(
                from_addr=from_addr,
                to=to_addr,
                value=amount_nanm,
                gas_limit=gas_limit,
                max_fee=max_fee,
                nonce=nonce,
            )
            
            # Show confirmation dialog
            dialog = SendConfirmDialog(tx, amount_anm, self)
            if dialog.exec() == QDialog.Accepted:
                await self._send_transaction(tx, from_addr)
            else:
                self._status_label.setText("Transaction cancelled")
                self._status_label.setStyleSheet("color: #666;")
        
        except ValueError as exc:
            self._status_label.setText(f"Invalid input: {exc}")
            self._status_label.setStyleSheet("color: #b00020;")
        except Exception as exc:
            self._status_label.setText(f"Error building transaction: {exc}")
            self._status_label.setStyleSheet("color: #b00020;")

    async def _send_transaction(self, tx: dict[str, Any], from_addr: str) -> None:
        """Sign and send the transaction."""
        try:
            self._status_label.setText("Signing transaction...")
            self._status_label.setStyleSheet("color: #666;")
            
            # Sign the transaction
            sign_result = await self._walletd_manager.tx_sign(tx=tx, from_addr=from_addr)
            signed_tx = sign_result["signed_tx"]
            tx_hash = sign_result.get("tx_hash", "")
            
            self._status_label.setText("Sending transaction...")
            self._status_label.setStyleSheet("color: #666;")
            
            # Send the transaction
            result = await self._walletd_manager.tx_send(signed_tx=signed_tx)
            
            # Handle the result - it might be a string or dict
            if isinstance(result, dict):
                final_hash = result.get("hash") or result.get("tx_hash") or tx_hash
            else:
                final_hash = result or tx_hash
            
            # Show success dialog
            dialog = SendSuccessDialog(final_hash, self)
            dialog.exec()
            
            self._status_label.setText("Transaction sent successfully!")
            self._status_label.setStyleSheet("color: #4caf50;")
            
            # Clear the form
            self._to_input.clear()
            self._amount_input.clear()
            self._nonce_input.clear()
        
        except Exception as exc:
            error_msg = self._map_error(str(exc))
            self._status_label.setText(f"Error: {error_msg}")
            self._status_label.setStyleSheet("color: #b00020;")
            QMessageBox.warning(self, "Transaction Failed", error_msg)

    def _map_error(self, error: str) -> str:
        """Map technical errors to user-friendly messages."""
        error_lower = error.lower()
        
        if "insufficient" in error_lower and "balance" in error_lower:
            return "Insufficient balance to complete this transaction."
        
        if "insufficient" in error_lower and "funds" in error_lower:
            return "Insufficient funds (including gas fees)."
        
        if "nonce" in error_lower:
            return "Invalid transaction nonce. Try again or specify a nonce manually."
        
        if "chain_id" in error_lower or "chainid" in error_lower:
            return "Chain ID mismatch. Ensure your node is running on the correct network."
        
        if "signature" in error_lower or "verify" in error_lower:
            return "Transaction signature verification failed."
        
        if "gas" in error_lower:
            return "Gas limit too low or gas price invalid."
        
        if "node" in error_lower and "not running" in error_lower:
            return "Node is not running. Start the node in the Node tab."
        
        if "locked" in error_lower and "wallet" in error_lower:
            return "Wallet is locked. Unlock the wallet first."
        
        # Return original error if no mapping found
        return error


class SendConfirmDialog(QDialog):
    """Confirmation dialog showing full transaction details."""

    def __init__(self, tx: dict[str, Any], amount_anm: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Confirm Transaction")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        # Warning message
        warning = QLabel(
            "⚠️ Please review the transaction details carefully before sending.",
            self,
        )
        warning.setStyleSheet("color: #ff9800; font-weight: 600; padding: 10px; background: #fff3e0; border-radius: 4px;")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        
        # Transaction details
        details_group = QGroupBox("Transaction Details", self)
        details_layout = QFormLayout(details_group)
        
        details_layout.addRow("From:", QLabel(str(tx.get("from", ""))))
        details_layout.addRow("To:", QLabel(str(tx.get("to", ""))))
        details_layout.addRow("Amount:", QLabel(f"{amount_anm:.9f} ANM"))
        
        # Calculate total cost
        value_nanm = int(tx.get("value", 0))
        gas_limit = int(tx.get("gas_limit", 0))
        max_fee = int(tx.get("max_fee", 0))
        max_cost_nanm = value_nanm + (gas_limit * max_fee)
        max_cost_anm = max_cost_nanm / 1_000_000_000
        
        details_layout.addRow("Gas Limit:", QLabel(str(gas_limit)))
        details_layout.addRow("Max Fee:", QLabel(f"{max_fee} nANM/gas"))
        details_layout.addRow("Max Total Cost:", QLabel(f"{max_cost_anm:.9f} ANM"))
        details_layout.addRow("Nonce:", QLabel(str(tx.get("nonce", ""))))
        details_layout.addRow("Chain ID:", QLabel(str(tx.get("chain_id", ""))))
        
        layout.addWidget(details_group)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SendSuccessDialog(QDialog):
    """Success dialog showing transaction hash with copy button."""

    def __init__(self, tx_hash: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Transaction Sent")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout(self)
        
        # Success message
        success = QLabel("✅ Transaction sent successfully!", self)
        success.setStyleSheet("color: #4caf50; font-size: 16px; font-weight: 600;")
        layout.addWidget(success)
        
        # Transaction hash
        hash_label = QLabel("Transaction Hash:", self)
        hash_label.setStyleSheet("font-weight: 600; margin-top: 10px;")
        layout.addWidget(hash_label)
        
        hash_display = QTextEdit(self)
        hash_display.setPlainText(str(tx_hash))
        hash_display.setReadOnly(True)
        hash_display.setMaximumHeight(80)
        layout.addWidget(hash_display)
        
        # Copy button
        copy_btn = QPushButton("Copy Hash", self)
        copy_btn.clicked.connect(lambda: self._copy_hash(tx_hash))
        layout.addWidget(copy_btn)
        
        # Close button
        close_btn = QPushButton("Close", self)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _copy_hash(self, tx_hash: str) -> None:
        """Copy transaction hash to clipboard."""
        from PySide6.QtGui import QClipboard
        from PySide6.QtWidgets import QApplication
        
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(str(tx_hash))
