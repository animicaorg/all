"""Settings page — placeholder."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SettingsPage(QWidget):
    """Application settings placeholder page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = QLabel("⚙️  Settings\n\nProfile and application settings will appear here.")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setObjectName("placeholderLabel")
        layout.addWidget(label)
