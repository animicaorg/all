from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.template_service import TemplateDef, TemplateService


@dataclass
class TemplateSelection:
    template_id: str
    params: dict[str, str]


class NewFromTemplateDialog(QDialog):
    def __init__(self, template_service: TemplateService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New from Template")
        self.resize(980, 560)
        self._svc = template_service
        self._param_inputs: dict[str, QLineEdit] = {}
        self._selection: TemplateSelection | None = None
        self._build_ui()
        self._reload()

    def selection(self) -> TemplateSelection | None:
        return self._selection

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        body = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel("Categories"))
        self._categories = QListWidget()
        self._categories.currentItemChanged.connect(lambda *_: self._reload_templates())
        left.addWidget(self._categories)

        middle = QVBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search templates…")
        self._search.textChanged.connect(lambda *_: self._reload_templates())
        middle.addWidget(self._search)
        self._templates = QListWidget()
        self._templates.currentItemChanged.connect(lambda *_: self._on_template_change())
        middle.addWidget(self._templates)

        right = QVBoxLayout()
        right.addWidget(QLabel("Preview"))
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right.addWidget(self._preview, 1)
        right.addWidget(QLabel("Parameters"))
        self._param_form_widget = QWidget()
        self._param_form = QFormLayout(self._param_form_widget)
        right.addWidget(self._param_form_widget)

        body.addLayout(left, 2)
        body.addLayout(middle, 3)
        body.addLayout(right, 5)
        root.addLayout(body)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _reload(self) -> None:
        self._categories.clear()
        self._categories.addItem("All")
        for cat in self._svc.categories():
            self._categories.addItem(cat)
        self._categories.setCurrentRow(0)
        self._reload_templates()

    def _reload_templates(self) -> None:
        category_item = self._categories.currentItem()
        category = category_item.text() if category_item else "All"
        query = self._search.text().strip()
        templates = self._svc.list_templates(query=query, category=category)
        self._templates.clear()
        for t in templates:
            item = QListWidgetItem(f"{t.name} ({t.category})")
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            item.setToolTip(t.description)
            self._templates.addItem(item)
        if templates:
            self._templates.setCurrentRow(0)
        else:
            self._preview.setPlainText("No templates found.")

    def _on_template_change(self) -> None:
        current = self._templates.currentItem()
        if current is None:
            return
        template_id = str(current.data(Qt.ItemDataRole.UserRole))
        template = self._svc.get(template_id)
        self._render_preview(template)
        self._render_form(template)

    def _render_preview(self, template: TemplateDef) -> None:
        params = {p.key: p.default for p in template.placeholders}
        try:
            text = self._svc.render(template.id, params)
        except Exception as exc:  # noqa: BLE001
            text = f"Template preview error: {exc}"
        self._preview.setPlainText(text)

    def _render_form(self, template: TemplateDef) -> None:
        while self._param_form.rowCount():
            self._param_form.removeRow(0)
        self._param_inputs.clear()
        for p in template.placeholders:
            edit = QLineEdit(p.default)
            edit.setPlaceholderText(p.help_text)
            edit.textChanged.connect(lambda *_: self._live_preview())
            self._param_inputs[p.key] = edit
            self._param_form.addRow(f"{p.label}:", edit)

    def _live_preview(self) -> None:
        current = self._templates.currentItem()
        if current is None:
            return
        template_id = str(current.data(Qt.ItemDataRole.UserRole))
        params = {k: w.text() for k, w in self._param_inputs.items()}
        try:
            text = self._svc.render(template_id, params)
            self._preview.setPlainText(text)
        except Exception as exc:  # noqa: BLE001
            self._preview.setPlainText(f"Validation error: {exc}")

    def _on_accept(self) -> None:
        current = self._templates.currentItem()
        if current is None:
            QMessageBox.warning(self, "Template", "Please select a template.")
            return
        template_id = str(current.data(Qt.ItemDataRole.UserRole))
        params = {k: w.text().strip() for k, w in self._param_inputs.items()}
        try:
            self._svc.render(template_id, params)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        self._selection = TemplateSelection(template_id=template_id, params=params)
        self.accept()
