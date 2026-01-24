"""Multi-file editor tabs with search/replace."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QCheckBox,
)

from animica_miner_gui.ide.editor import EditorWidget, SearchOptions

logger = logging.getLogger(__name__)


class FindReplacePanel(QWidget):
    """Find/replace panel with regex support."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find")
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("Replace")
        self.regex_checkbox = QCheckBox("Regex")
        self.case_checkbox = QCheckBox("Case")
        self.result_label = QLabel("")

        self.find_next_button = QPushButton("Next")
        self.find_prev_button = QPushButton("Prev")
        self.replace_button = QPushButton("Replace")
        self.replace_all_button = QPushButton("Replace All")

        layout.addWidget(self.find_input)
        layout.addWidget(self.replace_input)
        layout.addWidget(self.regex_checkbox)
        layout.addWidget(self.case_checkbox)
        layout.addWidget(self.find_next_button)
        layout.addWidget(self.find_prev_button)
        layout.addWidget(self.replace_button)
        layout.addWidget(self.replace_all_button)
        layout.addWidget(self.result_label)

    def options(self) -> SearchOptions:
        return SearchOptions(
            pattern=self.find_input.text(),
            regex=self.regex_checkbox.isChecked(),
            case_sensitive=self.case_checkbox.isChecked(),
        )


