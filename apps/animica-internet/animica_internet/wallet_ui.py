"""
Fail-closed wallet approval dialogs.

Every signing/spending action a .anm PAGE requests must pass through a modal that shows the
requesting origin and the EXACT message/transaction, and is denied if dismissed. This is the
desktop counterpart of the wallet extension's SignApprove fix (docs/SECURITY-signmessage-approval):
a page can never make the wallet a blind signing oracle, because the approval channel lives in
the privileged native UI, not in the webview.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel,
                               QLineEdit, QPlainTextEdit, QVBoxLayout)

from .config import NANM_PER_ANM

_SENSITIVE_PREFIXES = ("animica-login|", "usage:", "abuse:", "anmreserve:")


def _origin_row(origin: str) -> QLabel:
    lbl = QLabel(f"<b>{origin or 'unknown site'}</b> is requesting your approval")
    lbl.setWordWrap(True)
    return lbl


class ConnectApproveDialog(QDialog):
    def __init__(self, origin: str, address: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connect wallet")
        self.setMinimumWidth(420)
        v = QVBoxLayout(self)
        v.addWidget(_origin_row(origin))
        v.addWidget(QLabel(f"Connect account <code>{address}</code>?"))
        v.addWidget(QLabel("The site will see your address. It cannot move funds without a "
                           "separate, explicit approval each time."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class SignApproveDialog(QDialog):
    def __init__(self, origin: str, message: str, address: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signature request")
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.addWidget(_origin_row(origin))
        v.addWidget(QLabel(f"Sign this message with <code>{address}</code>:"))
        box = QPlainTextEdit(message)
        box.setReadOnly(True)
        box.setMaximumHeight(160)
        v.addWidget(box)
        if any(message.startswith(p) for p in _SENSITIVE_PREFIXES):
            warn = QLabel("⚠ This looks like a login or authorization message. Only sign it if "
                          "you understand what you are authorizing.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#F0A020;")
            v.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Sign")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Reject")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class SendApproveDialog(QDialog):
    def __init__(self, origin: str, to: str, amount_nanm: int, address: str,
                 note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transaction request")
        self.setMinimumWidth(480)
        v = QVBoxLayout(self)
        v.addWidget(_origin_row(origin))
        amount_anm = amount_nanm / NANM_PER_ANM
        v.addWidget(QLabel(f"Send <b>{amount_anm:,.9f} ANM</b>"))
        v.addWidget(QLabel(f"From: <code>{address}</code>"))
        v.addWidget(QLabel(f"To: <code>{to}</code>"))
        if note:
            v.addWidget(QLabel(note))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Confirm & send")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Reject")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


class UnlockDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unlock wallet")
        self.setMinimumWidth(360)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Enter your wallet passphrase:"))
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        v.addWidget(self.field)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def passphrase(self) -> str:
        return self.field.text()


def confirm(dialog_cls, *args, parent=None, **kwargs) -> bool:
    """Show a modal approval dialog and return True only on explicit accept (dismiss = deny)."""
    dlg = dialog_cls(*args, parent=parent, **kwargs)
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    return dlg.exec() == QDialog.DialogCode.Accepted
