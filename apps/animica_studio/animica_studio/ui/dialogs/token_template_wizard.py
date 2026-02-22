from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.token_template_service import TokenTemplateService


@dataclass
class TokenWizardSelection:
    template_id: str
    params: dict[str, str]
    output_dir: str
    open_after_create: bool


class TokenTemplateWizard(QDialog):
    def __init__(self, template_service: TokenTemplateService, workspace: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Token")
        self.resize(760, 520)
        self._svc = template_service
        self._workspace = workspace
        self._selection: TokenWizardSelection | None = None
        self._param_inputs: dict[str, QLineEdit] = {}
        self._build_ui()
        self._reload_templates()

    def selection(self) -> TokenWizardSelection | None:
        return self._selection

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._stack = QStackedWidget()
        self._type_page = self._build_type_page()
        self._params_page = self._build_params_page()
        self._output_page = self._build_output_page()
        self._stack.addWidget(self._type_page)
        self._stack.addWidget(self._params_page)
        self._stack.addWidget(self._output_page)
        root.addWidget(self._stack, 1)

        nav = QHBoxLayout()
        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._on_back)
        self._next_btn = QPushButton("Next")
        self._next_btn.clicked.connect(self._on_next)
        self._finish_btn = QPushButton("Generate")
        self._finish_btn.clicked.connect(self._on_finish)
        cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel.rejected.connect(self.reject)
        nav.addWidget(self._back_btn)
        nav.addWidget(self._next_btn)
        nav.addStretch()
        nav.addWidget(self._finish_btn)
        nav.addWidget(cancel)
        root.addLayout(nav)
        self._refresh_nav()

    def _build_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("1) Choose token template"))
        self._template_list = QListWidget()
        self._template_list.currentItemChanged.connect(lambda *_: self._render_params())
        layout.addWidget(self._template_list, 1)
        return page

    def _build_params_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._params_title = QLabel("2) Fill parameters")
        layout.addWidget(self._params_title)
        self._param_form_widget = QWidget()
        self._param_form = QFormLayout(self._param_form_widget)
        layout.addWidget(self._param_form_widget)
        layout.addStretch(1)
        return page

    def _build_output_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("3) Choose output folder"))
        row = QHBoxLayout()
        self._output_dir = QLineEdit("tokens/new_token")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_pick_output)
        row.addWidget(self._output_dir, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self._open_after = QCheckBox("Open main contract after create")
        self._open_after.setChecked(True)
        layout.addWidget(self._open_after)
        layout.addWidget(QLabel("Files will be generated inside the current workspace."))
        layout.addStretch(1)
        return page

    def _reload_templates(self) -> None:
        self._template_list.clear()
        for template in self._svc.list_templates():
            item = QListWidgetItem(f"{template.name} — {template.description}")
            item.setData(Qt.ItemDataRole.UserRole, template.id)
            self._template_list.addItem(item)
        if self._template_list.count():
            self._template_list.setCurrentRow(0)

    def _render_params(self) -> None:
        while self._param_form.rowCount():
            self._param_form.removeRow(0)
        self._param_inputs.clear()
        item = self._template_list.currentItem()
        if item is None:
            return
        template = self._svc.get(str(item.data(Qt.ItemDataRole.UserRole)))
        self._params_title.setText(f"2) Fill parameters — {template.name}")
        for p in template.params:
            edit = QLineEdit(p.default)
            edit.setPlaceholderText(p.help_text)
            self._param_form.addRow(p.label, edit)
            self._param_inputs[p.key] = edit

    def _on_pick_output(self) -> None:
        base = self._workspace / self._output_dir.text().strip()
        selected = QFileDialog.getExistingDirectory(self, "Select output folder", str(base))
        if not selected:
            return
        rel = Path(selected).resolve().relative_to(self._workspace.resolve())
        self._output_dir.setText(rel.as_posix())

    def _on_back(self) -> None:
        self._stack.setCurrentIndex(max(0, self._stack.currentIndex() - 1))
        self._refresh_nav()

    def _on_next(self) -> None:
        if self._stack.currentIndex() == 0 and self._template_list.currentItem() is None:
            QMessageBox.warning(self, "Template", "Choose a token template.")
            return
        if self._stack.currentIndex() == 1:
            try:
                self._validate_params()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Invalid parameters", str(exc))
                return
        self._stack.setCurrentIndex(min(self._stack.count() - 1, self._stack.currentIndex() + 1))
        self._refresh_nav()

    def _on_finish(self) -> None:
        try:
            params = self._validate_params()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Invalid parameters", str(exc))
            return
        item = self._template_list.currentItem()
        if item is None:
            QMessageBox.warning(self, "Template", "Choose a token template.")
            return
        output_dir = self._output_dir.text().strip().strip("/")
        if not output_dir:
            QMessageBox.warning(self, "Output", "Output folder is required.")
            return
        self._selection = TokenWizardSelection(
            template_id=str(item.data(Qt.ItemDataRole.UserRole)),
            params=params,
            output_dir=output_dir,
            open_after_create=self._open_after.isChecked(),
        )
        self.accept()

    def _validate_params(self) -> dict[str, str]:
        item = self._template_list.currentItem()
        if item is None:
            raise ValueError("Choose a token template")
        template_id = str(item.data(Qt.ItemDataRole.UserRole))
        params = {k: v.text().strip() for k, v in self._param_inputs.items()}
        self._svc.render(template_id, params)
        return params

    def _refresh_nav(self) -> None:
        idx = self._stack.currentIndex()
        self._back_btn.setEnabled(idx > 0)
        self._next_btn.setVisible(idx < self._stack.count() - 1)
        self._finish_btn.setVisible(idx == self._stack.count() - 1)
