"""Project tree view and file operations."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFileSystemModel,
    QInputDialog,
    QMenu,
    QMessageBox,
    QTreeView,
    QWidget,
)

logger = logging.getLogger(__name__)


class ProjectTree(QTreeView):
    """Filesystem explorer with context menu operations."""

    fileOpenRequested = Signal(str)
    rootChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.model = QFileSystemModel(self)
        self.model.setRootPath("")
        self.setModel(self.model)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)
        self.doubleClicked.connect(self._handle_double_click)
        self.setHeaderHidden(True)

        self._root_path: Optional[Path] = None

    def set_root(self, path: str) -> None:
        root_path = Path(path)
        if not root_path.exists():
            return
        self._root_path = root_path
        index = self.model.setRootPath(str(root_path))
        self.setRootIndex(index)
        self.rootChanged.emit(str(root_path))

    def open_context_menu(self, position) -> None:
        index = self.indexAt(position)
        menu = QMenu(self)

        new_file_action = menu.addAction("New File")
        new_folder_action = menu.addAction("New Folder")
        rename_action = menu.addAction("Rename")
        duplicate_action = menu.addAction("Duplicate")
        delete_action = menu.addAction("Delete")

        action = menu.exec(self.viewport().mapToGlobal(position))
        if action is None:
            return

        target_path = self._path_for_index(index)
        if action == new_file_action:
            self._create_item(target_path, is_folder=False)
        elif action == new_folder_action:
            self._create_item(target_path, is_folder=True)
        elif action == rename_action:
            self._rename_item(target_path)
        elif action == duplicate_action:
            self._duplicate_item(target_path)
        elif action == delete_action:
            self._delete_item(target_path)

    def _path_for_index(self, index) -> Optional[Path]:
        if not index.isValid():
            return self._root_path
        return Path(self.model.filePath(index))

    def _create_item(self, base_path: Optional[Path], is_folder: bool) -> None:
        if base_path is None:
            return
        if base_path.is_file():
            base_path = base_path.parent
        name, ok = QInputDialog.getText(self, "New Item", "Name:")
        if not ok or not name:
            return
        new_path = base_path / name
        if new_path.exists():
            QMessageBox.warning(self, "Create", f"{new_path} already exists.")
            return
        try:
            if is_folder:
                new_path.mkdir(parents=True, exist_ok=False)
            else:
                new_path.write_text("", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Create", f"Failed to create {new_path}: {exc}")

    def _rename_item(self, path: Optional[Path]) -> None:
        if path is None:
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=path.name)
        if not ok or not name:
            return
        new_path = path.parent / name
        try:
            path.rename(new_path)
        except OSError as exc:
            QMessageBox.warning(self, "Rename", f"Failed to rename {path}: {exc}")

    def _duplicate_item(self, path: Optional[Path]) -> None:
        if path is None or not path.exists():
            return
        if path.is_dir():
            QMessageBox.warning(self, "Duplicate", "Duplicating folders is not supported yet.")
            return
        new_path = path.with_name(f"{path.stem}_copy{path.suffix}")
        try:
            shutil.copy2(path, new_path)
        except OSError as exc:
            QMessageBox.warning(self, "Duplicate", f"Failed to duplicate {path}: {exc}")

    def _delete_item(self, path: Optional[Path]) -> None:
        if path is None or not path.exists():
            return
        response = QMessageBox.question(
            self,
            "Delete",
            f"Delete {path}?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Delete", f"Failed to delete {path}: {exc}")

    def _handle_double_click(self, index) -> None:
        path = self._path_for_index(index)
        if path and path.is_file():
            self.fileOpenRequested.emit(str(path))

    def open_workspace_dialog(self) -> Optional[str]:
        directory = QFileDialog.getExistingDirectory(self, "Select Workspace")
        return directory if directory else None
