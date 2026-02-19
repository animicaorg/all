from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class CommandPalette(QDialog):
    """Spotlight-style command palette opened with Ctrl/Cmd+K."""

    navigate = Signal(int)

    def __init__(self, items: list[str], parent=None) -> None:
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(480, 340)
        self._items = items

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        frame = QFrame()
        frame.setObjectName("CommandPaletteFrame")
        frame.setStyleSheet(
            "QFrame#CommandPaletteFrame {"
            "  border-radius: 14px;"
            "  border: 1px solid rgba(255,255,255,0.08);"
            "}"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        hint = QLabel("Jump to page or run command")
        hint.setProperty("variant", "muted")
        hint.setStyleSheet("font-size:11px;padding-left:4px;")
        lay.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to search…")
        self._search.setStyleSheet("font-size:15px; padding: 8px 12px; border-radius:10px;")
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet("border:none; background: transparent;")
        lay.addWidget(self._list)

        outer.addWidget(frame)

        self._search.textChanged.connect(self._refilter)
        self._list.itemActivated.connect(self._activate)
        self._search.returnPressed.connect(self._activate_current)
        self._refilter("")
        self._search.setFocus()

    def _refilter(self, text: str) -> None:
        self._list.clear()
        for i, item in enumerate(self._items):
            if text.lower() in item.lower():
                list_item = QListWidgetItem(f"  {item}")
                list_item.setData(Qt.ItemDataRole.UserRole, i)
                self._list.addItem(list_item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _activate(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self.navigate.emit(int(idx))
        self.accept()

    def _activate_current(self) -> None:
        current = self._list.currentItem()
        if current:
            self._activate(current)
