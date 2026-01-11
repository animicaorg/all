from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ReceiveTab(QWidget):
    """Receive tab showing address and QR code."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accounts: list[dict[str, Any]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Title and description
        title = QLabel("Receive ANM", self)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        desc = QLabel("Share your address to receive ANM tokens.", self)
        desc.setStyleSheet("color: #6b6b6b;")

        # Account selector
        selector_layout = QHBoxLayout()
        selector_layout.addWidget(QLabel("Account:", self))
        self._account_combo = QComboBox(self)
        self._account_combo.currentIndexChanged.connect(self._update_display)
        selector_layout.addWidget(self._account_combo, stretch=1)

        # Address display group
        address_group = QGroupBox("Your Address", self)
        address_layout = QVBoxLayout(address_group)

        # Address label
        self._address_label = QLabel("Select an account to view address", self)
        self._address_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._address_label.setStyleSheet(
            "font-family: monospace; font-size: 14px; padding: 10px; "
            "background: #f5f5f5; border-radius: 4px; word-wrap: break-word;"
        )
        self._address_label.setWordWrap(True)
        address_layout.addWidget(self._address_label)

        # Copy button
        copy_layout = QHBoxLayout()
        self._copy_btn = QPushButton("Copy Address", self)
        self._copy_btn.clicked.connect(self._copy_address)
        self._copy_btn.setEnabled(False)
        copy_layout.addStretch(1)
        copy_layout.addWidget(self._copy_btn)
        address_layout.addLayout(copy_layout)

        # QR code group
        qr_group = QGroupBox("QR Code", self)
        qr_layout = QVBoxLayout(qr_group)

        self._qr_label = QLabel(self)
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setMinimumSize(256, 256)
        self._qr_label.setStyleSheet(
            "background: white; border: 1px solid #ddd; border-radius: 4px;"
        )
        qr_layout.addWidget(self._qr_label)

        # Instructions
        instructions = QLabel(
            "Scan the QR code or share the address above to receive ANM tokens.",
            self,
        )
        instructions.setStyleSheet("color: #666; font-style: italic;")
        instructions.setWordWrap(True)

        # Add to main layout
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addLayout(selector_layout)
        layout.addWidget(address_group)
        layout.addWidget(qr_group)
        layout.addWidget(instructions)
        layout.addStretch(1)

    def refresh_accounts(self, accounts: list[dict[str, Any]]) -> None:
        """Update the account selector with available accounts."""
        self._accounts = accounts
        current_text = self._account_combo.currentText()
        self._account_combo.clear()

        for acct in accounts:
            label = acct.get("label", "Account")
            address = acct.get("address", "")
            display = f"{label} ({address[:10]}...{address[-6:]})"
            self._account_combo.addItem(display, userData=address)

        # Try to restore previous selection
        for i in range(self._account_combo.count()):
            if current_text in self._account_combo.itemText(i):
                self._account_combo.setCurrentIndex(i)
                break

        self._update_display()

    def _update_display(self) -> None:
        """Update address and QR code display."""
        index = self._account_combo.currentIndex()
        if index < 0 or index >= len(self._accounts):
            self._address_label.setText("Select an account to view address")
            self._copy_btn.setEnabled(False)
            self._qr_label.clear()
            self._qr_label.setText("No QR code available")
            return

        address = self._account_combo.itemData(index)
        if not address:
            return

        # Display address
        self._address_label.setText(str(address))
        self._copy_btn.setEnabled(True)

        # Generate QR code
        self._generate_qr_code(address)

    def _generate_qr_code(self, address: str) -> None:
        """Generate and display QR code for the address."""
        try:
            import qrcode
            from io import BytesIO

            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(address)
            qr.make(fit=True)

            # Create image
            img = qr.make_image(fill_color="black", back_color="white")

            # Convert to QPixmap
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            pixmap = QPixmap()
            pixmap.loadFromData(buffer.read())

            # Scale to fit the label
            scaled_pixmap = pixmap.scaled(
                240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._qr_label.setPixmap(scaled_pixmap)

        except ImportError:
            # qrcode library not available
            self._qr_label.clear()
            self._qr_label.setText(
                "QR code generation unavailable\n"
                "(install qrcode library: pip install qrcode[pil])"
            )
            self._qr_label.setAlignment(Qt.AlignCenter)
        except Exception as exc:
            self._qr_label.clear()
            self._qr_label.setText(f"QR code generation failed:\n{exc}")
            self._qr_label.setAlignment(Qt.AlignCenter)

    def _copy_address(self) -> None:
        """Copy the selected address to clipboard."""
        index = self._account_combo.currentIndex()
        if index < 0:
            return

        address = self._account_combo.itemData(index)
        if not address:
            return

        from PySide6.QtGui import QClipboard
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(str(address))
            # Show temporary feedback
            original_text = self._copy_btn.text()
            self._copy_btn.setText("✓ Copied!")
            self._copy_btn.setEnabled(False)

            # Reset after 2 seconds
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                2000,
                lambda: (
                    self._copy_btn.setText(original_text),
                    self._copy_btn.setEnabled(True),
                ),
            )
