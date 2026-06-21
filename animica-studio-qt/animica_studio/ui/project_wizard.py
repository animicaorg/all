"""New-project wizard for Animica Studio Qt.

:class:`ProjectWizard` drives the *New Project* flow: pick one of the starter
templates, choose a parent directory and a project name, then materialize the
template via the injected ``templates_service`` (a
:class:`~animica_studio.services.templates_service.TemplatesService`-like object
exposing ``list_templates()`` and ``create(template_id, dest_dir, name)``).

On success it emits :pyattr:`projectCreated(root_path)` so the ``MainWindow`` can
open the freshly created project.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional, Protocol

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class TemplatesProvider(Protocol):
    """Minimal interface required of the templates service."""

    def list_templates(self) -> list[dict[str, Any]]: ...
    def create(self, template_id: str, dest_dir: str, name: str) -> str: ...


class ProjectWizard(QDialog):
    """Dialog to choose a template + destination and create a new project."""

    #: Emitted with the new project's root path on successful creation.
    projectCreated = Signal(str)

    def __init__(
        self,
        templates_service: TemplatesProvider,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._service = templates_service
        self._created_path: Optional[str] = None

        self.setObjectName("Panel")
        self.setProperty("panel", "true")
        self.setWindowTitle("Animica Studio — New Project")
        self.setModal(True)
        self.resize(640, 520)
        self._build_ui()
        self._load_templates()

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Create a new project", self)
        title.setObjectName("SectionHeader")
        root.addWidget(title)

        # Template list + description.
        self._template_list = QListWidget(self)
        self._template_list.setObjectName("Sidebar")
        self._template_list.currentRowChanged.connect(self._on_template_changed)
        self._template_list.setMinimumHeight(220)
        root.addWidget(self._template_list, 1)

        self._desc_label = QLabel("", self)
        self._desc_label.setWordWrap(True)
        root.addWidget(self._desc_label)

        # Name + destination form.
        form = QFormLayout()
        form.setSpacing(8)

        self._name_edit = QLineEdit(self)
        self._name_edit.setPlaceholderText("my-awesome-project")
        self._name_edit.textChanged.connect(self._validate)
        form.addRow("Project name", self._name_edit)

        dest_row = QHBoxLayout()
        self._dest_edit = QLineEdit(self)
        self._dest_edit.setPlaceholderText("Parent directory for the new project")
        self._dest_edit.setText(os.path.expanduser("~"))
        self._dest_edit.textChanged.connect(self._validate)
        dest_row.addWidget(self._dest_edit, 1)
        browse_btn = QPushButton("Browse…", self)
        browse_btn.setProperty("class", "ghost")
        browse_btn.setProperty("variant", "ghost")
        browse_btn.clicked.connect(self._on_browse)
        dest_row.addWidget(browse_btn)
        dest_container = QWidget(self)
        dest_container.setLayout(dest_row)
        form.addRow("Location", dest_container)

        root.addLayout(form)

        self._error_label = QLabel("", self)
        self._error_label.setObjectName("StatusError")
        self._error_label.setWordWrap(True)
        root.addWidget(self._error_label)

        # Buttons.
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, parent=self
        )
        self._create_btn = QPushButton("Create Project", self)
        self._create_btn.setProperty("class", "primary")
        self._create_btn.setProperty("variant", "primary")
        self._create_btn.clicked.connect(self._on_create)
        self._buttons.addButton(
            self._create_btn, QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

    # ------------------------------------------------------------------ #
    # Data loading
    # ------------------------------------------------------------------ #
    def _load_templates(self) -> None:
        try:
            templates = self._service.list_templates()
        except Exception as exc:  # pragma: no cover - service dependent
            logger.warning("Failed to list templates: %s", exc)
            templates = []
        self._template_list.clear()
        for tpl in templates:
            label = str(tpl.get("name", tpl.get("id", "template")))
            if tpl.get("available") is False:
                label += "  (built-in fallback)"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, tpl)
            self._template_list.addItem(item)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)
        self._validate()

    # ------------------------------------------------------------------ #
    # Slots
    # ------------------------------------------------------------------ #
    def _current_template(self) -> Optional[dict[str, Any]]:
        item = self._template_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        return data if isinstance(data, dict) else None

    def _on_template_changed(self, _row: int) -> None:
        tpl = self._current_template()
        if tpl is None:
            self._desc_label.setText("")
            return
        self._desc_label.setText(str(tpl.get("description", "")))
        self._validate()

    def _on_browse(self) -> None:
        start = self._dest_edit.text().strip() or os.path.expanduser("~")
        directory = QFileDialog.getExistingDirectory(
            self, "Choose parent directory", start
        )
        if directory:
            self._dest_edit.setText(directory)

    def _validate(self) -> bool:
        tpl = self._current_template()
        name = self._name_edit.text().strip()
        dest = self._dest_edit.text().strip()
        problem = ""
        if tpl is None:
            problem = "Select a template."
        elif not name:
            problem = "Enter a project name."
        elif not dest:
            problem = "Choose a destination directory."
        self._error_label.setText(problem)
        ok = not problem
        self._create_btn.setEnabled(ok)
        return ok

    def _on_create(self) -> None:
        if not self._validate():
            return
        tpl = self._current_template()
        assert tpl is not None  # guarded by _validate
        name = self._name_edit.text().strip()
        dest = self._dest_edit.text().strip()
        template_id = str(tpl.get("id", ""))
        try:
            root_path = self._service.create(template_id, dest, name)
        except FileExistsError as exc:
            self._error_label.setText(str(exc))
            return
        except Exception as exc:  # pragma: no cover - service dependent
            logger.warning("Project creation failed: %s", exc)
            QMessageBox.critical(
                self, "Create Project", f"Could not create project:\n{exc}"
            )
            return
        self._created_path = root_path
        logger.info("Created project at %s", root_path)
        self.projectCreated.emit(root_path)
        self.accept()

    # ------------------------------------------------------------------ #
    # Result
    # ------------------------------------------------------------------ #
    def created_path(self) -> Optional[str]:
        """The created project's root path (valid after accept)."""
        return self._created_path


__all__ = ["ProjectWizard"]
