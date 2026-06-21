"""Diff viewer for Animica Studio Qt.

:class:`DiffViewer` presents a list of proposed :class:`~animica_studio.models.FileDiff`
changes before they are applied to the project. For each file it shows the old
and new content (side-by-side, objectNames ``DiffOld`` / ``DiffNew``) plus a
unified-diff preview, and offers per-file **Accept** / **Reject** as well as
**Accept all** / **Reject all**. A *"save rollback snapshot before applying"*
checkbox lets the user request a snapshot via the ``MainWindow``/``ProjectService``
prior to writing.

The viewer is purely presentational: it does **not** write files. On accept it
emits :pyattr:`accepted(list_of_paths)` (and the convenience
:pyattr:`fileAccepted(path, new_text)` per file) so the ``MainWindow`` can apply
the change through :class:`ProjectService`. Likewise it emits
:pyattr:`rejected(list_of_paths)`.
"""
from __future__ import annotations

import difflib
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..models import FileDiff

logger = logging.getLogger(__name__)


class DiffViewer(QWidget):
    """Per-file diff review with accept/reject and rollback-snapshot option."""

    #: Emitted with the list of accepted relative paths.
    accepted = Signal(list)
    #: Emitted with the list of rejected relative paths.
    rejected = Signal(list)
    #: Convenience per-file accept: (path, new_text).
    fileAccepted = Signal(str, str)
    #: Emitted when the user asks to apply with a rollback snapshot first.
    snapshotRequested = Signal(list)  # paths to snapshot

    def __init__(
        self,
        diffs: Optional[list[FileDiff]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setProperty("panel", "true")
        self._diffs: list[FileDiff] = []
        # path -> decision: None (undecided) / True (accept) / False (reject)
        self._decisions: dict[str, Optional[bool]] = {}
        self._build_ui()
        if diffs:
            self.set_diffs(diffs)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QLabel("Proposed Changes", self)
        header.setObjectName("SectionHeader")
        root.addWidget(header)

        body = QSplitter(Qt.Orientation.Horizontal, self)

        # Left: file list.
        self._file_list = QListWidget(self)
        self._file_list.setObjectName("Sidebar")
        self._file_list.currentRowChanged.connect(self._on_select_row)
        self._file_list.setMinimumWidth(220)
        body.addWidget(self._file_list)

        # Right: old/new + unified preview.
        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self._meta_label = QLabel("", right)
        self._meta_label.setObjectName("SectionHeader")
        right_layout.addWidget(self._meta_label)

        side = QSplitter(Qt.Orientation.Horizontal, right)
        self._old_view = self._make_code_view("DiffOld")
        self._new_view = self._make_code_view("DiffNew")
        old_box = self._labelled("Current", self._old_view)
        new_box = self._labelled("Proposed", self._new_view)
        side.addWidget(old_box)
        side.addWidget(new_box)
        right_layout.addWidget(side, 1)

        unified_label = QLabel("Unified diff", right)
        unified_label.setObjectName("SectionHeader")
        right_layout.addWidget(unified_label)
        self._unified_view = self._make_code_view("CodeArea")
        self._unified_view.setMaximumHeight(180)
        right_layout.addWidget(self._unified_view)

        # Per-file controls.
        per_file = QHBoxLayout()
        self._accept_one_btn = QPushButton("Accept file", right)
        self._accept_one_btn.setProperty("class", "primary")
        self._accept_one_btn.setProperty("variant", "primary")
        self._accept_one_btn.clicked.connect(self._on_accept_current)
        per_file.addWidget(self._accept_one_btn)

        self._reject_one_btn = QPushButton("Reject file", right)
        self._reject_one_btn.setProperty("class", "danger")
        self._reject_one_btn.setProperty("variant", "danger")
        self._reject_one_btn.clicked.connect(self._on_reject_current)
        per_file.addWidget(self._reject_one_btn)
        per_file.addStretch(1)
        right_layout.addLayout(per_file)

        body.addWidget(right)
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        root.addWidget(body, 1)

        # Bottom: snapshot option + bulk actions.
        bottom = QHBoxLayout()
        self._snapshot_check = QCheckBox("Save rollback snapshot before applying", self)
        self._snapshot_check.setChecked(True)
        bottom.addWidget(self._snapshot_check)
        bottom.addStretch(1)

        self._reject_all_btn = QPushButton("Reject all", self)
        self._reject_all_btn.setProperty("class", "ghost")
        self._reject_all_btn.setProperty("variant", "ghost")
        self._reject_all_btn.clicked.connect(self.reject_all)
        bottom.addWidget(self._reject_all_btn)

        self._accept_all_btn = QPushButton("Accept all", self)
        self._accept_all_btn.setProperty("class", "accent")
        self._accept_all_btn.setProperty("variant", "accent")
        self._accept_all_btn.clicked.connect(self.accept_all)
        bottom.addWidget(self._accept_all_btn)

        self._apply_btn = QPushButton("Apply accepted", self)
        self._apply_btn.setProperty("class", "primary")
        self._apply_btn.setProperty("variant", "primary")
        self._apply_btn.clicked.connect(self._on_apply)
        bottom.addWidget(self._apply_btn)

        root.addLayout(bottom)

    @staticmethod
    def _make_code_view(object_name: str) -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setObjectName(object_name)
        view.setProperty("class", "code")
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        return view

    @staticmethod
    def _labelled(text: str, widget: QWidget) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QLabel(text, box)
        label.setObjectName("SectionHeader")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        return box

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def set_diffs(self, diffs: list[FileDiff]) -> None:
        """Replace the displayed set of diffs and reset all decisions."""
        self._diffs = list(diffs or [])
        self._decisions = {self._key(d): None for d in self._diffs}
        self._file_list.clear()
        for d in self._diffs:
            item = QListWidgetItem(self._display_label(d))
            item.setData(Qt.ItemDataRole.UserRole, self._key(d))
            self._file_list.addItem(item)
        if self._diffs:
            self._file_list.setCurrentRow(0)
        else:
            self._old_view.clear()
            self._new_view.clear()
            self._unified_view.clear()
            self._meta_label.setText("No changes proposed.")

    def diffs(self) -> list[FileDiff]:
        return list(self._diffs)

    def accepted_paths(self) -> list[str]:
        return [self._key(d) for d in self._diffs if self._decisions.get(self._key(d)) is True]

    def rejected_paths(self) -> list[str]:
        return [self._key(d) for d in self._diffs if self._decisions.get(self._key(d)) is False]

    def wants_snapshot(self) -> bool:
        return self._snapshot_check.isChecked()

    # ------------------------------------------------------------------ #
    # Selection / rendering
    # ------------------------------------------------------------------ #
    def _on_select_row(self, row: int) -> None:
        if row < 0 or row >= len(self._diffs):
            return
        diff = self._diffs[row]
        self._old_view.setPlainText(diff.old_text or "")
        self._new_view.setPlainText(diff.new_text or "")
        self._unified_view.setPlainText(self._unified(diff))
        self._meta_label.setText(self._meta(diff))

    def _refresh_item_labels(self) -> None:
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if i < len(self._diffs):
                item.setText(self._display_label(self._diffs[i]))

    # ------------------------------------------------------------------ #
    # Per-file decisions
    # ------------------------------------------------------------------ #
    def _current_diff(self) -> Optional[FileDiff]:
        row = self._file_list.currentRow()
        if 0 <= row < len(self._diffs):
            return self._diffs[row]
        return None

    def _on_accept_current(self) -> None:
        diff = self._current_diff()
        if diff is None:
            return
        self._decisions[self._key(diff)] = True
        self._refresh_item_labels()
        self._advance()

    def _on_reject_current(self) -> None:
        diff = self._current_diff()
        if diff is None:
            return
        self._decisions[self._key(diff)] = False
        self._refresh_item_labels()
        self._advance()

    def _advance(self) -> None:
        row = self._file_list.currentRow()
        if row + 1 < self._file_list.count():
            self._file_list.setCurrentRow(row + 1)

    # ------------------------------------------------------------------ #
    # Bulk actions
    # ------------------------------------------------------------------ #
    def accept_all(self) -> None:
        for d in self._diffs:
            self._decisions[self._key(d)] = True
        self._refresh_item_labels()
        self._emit_and_apply(emit_only=True)

    def reject_all(self) -> None:
        for d in self._diffs:
            self._decisions[self._key(d)] = False
        self._refresh_item_labels()
        paths = [self._key(d) for d in self._diffs]
        self.rejected.emit(paths)

    def _on_apply(self) -> None:
        self._emit_and_apply(emit_only=False)

    def _emit_and_apply(self, *, emit_only: bool) -> None:
        accepted = [d for d in self._diffs if self._decisions.get(self._key(d)) is True]
        rejected_paths = self.rejected_paths()
        accepted_paths = [self._key(d) for d in accepted]

        if accepted and self.wants_snapshot():
            self.snapshotRequested.emit(accepted_paths)

        for d in accepted:
            self.fileAccepted.emit(self._key(d), d.new_text)

        if accepted_paths:
            self.accepted.emit(accepted_paths)
        if rejected_paths and not emit_only:
            self.rejected.emit(rejected_paths)

    # ------------------------------------------------------------------ #
    # Formatting helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _key(diff: FileDiff) -> str:
        return diff.path or (diff.new_path or "")

    def _display_label(self, diff: FileDiff) -> str:
        decision = self._decisions.get(self._key(diff))
        mark = "•"
        if decision is True:
            mark = "✓"
        elif decision is False:
            mark = "✗"
        kind = ""
        if diff.is_new:
            kind = "[new] "
        elif diff.is_delete:
            kind = "[del] "
        elif diff.is_rename:
            kind = "[ren] "
        return f"{mark} {kind}{self._key(diff)}"

    @staticmethod
    def _meta(diff: FileDiff) -> str:
        if diff.is_delete:
            return f"Delete: {diff.path}"
        if diff.is_rename:
            return f"Rename: {diff.path} → {diff.new_path or '?'}"
        if diff.is_new:
            return f"Create: {diff.path}"
        return f"Modify: {diff.path}"

    def _unified(self, diff: FileDiff) -> str:
        old_lines = (diff.old_text or "").splitlines(keepends=True)
        new_lines = (diff.new_text or "").splitlines(keepends=True)
        from_name = diff.path or "a"
        to_name = diff.new_path or diff.path or "b"
        ud = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{from_name}",
            tofile=f"b/{to_name}",
            lineterm="",
        )
        text = "\n".join(ud)
        return text or "(no textual difference)"


__all__ = ["DiffViewer"]
