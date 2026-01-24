"""Output panels for IDE build/deploy/console/problems."""

from __future__ import annotations

from typing import Dict

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTabWidget, QWidget


class OutputPanels(QTabWidget):
    """Tabbed output views for build, deploy, console, and problems."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panes: Dict[str, QPlainTextEdit] = {}
        for name in ("Build", "Deploy", "Console", "Problems"):
            pane = QPlainTextEdit()
            pane.setReadOnly(True)
            self._panes[name] = pane
            self.addTab(pane, name)

    def append_output(self, panel: str, text: str) -> None:
        pane = self._panes.get(panel)
        if not pane:
            return
        pane.appendPlainText(text)
        pane.moveCursor(QTextCursor.End)
