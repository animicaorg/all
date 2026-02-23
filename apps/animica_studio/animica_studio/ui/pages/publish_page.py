from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

from animica_studio.services.ena_automation_service import EnaService


class PublishPage(QWidget):
    def __init__(self, service: EnaService, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Publish to Network (DA)")
        self.service = service
        root = QVBoxLayout(self)
        self.dev = QCheckBox("Use local dev DA stub")
        root.addWidget(self.dev)
        b = QPushButton("Publish selected checkpoint")
        b.clicked.connect(self._run)
        root.addWidget(b)

        actions = QHBoxLayout()
        self.enable_remote_btn = QPushButton("Enable DA uploads (allow_remote_put)")
        self.enable_remote_btn.clicked.connect(self._enable_remote_put)
        self.enable_remote_btn.setEnabled(False)
        actions.addWidget(self.enable_remote_btn)

        self.local_upload_btn = QPushButton("Upload locally (no remote put)")
        self.local_upload_btn.setEnabled(False)
        actions.addWidget(self.local_upload_btn)

        self.copy_diag_btn = QPushButton("Copy diagnostics")
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        self.copy_diag_btn.setEnabled(False)
        actions.addWidget(self.copy_diag_btn)
        root.addLayout(actions)

        self._last_diag: dict = {}
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        root.addWidget(self.out)

    def _run(self) -> None:
        cps = self.service.list_checkpoints()
        if not cps:
            self.out.setPlainText("No local checkpoints available")
            return
        out = self.service.publish_checkpoint(cps[-1]["sha256"], dev_mode=self.dev.isChecked())
        self._last_diag = {}
        self.enable_remote_btn.setEnabled(False)
        self.copy_diag_btn.setEnabled(False)
        self.local_upload_btn.setEnabled(False)

        run = out.get("run")
        if run and getattr(run, "status", "") == "failed":
            failed_steps = [s for s in run.steps if s.status == "failed"]
            if failed_steps:
                step = failed_steps[0]
                details = step.error_details or run.result.get(step.name, {}) or {}
                self._last_diag = details.get("diagnostics") or {}
                actions = details.get("actions") or []
                can_enable = any(a.get("id") == "enable_remote_put" for a in actions if isinstance(a, dict))
                self.enable_remote_btn.setEnabled(bool(can_enable))
                self.copy_diag_btn.setEnabled(bool(self._last_diag))
                self.local_upload_btn.setEnabled(any(a.get("id") == "local_upload" for a in actions if isinstance(a, dict)))
        self.out.setPlainText(str(out))

    def _enable_remote_put(self) -> None:
        status = self.service.da_status.get_status()
        if not status.get("can_configure_allow_remote_put"):
            self.out.append("\nNode does not expose allow_remote_put in da.configure schema.")
            return
        dir_path = status.get("configured_dir") or status.get("raw", {}).get("dir") or "/data/da"
        limit = int(status.get("effective_limit") or 10 * 1024 * 1024 * 1024)
        res = self.service.da_status.enable_da(dir_path=dir_path, limit_bytes=limit)
        if res.get("ok"):
            status_obj = self.service.da.status() if hasattr(self.service.da, "status") else {}
            if isinstance(status_obj, dict) and status_obj.get("allow_remote_put") is not True:
                self.service.da.configure({"allow_remote_put": True})
            self.out.append("\nallow_remote_put enabled. Retry Push to DA.")
        else:
            self.out.append("\nFailed to enable allow_remote_put: " + str(res.get("error") or res))

    def _copy_diagnostics(self) -> None:
        if not self._last_diag:
            return
        text = json.dumps(self._last_diag, indent=2, sort_keys=True)
        self.out.append("\nDiagnostics copied to output:\n" + text)
