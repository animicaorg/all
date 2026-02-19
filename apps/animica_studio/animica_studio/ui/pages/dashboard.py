"""Dashboard page — placeholder."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class DashboardPage(QWidget):
    """Overview / dashboard placeholder page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel("📊  Dashboard\n\nNetwork overview will appear here.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("placeholderLabel")
        layout.addWidget(label)
