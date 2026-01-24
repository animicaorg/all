"""Manifest editor widget with schema validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.ide.toolchain.manifest import ManifestLoadError, validate_manifest


class ManifestEditor(QWidget):
    """Widget for editing and validating contract manifests."""

    manifestSaved = Signal(Path)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._manifest_path: Optional[Path] = None

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Manifest JSON")
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)

        self.status_label = QLabel("No manifest loaded")
        self.validation_list = QListWidget()

        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self.reload_manifest)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_manifest)
        validate_button = QPushButton("Validate")
        validate_button.clicked.connect(self.validate_current)

        button_row = QHBoxLayout()
        button_row.addWidget(reload_button)
        button_row.addWidget(save_button)
        button_row.addWidget(validate_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Manifest"))
        layout.addWidget(self.editor, stretch=2)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.validation_list, stretch=1)

    def set_manifest_path(self, path: Optional[Path]) -> None:
        self._manifest_path = path
        if path:
            self._load_manifest(path)
        else:
            self.editor.setPlainText("")
            self.status_label.setText("No manifest loaded")
            self.validation_list.clear()

    def reload_manifest(self) -> None:
        if self._manifest_path:
            self._load_manifest(self._manifest_path)

    def save_manifest(self) -> None:
        if not self._manifest_path:
            self.status_label.setText("No manifest path selected")
            return
        try:
            payload = self._parse_json()
            self._manifest_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self.status_label.setText(f"Saved {self._manifest_path}")
            self.manifestSaved.emit(self._manifest_path)
        except ManifestLoadError as exc:
            self.status_label.setText(str(exc))

    def validate_current(self) -> None:
        self.validation_list.clear()
        try:
            payload = self._parse_json()
        except ManifestLoadError as exc:
            self.status_label.setText(str(exc))
            return

        abi = payload.get("abi") if isinstance(payload, dict) else None
        issues = validate_manifest(payload, abi)
        for issue in issues:
            text = issue.message
            if issue.path:
                text = f"{issue.path}: {text}"
            item = QListWidgetItem(text)
            if issue.severity == "warning":
                item.setForeground(QColor("#d8a657"))
            elif issue.severity == "info":
                item.setForeground(QColor("#89b4fa"))
            else:
                item.setForeground(QColor("#f38ba8"))
            self.validation_list.addItem(item)
        if issues:
            self.status_label.setText(f"Validation: {issues[-1].severity}")
        else:
            self.status_label.setText("Validation complete")

    def _load_manifest(self, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.status_label.setText(f"Failed to load manifest: {exc}")
            return
        self.editor.setPlainText(json.dumps(payload, indent=2, ensure_ascii=False))
        self.status_label.setText(f"Loaded {path}")
        self.validate_current()

    def _parse_json(self) -> Dict[str, Any]:
        try:
            return json.loads(self.editor.toPlainText() or "{}")
        except Exception as exc:
            raise ManifestLoadError(f"Invalid JSON: {exc}") from exc
