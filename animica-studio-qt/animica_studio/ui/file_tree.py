"""Project file-tree panel for Animica Studio Qt.

:class:`FileTreePanel` renders the open project's directory structure in a
:class:`QTreeView` (respecting the project's ignore globs), with a search box
that filters by name, a right-click context menu (new file / new folder /
rename / delete / reveal in OS file manager), and a :data:`fileActivated`
signal carrying the *relative* path of an activated file.

Every filesystem mutation is delegated to
:class:`~animica_studio.services.project_service.ProjectService`, so all
operations stay sandboxed inside the project root.

The module imports and constructs headless (``QT_QPA_PLATFORM=offscreen``).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Optional

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Custom data roles stored on each tree item.
_REL_PATH_ROLE = Qt.ItemDataRole.UserRole + 1
_IS_DIR_ROLE = Qt.ItemDataRole.UserRole + 2

_FOLDER_ICON = "📁"
_FILE_ICON = "📄"


class FileTreePanel(QFrame):
    """A searchable, sandboxed project tree view.

    Signals
    -------
    fileActivated(str path)
        Emitted (relative path) when a file node is double-clicked / activated.
    """

    fileActivated = Signal(str)  # rel path

    def __init__(self, project_service) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._project = project_service
        self._filter = ""

        self.setObjectName("Sidebar")
        self._build_ui()

        # Refresh whenever the service reports a change.
        try:
            self._project.fileTreeChanged.connect(self.refresh)
            self._project.projectOpened.connect(lambda _path: self.refresh())
        except Exception:  # pragma: no cover - service may lack signals in tests
            logger.debug("ProjectService signals unavailable; manual refresh only.")

        self.refresh()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        header = QLabel("Explorer")
        header.setObjectName("SectionHeader")
        layout.addWidget(header)

        # Search + refresh row.
        row = QHBoxLayout()
        row.setSpacing(6)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Search files…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search, 1)

        refresh_btn = QPushButton("⟳", self)
        refresh_btn.setProperty("class", "ghost")
        refresh_btn.setFixedWidth(30)
        refresh_btn.setToolTip("Refresh tree")
        refresh_btn.clicked.connect(self.refresh)
        row.addWidget(refresh_btn)
        layout.addLayout(row)

        # Tree.
        self._model = QStandardItemModel(self)
        self._model.setHorizontalHeaderLabels(["Project"])

        self._tree = QTreeView(self)
        self._tree.setModel(self._model)
        self._tree.setHeaderHidden(True)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.setUniformRowHeights(True)
        self._tree.setExpandsOnDoubleClick(True)
        self._tree.setAnimated(True)
        self._tree.activated.connect(self._on_activated)
        self._tree.doubleClicked.connect(self._on_activated)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree, 1)

        self._empty_label = QLabel("No project open.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)
        self._empty_label.setVisible(False)

    # ------------------------------------------------------------------ #
    # Building / refreshing the model
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Rebuild the tree from the project service."""
        self._model.clear()
        self._model.setHorizontalHeaderLabels(["Project"])

        if self._project is None or not self._project.is_open():
            self._empty_label.setVisible(True)
            self._tree.setVisible(False)
            return

        self._empty_label.setVisible(False)
        self._tree.setVisible(True)

        root = self._project.root
        root_item = QStandardItem(f"{_FOLDER_ICON} {root.name if root else 'project'}")
        root_item.setEditable(False)
        root_item.setData("", _REL_PATH_ROLE)
        root_item.setData(True, _IS_DIR_ROLE)

        try:
            nodes = self._project.file_tree("")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("file_tree failed: %s", exc)
            nodes = []

        added = self._populate(root_item, nodes)
        # When filtering, drop the project root if nothing matched.
        if self._filter and not added:
            self._empty_label.setText("No files match your search.")
            self._empty_label.setVisible(True)
            self._tree.setVisible(False)
            return

        self._model.appendRow(root_item)
        self._tree.expand(root_item.index())
        if self._filter:
            self._tree.expandAll()

    def _populate(
        self, parent_item: QStandardItem, nodes: list[dict[str, Any]]
    ) -> bool:
        """Add ``nodes`` under ``parent_item``; return True if anything matched.

        When a filter is active, directories are kept only if a descendant file
        matches; files are kept only if their name contains the filter.
        """
        any_added = False
        for node in nodes:
            name = node.get("name", "")
            rel = node.get("path", "")
            is_dir = bool(node.get("is_dir"))

            if is_dir:
                item = QStandardItem(f"{_FOLDER_ICON} {name}")
                item.setEditable(False)
                item.setData(rel, _REL_PATH_ROLE)
                item.setData(True, _IS_DIR_ROLE)
                child_added = self._populate(item, node.get("children", []) or [])
                self_match = self._matches(name)
                if not self._filter or child_added or self_match:
                    parent_item.appendRow(item)
                    any_added = True
            else:
                if self._filter and not self._matches(name):
                    continue
                item = QStandardItem(f"{_FILE_ICON} {name}")
                item.setEditable(False)
                item.setData(rel, _REL_PATH_ROLE)
                item.setData(False, _IS_DIR_ROLE)
                item.setToolTip(rel)
                parent_item.appendRow(item)
                any_added = True
        return any_added

    def _matches(self, name: str) -> bool:
        return self._filter in name.lower()

    def _on_search_changed(self, text: str) -> None:
        self._filter = (text or "").strip().lower()
        self._empty_label.setText("No project open.")
        self.refresh()

    # ------------------------------------------------------------------ #
    # Activation / selection helpers
    # ------------------------------------------------------------------ #
    def _on_activated(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        item = self._model.itemFromIndex(index)
        if item is None:
            return
        is_dir = bool(item.data(_IS_DIR_ROLE))
        rel = item.data(_REL_PATH_ROLE) or ""
        if is_dir:
            # Toggle expansion for directories.
            if self._tree.isExpanded(index):
                self._tree.collapse(index)
            else:
                self._tree.expand(index)
            return
        if rel:
            self.fileActivated.emit(rel)

    def _selected_item(self) -> Optional[QStandardItem]:
        indexes = self._tree.selectedIndexes()
        if not indexes:
            return None
        return self._model.itemFromIndex(indexes[0])

    def _selected_rel(self) -> tuple[str, bool]:
        """Return (rel_path, is_dir) for the current selection (root if none)."""
        item = self._selected_item()
        if item is None:
            return "", True
        return item.data(_REL_PATH_ROLE) or "", bool(item.data(_IS_DIR_ROLE))

    def _parent_dir_for_new(self) -> str:
        """Directory (rel) under which a new entry should be created."""
        rel, is_dir = self._selected_rel()
        if not rel:
            return ""
        if is_dir:
            return rel
        # File selected -> use its parent directory.
        if "/" in rel:
            return rel.rsplit("/", 1)[0]
        return ""

    # ------------------------------------------------------------------ #
    # Context menu + operations
    # ------------------------------------------------------------------ #
    def _on_context_menu(self, pos) -> None:
        if self._project is None or not self._project.is_open():
            return
        index = self._tree.indexAt(pos)
        if index.isValid():
            self._tree.setCurrentIndex(index)
        rel, is_dir = self._selected_rel()

        menu = QMenu(self)
        if not rel or is_dir:
            menu.addAction("New File…", self._action_new_file)
            menu.addAction("New Folder…", self._action_new_folder)
            menu.addSeparator()
        if rel:
            if not is_dir:
                menu.addAction("Open", lambda: self.fileActivated.emit(rel))
                menu.addSeparator()
            menu.addAction("Rename…", self._action_rename)
            menu.addAction("Delete", self._action_delete)
            menu.addSeparator()
        menu.addAction("Reveal in File Manager", self._action_reveal)
        menu.addAction("Refresh", self.refresh)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _join(self, parent: str, name: str) -> str:
        return f"{parent}/{name}" if parent else name

    def _action_new_file(self) -> None:
        parent = self._parent_dir_for_new()
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if not ok or not name.strip():
            return
        rel = self._join(parent, name.strip())
        try:
            self._project.create_file(rel, "")
        except Exception as exc:
            QMessageBox.warning(self, "New File", f"Cannot create {rel}:\n{exc}")
            return
        self.refresh()
        self.fileActivated.emit(rel)

    def _action_new_folder(self) -> None:
        parent = self._parent_dir_for_new()
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if not ok or not name.strip():
            return
        rel = self._join(parent, name.strip())
        try:
            create_dir = getattr(self._project, "create_dir", None)
            if callable(create_dir):
                create_dir(rel)
            else:  # pragma: no cover - fallback if service lacks create_dir
                self._project.create_file(self._join(rel, ".gitkeep"), "")
        except Exception as exc:
            QMessageBox.warning(self, "New Folder", f"Cannot create {rel}:\n{exc}")
            return
        self.refresh()

    def _action_rename(self) -> None:
        rel, _is_dir = self._selected_rel()
        if not rel:
            return
        old_name = rel.rsplit("/", 1)[-1]
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New name:", QLineEdit.EchoMode.Normal, old_name
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        dst = self._join(parent, new_name.strip())
        try:
            self._project.rename_file(rel, dst)
        except Exception as exc:
            QMessageBox.warning(self, "Rename", f"Cannot rename {rel}:\n{exc}")
            return
        self.refresh()

    def _action_delete(self) -> None:
        rel, is_dir = self._selected_rel()
        if not rel:
            return
        kind = "folder" if is_dir else "file"
        resp = QMessageBox.question(
            self,
            "Delete",
            f"Delete {kind} '{rel}'? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            self._project.delete_file(rel)
        except Exception as exc:
            QMessageBox.warning(self, "Delete", f"Cannot delete {rel}:\n{exc}")
            return
        self.refresh()

    def _action_reveal(self) -> None:
        rel, _is_dir = self._selected_rel()
        try:
            target = self._project.resolve(rel) if rel else self._project.root
        except Exception as exc:
            QMessageBox.warning(self, "Reveal", f"Cannot resolve path:\n{exc}")
            return
        if target is None:
            return
        path = str(target)
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", "-R", path])
            elif os.name == "nt":  # pragma: no cover - windows only
                subprocess.Popen(["explorer", "/select,", path])
            else:
                # Open the containing directory on Linux.
                folder = path if os.path.isdir(path) else os.path.dirname(path)
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:  # pragma: no cover - environment dependent
            QMessageBox.information(
                self, "Reveal", f"Could not open file manager:\n{exc}\n\nPath: {path}"
            )


# Backwards/contract compatibility: the module map references
# ``ui/file_tree_panel.py``; the class is identical either way.
__all__ = ["FileTreePanel"]
