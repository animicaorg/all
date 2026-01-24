"""IDE tab implementation for Animica Miner GUI."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.ide.command_palette import CommandPalette, PaletteCommand
from animica_miner_gui.ide.controller import IDEController
from animica_miner_gui.ide.editor_tabs import EditorTabs
from animica_miner_gui.ide.output_panel import OutputPanels
from animica_miner_gui.ide.project_tree import ProjectTree
from animica_miner_gui.ide.settings import IDESettings, load_ide_settings, save_ide_settings

logger = logging.getLogger(__name__)


class IDETab(QWidget):
    """IDE tab with project explorer and editor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.settings = load_ide_settings()
        self.controller = IDEController(self)

        self.workspace_picker = QComboBox()
        self.workspace_picker.setEditable(True)
        self._load_recent_projects(self.settings.recent_projects)
        self.workspace_picker.currentTextChanged.connect(self._on_workspace_selected)

        open_button = QPushButton("Open Folder")
        open_button.clicked.connect(self.select_workspace)

        refresh_button = QToolButton()
        refresh_button.setText("↻")
        refresh_button.clicked.connect(self.refresh_workspace)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Workspace:"))
        top_bar.addWidget(self.workspace_picker, stretch=1)
        top_bar.addWidget(open_button)
        top_bar.addWidget(refresh_button)

        self.project_tree = ProjectTree(self)
        self.project_tree.fileOpenRequested.connect(self._open_file)

        self.editor_tabs = EditorTabs(autosave_interval_ms=self.settings.autosave_interval_ms, parent=self)
        self.editor_tabs.fileOpened.connect(self._register_open_file)
        self.editor_tabs.fileClosed.connect(self._unregister_open_file)
        if not self.settings.autosave_enabled:
            self.editor_tabs.autosave_timer.stop()

        self.output_panels = OutputPanels(self)

        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.addWidget(QLabel("Inspector"))
        inspector_layout.addStretch(1)

        horizontal_split = QSplitter(Qt.Horizontal)
        horizontal_split.addWidget(self.project_tree)
        horizontal_split.addWidget(self.editor_tabs)
        horizontal_split.addWidget(inspector)
        horizontal_split.setStretchFactor(1, 2)

        vertical_split = QSplitter(Qt.Vertical)
        vertical_split.addWidget(horizontal_split)
        vertical_split.addWidget(self.output_panels)
        vertical_split.setStretchFactor(0, 3)
        vertical_split.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(top_bar)
        layout.addWidget(vertical_split)

        self._setup_actions()
        self._connect_controller()
        self._restore_workspace()
        self._restore_open_tabs()

    def _setup_actions(self) -> None:
        self.command_palette_action = QAction("Command Palette", self)
        self.command_palette_action.setShortcut(QKeySequence("Ctrl+P"))
        self.command_palette_action.triggered.connect(self.open_command_palette)
        self.addAction(self.command_palette_action)

        self.build_action = QAction("Build", self)
        self.build_action.setShortcut(QKeySequence("Ctrl+B"))
        self.build_action.triggered.connect(self.run_build)
        self.addAction(self.build_action)

        self.deploy_action = QAction("Deploy", self)
        self.deploy_action.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.deploy_action.triggered.connect(self.run_deploy)
        self.addAction(self.deploy_action)

        self.save_action = QAction("Save", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.editor_tabs.save_current)
        self.addAction(self.save_action)

        self.save_all_action = QAction("Save All", self)
        self.save_all_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.save_all_action.triggered.connect(self.editor_tabs.save_all)
        self.addAction(self.save_all_action)

        self.find_action = QAction("Find", self)
        self.find_action.setShortcut(QKeySequence.Find)
        self.find_action.triggered.connect(lambda: self.editor_tabs.toggle_find(True))
        self.addAction(self.find_action)

        self.replace_action = QAction("Replace", self)
        self.replace_action.setShortcut(QKeySequence("Ctrl+H"))
        self.replace_action.triggered.connect(lambda: self.editor_tabs.toggle_find(True))
        self.addAction(self.replace_action)

    def _connect_controller(self) -> None:
        self.controller.buildFinished.connect(self._on_build_finished)
        self.controller.deployFinished.connect(self._on_deploy_finished)

    def _restore_workspace(self) -> None:
        if self.settings.last_workspace:
            self.workspace_picker.setCurrentText(self.settings.last_workspace)
            self.project_tree.set_root(self.settings.last_workspace)

    def _restore_open_tabs(self) -> None:
        for file_path in self.settings.open_files:
            path = Path(file_path)
            if path.exists():
                self.editor_tabs.open_file(path)
        if self.settings.active_file:
            self.editor_tabs.set_active_file(self.settings.active_file)

    def _load_recent_projects(self, projects: List[str]) -> None:
        self.workspace_picker.clear()
        for project in projects:
            self.workspace_picker.addItem(project)

    def _on_workspace_selected(self, path: str) -> None:
        if path:
            self.project_tree.set_root(path)
            self.settings.last_workspace = path
            self._add_recent_project(path)
            self._persist_settings()

    def select_workspace(self) -> None:
        directory = self.project_tree.open_workspace_dialog()
        if directory:
            self.workspace_picker.setCurrentText(directory)

    def refresh_workspace(self) -> None:
        path = self.workspace_picker.currentText()
        if path:
            self.project_tree.set_root(path)

    def _add_recent_project(self, path: str) -> None:
        if path in self.settings.recent_projects:
            self.settings.recent_projects.remove(path)
        self.settings.recent_projects.insert(0, path)
        self.settings.recent_projects = self.settings.recent_projects[:10]
        self._load_recent_projects(self.settings.recent_projects)

    def _open_file(self, path: str) -> None:
        self.editor_tabs.open_file(Path(path))

    def _register_open_file(self, path: str) -> None:
        if path not in self.settings.open_files:
            self.settings.open_files.append(path)
            self._persist_settings()

    def _unregister_open_file(self, path: str) -> None:
        if path in self.settings.open_files:
            self.settings.open_files.remove(path)
            self._persist_settings()

    def open_command_palette(self) -> None:
        commands = [
            PaletteCommand("Open File", self._command_open_file),
            PaletteCommand("Go to Line", self._command_go_to_line),
            PaletteCommand("Save", self.editor_tabs.save_current),
            PaletteCommand("Save All", self.editor_tabs.save_all),
            PaletteCommand("Find", lambda: self.editor_tabs.toggle_find(True)),
            PaletteCommand("Replace", lambda: self.editor_tabs.toggle_find(True)),
            PaletteCommand("Build Project", self.run_build),
            PaletteCommand("Deploy Project", self.run_deploy),
        ]
        palette = CommandPalette(commands, self)
        palette.exec()

    def _command_open_file(self) -> None:
        workspace = Path(self.workspace_picker.currentText())
        if not workspace.exists():
            QMessageBox.warning(self, "Open File", "Select a workspace first.")
            return
        files = [str(path.relative_to(workspace)) for path in workspace.rglob("*") if path.is_file()]
        if not files:
            QMessageBox.information(self, "Open File", "No files in workspace.")
            return
        choice, ok = QInputDialog.getItem(self, "Open File", "File:", files, 0, False)
        if ok and choice:
            self.editor_tabs.open_file(workspace / choice)

    def _command_go_to_line(self) -> None:
        editor = self.editor_tabs.current_editor()
        if not editor:
            return
        line, ok = QInputDialog.getInt(self, "Go to Line", "Line number:", 1, 1, 1000000)
        if ok:
            editor.go_to_line(line)

    def run_build(self) -> None:
        self.output_panels.append_output("Build", "Starting build...")
        self.controller.build_project(self.workspace_picker.currentText())

    def run_deploy(self) -> None:
        self.output_panels.append_output("Deploy", "Starting deploy...")
        self.controller.deploy_project(self.workspace_picker.currentText())

    def _on_build_finished(self, success: bool, message: str) -> None:
        status = "✅" if success else "❌"
        self.output_panels.append_output("Build", f"{status} {message}")

    def _on_deploy_finished(self, success: bool, message: str) -> None:
        status = "✅" if success else "❌"
        self.output_panels.append_output("Deploy", f"{status} {message}")

    def prompt_close(self) -> bool:
        if not self.editor_tabs.close_all():
            return False
        self.settings.open_files = self.editor_tabs.open_files()
        self.settings.active_file = self.editor_tabs.active_file()
        self._persist_settings()
        return True

    def _persist_settings(self) -> None:
        self.settings.open_files = self.editor_tabs.open_files()
        self.settings.active_file = self.editor_tabs.active_file()
        save_ide_settings(self.settings)
