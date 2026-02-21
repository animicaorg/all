from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_service import (
    EnaEditProposal,
    EnaProvider,
    LocalEnaProvider,
    RemoteEnaProvider,
    WorkspaceIndexService,
    apply_edit_atomic,
    command_is_allowed,
)


class EnaPanel(QWidget):
    def __init__(self, get_workspace, get_current_file_text, get_selection_text, ena_config: dict, parent=None) -> None:
        super().__init__(parent)
        self._get_workspace = get_workspace
        self._get_current_file_text = get_current_file_text
        self._get_selection_text = get_selection_text
        self._ena_config = ena_config
        self._provider: EnaProvider = LocalEnaProvider()
        self._index = WorkspaceIndexService(
            max_file_bytes=200_000,
            max_total_bytes=int(ena_config.get("context", {}).get("max_bytes", 1_000_000)),
        )
        self._process: QProcess | None = None
        self._last_proposal: EnaEditProposal | None = None
        self._build_ui()
        QTimer.singleShot(0, self._init_provider)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("ENA"))
        self._transcript = QTextEdit()
        self._transcript.setReadOnly(True)
        layout.addWidget(self._transcript, 1)

        row = QHBoxLayout()
        self._ctx_current_file = QCheckBox("Current file")
        self._ctx_current_file.setChecked(True)
        self._ctx_selection = QCheckBox("Selection")
        self._ctx_open_files = QCheckBox("Open files")
        self._ctx_logs = QCheckBox("Logs")
        self._ctx_tree = QCheckBox("Project tree")
        for w in [self._ctx_current_file, self._ctx_selection, self._ctx_open_files, self._ctx_logs, self._ctx_tree]:
            row.addWidget(w)
        layout.addLayout(row)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("Ask ENA for help…")
        self._input.setMaximumHeight(84)
        layout.addWidget(self._input)

        btns = QHBoxLayout()
        ask = QPushButton("Ask")
        ask.clicked.connect(self._on_ask)
        propose = QPushButton("Propose Patch")
        propose.clicked.connect(self._on_propose_patch)
        apply_btn = QPushButton("Apply Patch…")
        apply_btn.clicked.connect(self._on_apply_patch)
        run_btn = QPushButton("Run Checks")
        run_btn.clicked.connect(self._on_run_checks)
        btns.addWidget(ask)
        btns.addWidget(propose)
        btns.addWidget(apply_btn)
        btns.addWidget(run_btn)
        layout.addLayout(btns)

    def _init_provider(self) -> None:
        provider_name = self._ena_config.get("provider", "local")
        if provider_name == "remote":
            remote = self._ena_config.get("remote", {})
            self._provider = RemoteEnaProvider(
                endpoint=str(remote.get("endpoint", "")),
                api_key=str(remote.get("api_key", "")),
                model=str(remote.get("model", "")),
            )
        else:
            self._provider = LocalEnaProvider()
        self._append(f"[system] Provider: {provider_name}, available={self._provider.is_available()}")

    def _append(self, line: str) -> None:
        self._transcript.append(line)

    def _context_payload(self) -> dict:
        ws = self._get_workspace()
        payload = {"current_file": None, "selection": ""}
        if ws is None:
            return payload
        if self._ctx_current_file.isChecked():
            path, text = self._get_current_file_text()
            payload["current_file"] = path
            payload["current_file_text"] = text[:200_000]
        if self._ctx_selection.isChecked():
            payload["selection"] = self._get_selection_text()[:20_000]
        if self._ctx_tree.isChecked():
            files = self._index.list_workspace_files(ws)
            payload["project_tree"] = [str(p.relative_to(ws)) for p in files[:200]]
        return payload

    def _on_ask(self) -> None:
        prompt = self._input.toPlainText().strip()
        if not prompt:
            return
        ctx = self._context_payload()
        self._append(f"[you] {prompt}")
        resp = self._provider.chat([{"role": "user", "content": prompt}], ctx)
        if resp.error:
            self._append(f"[ena:error] {resp.error}")
            return
        self._append(f"[ena] {resp.text}")

    def _on_propose_patch(self) -> None:
        ws = self._get_workspace()
        if ws is None:
            QMessageBox.information(self, "ENA", "Select a workspace first.")
            return
        goal = self._input.toPlainText().strip() or "improve this file"
        current_path, current_text = self._get_current_file_text()
        files = {current_path: current_text} if current_path else {}
        proposal = self._provider.propose_edits(goal, files, self._get_selection_text(), self._context_payload())
        self._last_proposal = proposal
        if proposal.error:
            self._append(f"[ena:error] {proposal.error}")
            return
        self._append(f"[ena] {proposal.summary}")
        for edit in proposal.edits:
            self._append(f"[diff] {edit.path}\n{edit.unified_diff[:2000]}")

    def _on_apply_patch(self) -> None:
        ws = self._get_workspace()
        if ws is None or self._last_proposal is None or not self._last_proposal.edits:
            QMessageBox.information(self, "ENA", "No patch proposal available.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Apply ENA Patch")
        dlg.resize(900, 560)
        layout = QVBoxLayout(dlg)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlainText("\n\n".join(e.unified_diff for e in self._last_proposal.edits))
        layout.addWidget(preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            for edit in self._last_proposal.edits:
                apply_edit_atomic(ws, edit)
            self._append("[system] Patch applied.")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Patch failed", str(exc))

    def _on_run_checks(self) -> None:
        ws = self._get_workspace()
        if ws is None:
            return
        allowlist = list(self._ena_config.get("tools", {}).get("allowlist", []))
        cmd = ["python", "-m", "pytest", "-q"]
        if not command_is_allowed(cmd, allowlist):
            QMessageBox.warning(self, "Blocked", "Command is not allowlisted.")
            return
        msg = f"Run command?\n\n{' '.join(cmd)}\nCWD: {ws}"
        if QMessageBox.question(self, "Confirm ENA Tool Run", msg) != QMessageBox.StandardButton.Yes:
            return
        if self._process is not None:
            self._process.kill()
            self._process.deleteLater()
        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(ws))
        self._process.setProgram(cmd[0])
        self._process.setArguments(cmd[1:])
        self._process.readyReadStandardOutput.connect(
            lambda: self._append(self._process.readAllStandardOutput().data().decode("utf-8", errors="replace"))
        )
        self._process.readyReadStandardError.connect(
            lambda: self._append(self._process.readAllStandardError().data().decode("utf-8", errors="replace"))
        )
        self._process.finished.connect(lambda code, _status: self._append(f"[tool] exit={code}"))
        self._process.start()
