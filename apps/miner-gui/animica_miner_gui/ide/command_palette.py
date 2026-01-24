"""Command palette for quick actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


@dataclass
class PaletteCommand:
    name: str
    callback: Callable[[], None]


class CommandPalette(QDialog):
    """Command palette dialog with filtering."""

    def __init__(self, commands: List[PaletteCommand], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setWindowFlags(self.windowFlags() | Qt.Popup)
        self.resize(480, 360)

        self.commands = commands

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Command:"))
        self.filter_input = QLineEdit()
        header.addWidget(self.filter_input)
        layout.addLayout(header)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        self.filter_input.textChanged.connect(self._filter)
        self.list_widget.itemActivated.connect(self._activate)

        self._populate()
        self.filter_input.setFocus()

    def _populate(self) -> None:
        self.list_widget.clear()
        for command in self.commands:
            item = QListWidgetItem(command.name)
            item.setData(Qt.UserRole, command)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _filter(self, text: str) -> None:
        lower = text.lower().strip()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setHidden(lower not in item.text().lower())

    def _activate(self, item: QListWidgetItem) -> None:
        command: Optional[PaletteCommand] = item.data(Qt.UserRole)
        if command:
            self.accept()
            command.callback()
