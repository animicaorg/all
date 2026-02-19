"""ProfilesDialog — list, add, edit, delete, and activate connection profiles."""

from __future__ import annotations

import logging
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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.models.profile_models import ProfileType, RpcProfile

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quick add / edit dialog
# ---------------------------------------------------------------------------


class _EditProfileDialog(QDialog):
    """Minimal inline edit dialog for a remote RPC profile."""

    def __init__(self, profile: RpcProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Profile" if profile else "Add Profile")
        self.setMinimumWidth(400)
        self._original = profile

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.name_edit = QLineEdit(profile.name if profile else "New Profile")
        form.addRow("Name:", self.name_edit)

        self.url_edit = QLineEdit(profile.rpc_url if profile else "https://mainnet.animica.org/rpc")
        form.addRow("RPC URL:", self.url_edit)

        self.chain_id_edit = QLineEdit(str(profile.chain_id_expected if profile else 1))
        form.addRow("Chain ID:", self.chain_id_edit)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #f38ba8; font-size: 12px;")
        layout.addWidget(self._status_lbl)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _validate_and_accept(self) -> None:
        from animica_studio.models.profile_models import validate_rpc_url  # noqa: PLC0415

        url = self.url_edit.text().strip()
        try:
            validate_rpc_url(url)
        except ValueError as exc:
            self._status_lbl.setText(str(exc))
            return
        try:
            int(self.chain_id_edit.text().strip())
        except ValueError:
            self._status_lbl.setText("Chain ID must be an integer.")
            return
        self.accept()

    def build_profile(self) -> RpcProfile:
        import uuid  # noqa: PLC0415

        pid = self._original.id if self._original else str(uuid.uuid4())
        return RpcProfile(
            id=pid,
            name=self.name_edit.text().strip() or "Unnamed",
            type=self._original.type if self._original else ProfileType.REMOTE_RPC,
            rpc_url=self.url_edit.text().strip(),
            chain_id_expected=int(self.chain_id_edit.text().strip()),
            node_start_cmd=self._original.node_start_cmd if self._original else None,
            node_datadir=self._original.node_datadir if self._original else None,
            node_rpc_url=self._original.node_rpc_url if self._original else None,
        )


# ---------------------------------------------------------------------------
# ProfilesDialog
# ---------------------------------------------------------------------------


class ProfilesDialog(QDialog):
    """Manage connection profiles.

    Parameters
    ----------
    profile_service:
        The application's :class:`~animica_studio.services.profile_service.ProfileService`.
    parent:
        Optional parent widget.
    """

    def __init__(self, profile_service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = profile_service
        self.setWindowTitle("Manage Profiles")
        self.setMinimumSize(520, 380)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)

        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_selection)
        layout.addWidget(self._list, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setAlignment(Qt.AlignmentFlag.AlignTop)
        btn_col.setSpacing(8)

        self._add_btn = QPushButton("➕  Add…")
        self._edit_btn = QPushButton("✏️  Edit…")
        self._delete_btn = QPushButton("🗑️  Delete")
        self._activate_btn = QPushButton("✅  Set Active")

        for btn in (self._add_btn, self._edit_btn, self._delete_btn, self._activate_btn):
            btn_col.addWidget(btn)

        self._add_btn.clicked.connect(self._on_add)
        self._edit_btn.clicked.connect(self._on_edit)
        self._delete_btn.clicked.connect(self._on_delete)
        self._activate_btn.clicked.connect(self._on_activate)

        btn_col.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_col.addWidget(close_btn)

        layout.addLayout(btn_col)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._list.clear()
        active_id = self._service.get_active_profile_id()
        for profile in self._service.list_profiles():
            label = f"{profile.name}  [{profile.type.value}]  {profile.rpc_url}"
            if profile.id == active_id:
                label = f"● {label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self._list.addItem(item)
        self._on_selection(self._list.currentRow())

    def _selected_profile_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_profile(self) -> RpcProfile | None:
        pid = self._selected_profile_id()
        if pid is None:
            return None
        for p in self._service.list_profiles():
            if p.id == pid:
                return p
        return None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_selection(self, _row: int) -> None:
        has_sel = self._list.currentItem() is not None
        self._edit_btn.setEnabled(has_sel)
        self._delete_btn.setEnabled(has_sel)
        self._activate_btn.setEnabled(has_sel)

    def _on_add(self) -> None:
        dlg = _EditProfileDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._service.add_profile(dlg.build_profile())
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Error", str(exc))
            self._refresh()

    def _on_edit(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        dlg = _EditProfileDialog(profile=profile, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                self._service.update_profile(dlg.build_profile())
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Error", str(exc))
            self._refresh()

    def _on_delete(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete profile '{profile.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.delete_profile(profile.id)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot Delete", str(exc))
        self._refresh()

    def _on_activate(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        try:
            self._service.set_active(profile.id)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))
        self._refresh()