class EditorTabs(QWidget):
    """Editor tabs manager with autosave and find/replace support."""

    fileOpened = Signal(str)
    fileClosed = Signal(str)

    def __init__(self, autosave_interval_ms: int = 5000, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._current_tab_changed)

        self.find_panel = FindReplacePanel()
        self.find_panel.hide()

        self.find_panel.find_next_button.clicked.connect(lambda: self.find_next(True))
        self.find_panel.find_prev_button.clicked.connect(lambda: self.find_next(False))
        self.find_panel.replace_button.clicked.connect(self.replace_current)
        self.find_panel.replace_all_button.clicked.connect(self.replace_all)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.find_panel)
        layout.addWidget(self.tabs)

        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(autosave_interval_ms)
        self.autosave_timer.timeout.connect(self.autosave)
        self.autosave_timer.start()

        self.save_action = QAction("Save", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.save_current)
        self.addAction(self.save_action)

        self.save_all_action = QAction("Save All", self)
        self.save_all_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_all_action.triggered.connect(self.save_all)
        self.addAction(self.save_all_action)

        self.find_action = QAction("Find", self)
        self.find_action.setShortcut(QKeySequence.Find)
        self.find_action.triggered.connect(lambda: self.toggle_find(True))
        self.addAction(self.find_action)

        self.replace_action = QAction("Replace", self)
        self.replace_action.setShortcut(QKeySequence("Ctrl+H"))
        self.replace_action.triggered.connect(lambda: self.toggle_find(True))
        self.addAction(self.replace_action)

        self.setFocusPolicy(Qt.StrongFocus)

    def open_file(self, path: Path) -> None:
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            if isinstance(editor, EditorWidget) and editor.file_path() == path:
                self.tabs.setCurrentIndex(index)
                return

        editor = EditorWidget(self)
        try:
            editor.load_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "Open File", f"Failed to open {path}: {exc}")
            return

        editor.modificationChanged.connect(lambda modified, ed=editor: self._update_tab_title(ed, modified))
        editor.cursorPositionChanged.connect(self._update_status)

        title = path.name
        index = self.tabs.addTab(editor, title)
        self.tabs.setCurrentIndex(index)
        self.fileOpened.emit(str(path))

    def new_file(self, name: str, directory: Path) -> None:
        path = directory / name
        if path.exists():
            QMessageBox.warning(self, "New File", f"{path} already exists.")
            return
        path.write_text("", encoding="utf-8")
        self.open_file(path)

    def current_editor(self) -> Optional[EditorWidget]:
        widget = self.tabs.currentWidget()
        if isinstance(widget, EditorWidget):
            return widget
        return None

    def save_current(self) -> None:
        editor = self.current_editor()
        if not editor:
            return
        path = editor.file_path()
        if not path:
            return
        try:
            editor.save_file()
        except OSError as exc:
            QMessageBox.warning(self, "Save File", f"Failed to save {path}: {exc}")

    def save_all(self) -> None:
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            if isinstance(editor, EditorWidget) and editor.file_path():
                try:
                    editor.save_file()
                except OSError as exc:
                    QMessageBox.warning(self, "Save File", f"Failed to save {editor.file_path()}: {exc}")

    def toggle_find(self, show: bool) -> None:
        self.find_panel.setVisible(show)
        if show:
            self.find_panel.find_input.setFocus()

    def find_next(self, forward: bool = True) -> None:
        editor = self.current_editor()
        if not editor:
            return
        options = self.find_panel.options()
        if not options.pattern:
            return
        found = editor.find_next(options, forward)
        self.find_panel.result_label.setText("Found" if found else "Not found")

    def replace_current(self) -> None:
        editor = self.current_editor()
        if not editor:
            return
        editor.replace_current(self.find_panel.replace_input.text())

    def replace_all(self) -> None:
        editor = self.current_editor()
        if not editor:
            return
        count = editor.replace_all(self.find_panel.options(), self.find_panel.replace_input.text())
        self.find_panel.result_label.setText(f"Replaced {count}")

    def close_tab(self, index: int) -> None:
        editor = self.tabs.widget(index)
        if not isinstance(editor, EditorWidget):
            self.tabs.removeTab(index)
            return
        if editor.is_modified():
            response = QMessageBox.question(
                self,
                "Unsaved Changes",
                f"Save changes to {editor.file_path() or 'Untitled'}?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if response == QMessageBox.Cancel:
                return
            if response == QMessageBox.Yes:
                try:
                    editor.save_file()
                except OSError as exc:
                    QMessageBox.warning(self, "Save File", f"Failed to save {editor.file_path()}: {exc}")
        path = editor.file_path()
        self.tabs.removeTab(index)
        if path:
            self.fileClosed.emit(str(path))

    def close_all(self) -> bool:
        for index in reversed(range(self.tabs.count())):
            self.tabs.setCurrentIndex(index)
            before = self.tabs.count()
            self.close_tab(index)
            if self.tabs.count() == before:
                return False
        return True

    def autosave(self) -> None:
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            if isinstance(editor, EditorWidget) and editor.is_modified() and editor.file_path():
                try:
                    editor.save_file()
                except OSError as exc:
                    logger.warning("Autosave failed for %s: %s", editor.file_path(), exc)

    def open_files(self) -> list[str]:
        files = []
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            if isinstance(editor, EditorWidget) and editor.file_path():
                files.append(str(editor.file_path()))
        return files

    def active_file(self) -> str:
        editor = self.current_editor()
        if editor and editor.file_path():
            return str(editor.file_path())
        return ""

    def set_active_file(self, path: str) -> None:
        for index in range(self.tabs.count()):
            editor = self.tabs.widget(index)
            if isinstance(editor, EditorWidget) and editor.file_path() and str(editor.file_path()) == path:
                self.tabs.setCurrentIndex(index)
                return

    def open_file_at(self, path: Path, line: int, column: int = 1) -> None:
        self.open_file(path)
        editor = self.current_editor()
        if editor:
            editor.go_to_line_column(line, column)

    def _update_tab_title(self, editor: EditorWidget, modified: bool) -> None:
        index = self.tabs.indexOf(editor)
        if index == -1:
            return
        title = editor.file_path().name if editor.file_path() else "Untitled"
        if modified:
            title = f"*{title}"
        self.tabs.setTabText(index, title)

    def _current_tab_changed(self, _index: int) -> None:
        self._update_status()

    def _update_status(self) -> None:
        editor = self.current_editor()
        if not editor:
            return
        self.setToolTip(str(editor.file_path() or "Untitled"))
