"""Console page — placeholder."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ConsolePage(QWidget):
    """Interactive console placeholder page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel("🖱️  Console\n\nRPC console and log output will appear here.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("placeholderLabel")
        layout.addWidget(label)
