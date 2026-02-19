from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QVBoxLayout


class CommandPalette(QDialog):
    navigate = Signal(int)

    def __init__(self, items: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.resize(440, 320)
        self._items = items
        l = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Jump to page or run command…")
        self._list = QListWidget()
        l.addWidget(self._search)
        l.addWidget(self._list)
        self._search.textChanged.connect(self._refilter)
        self._list.itemActivated.connect(self._activate)
        self._refilter("")

    def _refilter(self, text: str) -> None:
        self._list.clear()
        for i, item in enumerate(self._items):
            if text.lower() in item.lower():
                self._list.addItem(f"{i}: {item}")

    def _activate(self, item) -> None:
        idx = int(item.text().split(":", 1)[0])
        self.navigate.emit(idx)
        self.accept()
