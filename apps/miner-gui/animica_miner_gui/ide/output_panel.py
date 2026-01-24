"""Output panels for IDE build/deploy/console/problems."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QPlainTextEdit, QTabWidget, QWidget

from animica_miner_gui.ide.toolchain.diagnostics import Diagnostic


class OutputPanels(QTabWidget):
    """Tabbed output views for build, deploy, console, and problems."""

    problemActivated = Signal(str, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panes: Dict[str, QPlainTextEdit] = {}
        for name in ("Build", "Deploy", "Preflight", "Console", "Simulation"):
            pane = QPlainTextEdit()
            pane.setReadOnly(True)
            self._panes[name] = pane
            self.addTab(pane, name)

        self._problems = QListWidget()
        self._problems.itemActivated.connect(self._on_problem_activated)
        self.addTab(self._problems, "Problems")

    def append_output(self, panel: str, text: str) -> None:
        pane = self._panes.get(panel)
        if not pane:
            return
        pane.appendPlainText(text)
        pane.moveCursor(QTextCursor.End)

    def set_problems(self, diagnostics: list[Diagnostic]) -> None:
        self._problems.clear()
        for diag in diagnostics:
            item = QListWidgetItem(diag.display_text())
            item.setData(Qt.UserRole, (diag.path, diag.line, diag.column))
            if diag.severity == "warning":
                item.setForeground(Qt.GlobalColor.darkYellow)
            elif diag.severity == "info":
                item.setForeground(Qt.GlobalColor.blue)
            self._problems.addItem(item)

    def clear_problems(self) -> None:
        self._problems.clear()

    def _on_problem_activated(self, item: QListWidgetItem) -> None:
        payload: Optional[Tuple[Optional[object], Optional[int], Optional[int]]] = item.data(
            Qt.UserRole
        )
        if not payload:
            return
        path, line, col = payload
        if path is None:
            return
        self.problemActivated.emit(str(path), int(line or 1), int(col or 1))
