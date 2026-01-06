"""Wallet tab - send transactions and manage wallet."""

import logging
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from animica_miner_gui.backend.config import MiningAppConfig

logger = logging.getLogger(__name__)

# Constants
MIN_ADDRESS_LENGTH = 42  # Minimum length for valid Animica address
TX_SEND_TIMEOUT = 60  # Timeout for transaction sending in seconds
ADDRESS_PREVIEW_LENGTH = 20  # Number of characters to show in address preview


class WalletTab(QWidget):
    """Wallet tab for sending transactions."""
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setup_ui()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Wallet info group
        wallet_group = QGroupBox("Wallet Information")
        wallet_layout = QFormLayout()
        
        # Address with copy button
        address_layout = QHBoxLayout()
        self.address_label = QLabel(self.config.miner.payout_address or "Not configured")
        self.address_label.setWordWrap(True)
        self.address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        address_layout.addWidget(self.address_label, stretch=1)
        
        self.copy_address_button = QPushButton("📋 Copy")
        self.copy_address_button.setMaximumWidth(80)
        self.copy_address_button.setToolTip("Copy address to clipboard")
        self.copy_address_button.clicked.connect(self.copy_address_to_clipboard)
        if not self.config.miner.payout_address:
            self.copy_address_button.setEnabled(False)
        address_layout.addWidget(self.copy_address_button)
        
        address_widget = QWidget()
        address_widget.setLayout(address_layout)
        wallet_layout.addRow("Address:", address_widget)
        
        wallet_group.setLayout(wallet_layout)
        layout.addWidget(wallet_group)
        
        # Send transaction group
        send_group = QGroupBox("Send Transaction")
        send_layout = QFormLayout()
        
        # Recipient address
        self.recipient_input = QLineEdit()
        self.recipient_input.setPlaceholderText("anim1...")
        send_layout.addRow("To Address:", self.recipient_input)
        
        # Amount
        self.amount_input = QLineEdit()
        self.amount_input.setPlaceholderText("0.0")
        amount_help = QLabel("Amount in ANM (e.g., 1.5 for 1.5 ANM)")
        amount_help.setStyleSheet("color: gray; font-size: 11px;")
        send_layout.addRow("Amount:", self.amount_input)
        send_layout.addRow("", amount_help)
        
        # Send button
        button_layout = QHBoxLayout()
        self.send_button = QPushButton("Send Transaction")
        self.send_button.clicked.connect(self.send_transaction)
        self.send_button.setMinimumHeight(35)
        button_layout.addWidget(self.send_button)
        button_layout.addStretch()
        
        send_layout.addRow("", button_layout)
        
        send_group.setLayout(send_layout)
        layout.addWidget(send_group)
        
        # Status/result group
        result_group = QGroupBox("Transaction Result")
        result_layout = QVBoxLayout()
        
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(150)
        result_layout.addWidget(self.result_text)
        
        result_group.setLayout(result_layout)
        layout.addWidget(result_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def copy_address_to_clipboard(self) -> None:
        """Copy the wallet address to clipboard."""
        address = self.config.miner.payout_address
        if not address:
            QMessageBox.warning(
                self,
                "No Address",
                "No payout address configured."
            )
            return
        
        try:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(address)
                # Show brief notification with preview
                preview = address[:ADDRESS_PREVIEW_LENGTH] + "..." if len(address) > ADDRESS_PREVIEW_LENGTH else address
                QMessageBox.information(
                    self,
                    "Copied",
                    f"Address copied to clipboard!\n\n{preview}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Error",
                    "Unable to access clipboard."
                )
        except Exception as e:
            logger.error(f"Failed to copy address to clipboard: {e}")
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to copy address: {str(e)}"
            )
    
    def send_transaction(self) -> None:
        """Send a transaction using the wallet CLI."""
        # Get sender address from config
        from_addr = self.config.miner.payout_address
        if not from_addr:
            QMessageBox.warning(
                self,
                "No Wallet",
                "No payout address configured. Please configure a wallet first."
            )
            return
        
        # Get recipient and amount
        to_addr = self.recipient_input.text().strip()
        amount_str = self.amount_input.text().strip()
        
        # Validate inputs
        if not to_addr:
            QMessageBox.warning(self, "Invalid Input", "Please enter a recipient address.")
            return
        
        if not to_addr.startswith("anim1") or len(to_addr) < MIN_ADDRESS_LENGTH:
            QMessageBox.warning(self, "Invalid Address", "Recipient address must be a valid Animica address (anim1...).")
            return
        
        if not amount_str:
            QMessageBox.warning(self, "Invalid Input", "Please enter an amount.")
            return
        
        try:
            amount = float(amount_str)
            if amount <= 0:
                QMessageBox.warning(self, "Invalid Amount", "Amount must be greater than 0.")
                return
        except ValueError:
            QMessageBox.warning(self, "Invalid Amount", "Please enter a valid number for amount.")
            return
        
        # Confirm transaction
        reply = QMessageBox.question(
            self,
            "Confirm Transaction",
            f"Send {amount} ANM to {to_addr[:20]}...?\n\nThis will use your configured wallet.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Disable send button during transaction
        self.send_button.setEnabled(False)
        self.result_text.setPlainText("Sending transaction...\n")
        
        try:
            # Build the tx send command
            # Use the animica CLI: animica tx send --from <addr> --to <addr> --value <amount>
            rpc_url = self.config.network.rpc_url
            
            cmd = [
                sys.executable, "-m", "animica", "tx", "send",
                "--from", from_addr,
                "--to", to_addr,
                "--value", str(amount),
                "--rpc-url", rpc_url,
            ]
            
            self.result_text.append(f"Running: {' '.join(cmd)}\n")
            
            # Run the command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=TX_SEND_TIMEOUT
            )
            
            # Display results
            if result.returncode == 0:
                self.result_text.append("✓ Transaction sent successfully!\n\n")
                self.result_text.append("Output:\n")
                self.result_text.append(result.stdout)
                
                # Clear inputs on success
                self.recipient_input.clear()
                self.amount_input.clear()
                
                QMessageBox.information(
                    self,
                    "Success",
                    "Transaction sent successfully! Check the result below for details."
                )
            else:
                self.result_text.append("✗ Transaction failed!\n\n")
                self.result_text.append("Error:\n")
                self.result_text.append(result.stderr or result.stdout)
                
                QMessageBox.critical(
                    self,
                    "Transaction Failed",
                    f"Failed to send transaction. See result pane for details."
                )
        
        except subprocess.TimeoutExpired:
            self.result_text.append(f"✗ Transaction timed out after {TX_SEND_TIMEOUT} seconds.\n")
            QMessageBox.critical(self, "Timeout", "Transaction timed out.")
        
        except Exception as e:
            logger.error(f"Error sending transaction: {e}")
            self.result_text.append(f"✗ Error: {str(e)}\n")
            QMessageBox.critical(self, "Error", f"Failed to send transaction: {str(e)}")
        
        finally:
            self.send_button.setEnabled(True)
