"""Approval dialog for external RPC requests."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)


class ApprovalDialog(QDialog):
    """Dialog for approving external wallet requests."""
    
    def __init__(self, request_data: dict, parent=None) -> None:
        super().__init__(parent)
        self.request_data = request_data
        self.setWindowTitle("Wallet Request Approval")
        self.setModal(True)
        self.resize(600, 500)
        
        self._build_ui()
    
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        
        # Header
        header = QLabel("An external application is requesting wallet access")
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        # Requester info
        requester_group = QGroupBox("Requesting Application")
        requester_layout = QFormLayout(requester_group)
        
        requester_info = self.request_data.get("requester_info", {})
        process_name = requester_info.get("process_name", "unknown")
        pid = requester_info.get("pid")
        remote_addr = requester_info.get("remote_addr", "unknown")
        
        requester_layout.addRow("Process:", QLabel(process_name))
        if pid:
            requester_layout.addRow("PID:", QLabel(str(pid)))
        requester_layout.addRow("Address:", QLabel(remote_addr))
        
        layout.addWidget(requester_group)
        
        # Request details
        request_group = QGroupBox("Request Details")
        request_layout = QFormLayout(request_group)
        
        method = self.request_data.get("method", "unknown")
        params = self.request_data.get("params", {})
        
        request_layout.addRow("Method:", QLabel(method))
        
        # Show specific details based on method
        if method == "wallet_requestAccounts":
            desc = QLabel("The application wants to view your wallet addresses.")
            desc.setWordWrap(True)
            request_layout.addRow("Description:", desc)
        
        elif method == "wallet_signTransaction":
            desc = QLabel("The application wants to sign a transaction.")
            desc.setWordWrap(True)
            request_layout.addRow("Description:", desc)
            
            tx = params.get("transaction", {})
            if tx:
                request_layout.addRow("From:", QLabel(str(params.get("from", "N/A"))))
                request_layout.addRow("To:", QLabel(str(tx.get("to", "N/A"))))
                request_layout.addRow("Value:", QLabel(f"{tx.get('value', 0)} wei"))
                request_layout.addRow("Gas Limit:", QLabel(str(tx.get("gas_limit", "N/A"))))
                request_layout.addRow("Max Fee:", QLabel(str(tx.get("max_fee", "N/A"))))
        
        elif method == "wallet_sendTransaction":
            desc = QLabel("The application wants to send a transaction.")
            desc.setWordWrap(True)
            request_layout.addRow("Description:", desc)
            
            tx = params.get("transaction", {})
            if tx:
                request_layout.addRow("From:", QLabel(str(params.get("from", "N/A"))))
                request_layout.addRow("To:", QLabel(str(tx.get("to", "N/A"))))
                request_layout.addRow("Value:", QLabel(f"{tx.get('value', 0)} wei"))
                request_layout.addRow("Gas Limit:", QLabel(str(tx.get("gas_limit", "N/A"))))
                request_layout.addRow("Max Fee:", QLabel(str(tx.get("max_fee", "N/A"))))
        
        layout.addWidget(request_group)
        
        # Raw params (optional)
        if params:
            raw_group = QGroupBox("Raw Parameters")
            raw_layout = QVBoxLayout(raw_group)
            
            raw_text = QPlainTextEdit()
            raw_text.setPlainText(str(params))
            raw_text.setReadOnly(True)
            raw_text.setMaximumHeight(150)
            raw_layout.addWidget(raw_text)
            
            layout.addWidget(raw_group)
        
        # Warning
        warning = QLabel("⚠️ Only approve requests from applications you trust!")
        warning.setStyleSheet("color: #d9534f; font-weight: bold; margin: 10px 0;")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(warning)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Approve")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Deny")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def get_approved(self) -> bool:
        """Return True if the request was approved."""
        return self.result() == QDialog.DialogCode.Accepted
