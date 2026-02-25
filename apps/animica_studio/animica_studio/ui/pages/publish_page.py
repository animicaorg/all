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
        self.configure_da_btn = QPushButton("Configure DA Now")
        self.configure_da_btn.clicked.connect(self._configure_da_now)
        actions.addWidget(self.configure_da_btn)

        self.allow_remote_toggle = QCheckBox("Allow uploads (allow_remote_put)")
        self.allow_remote_toggle.setChecked(False)
        self.allow_remote_toggle.setToolTip("Enable only on local/dev nodes you control.")
        actions.addWidget(self.allow_remote_toggle)

        self.local_upload_btn = QPushButton("Retry DA Upload")
        self.local_upload_btn.clicked.connect(self._retry_local_ingest)
        self.local_upload_btn.setEnabled(False)
        actions.addWidget(self.local_upload_btn)

        self.retry_register_btn = QPushButton("Retry AICF Register")
        self.retry_register_btn.clicked.connect(self._retry_register)
        self.retry_register_btn.setEnabled(False)
        actions.addWidget(self.retry_register_btn)

        self.copy_diag_btn = QPushButton("Copy diagnostics")
        self.copy_diag_btn.clicked.connect(self._copy_diagnostics)
        self.copy_diag_btn.setEnabled(False)
        actions.addWidget(self.copy_diag_btn)

        self.run_curl_btn = QPushButton("Run this curl")
        self.run_curl_btn.clicked.connect(self._show_curl_commands)
        self.run_curl_btn.setEnabled(False)
        actions.addWidget(self.run_curl_btn)
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
        self.copy_diag_btn.setEnabled(False)
        self.local_upload_btn.setEnabled(False)
        self.retry_register_btn.setEnabled(False)
        self.run_curl_btn.setEnabled(False)

        run = out.get("run")
        if run and getattr(run, "status", "") in {"failed", "partial"}:
            failed_steps = [s for s in run.steps if s.status == "failed"]
            if failed_steps:
                step = failed_steps[0]
                details = step.error_details or run.result.get(step.name, {}) or {}
                self._last_diag = details.get("diagnostics") or {}
                actions = details.get("actions") or []
                self.copy_diag_btn.setEnabled(bool(self._last_diag))
                self.local_upload_btn.setEnabled(
                    any(a.get("id") in {"local_upload", "enable_remote_put"} for a in actions if isinstance(a, dict))
                    or bool((self._last_diag or {}).get("local_node"))
                )
                if details.get("error_code") == "DA_NOT_CONFIGURED":
                    self.out.setPlainText("Publish prepared locally (DA upload pending). Configure DA Now, then Retry DA Upload.\n\n" + str(out))
                    return
            if getattr(run, "status", "") == "partial":
                self.out.setPlainText("Publish prepared locally (DA upload pending) or register pending.\n\n" + str(out))
                self.local_upload_btn.setEnabled(True)
                self.retry_register_btn.setEnabled(True)
                return
        self.out.setPlainText(str(out))

    def _configure_da_now(self) -> None:
        status = self.service.da_status.get_status()
        limit = int(status.get("effective_limit") or 10 * 1024 * 1024 * 1024)
        dir_path = str(status.get("default_dir") or status.get("configured_dir") or status.get("raw", {}).get("dir") or "/data/da")
        res = self.service.da_status.enable_da(dir_path=dir_path, limit_bytes=limit)
        if not res.get("ok"):
            status_after = res.get("status") if isinstance(res.get("status"), dict) else {}
            reason = status_after.get("reason") or status_after.get("policy_blocked_reason") or res.get("error")
            self._last_diag = {
                "error": res.get("error"),
                "request_payload": res.get("request_payload"),
                "response": res.get("response"),
                "status": status_after,
                "curl_configure": res.get("curl_configure"),
                "curl_get_status": res.get("curl_get_status"),
            }
            self.copy_diag_btn.setEnabled(True)
            self.run_curl_btn.setEnabled(bool(res.get("curl_configure") or res.get("curl_get_status")))
            self.out.append("\nNode refused to configure DA")
            self.out.append("Request payload: " + json.dumps(res.get("request_payload"), indent=2, sort_keys=True))
            self.out.append("Response: " + json.dumps(res.get("response"), indent=2, sort_keys=True))
            self.out.append("Verify status: " + json.dumps(status_after, indent=2, sort_keys=True))
            self.out.append(f"Reason: {reason}")
            return

        if self.allow_remote_toggle.isChecked() and status.get("can_configure_allow_remote_put"):
            try:
                self.service.da.configure({"allow_remote_put": True})
            except Exception:
                pass

        status_after = self.service.da_status.get_status()
        self._last_diag = {
            "request_payload": res.get("request_payload") or res.get("payload"),
            "response": res.get("response"),
            "status": status_after,
            "curl_configure": res.get("curl_configure"),
            "curl_get_status": res.get("curl_get_status") or status_after.get("curl_get_status"),
        }
        self.copy_diag_btn.setEnabled(True)
        self.run_curl_btn.setEnabled(bool(self._last_diag.get("curl_configure") or self._last_diag.get("curl_get_status")))
        if status_after.get("enabled") and status_after.get("ok"):
            self.out.append("\nDA configured successfully. You can now Retry Push to DA.")
            self.local_upload_btn.setEnabled(True)
        else:
            reason = status_after.get("reason") or status_after.get("policy_blocked_reason") or res.get("error")
            self.out.append("\nNode refused to configure DA")
            self.out.append("Request payload: " + json.dumps(self._last_diag.get("request_payload"), indent=2, sort_keys=True))
            self.out.append("Response: " + json.dumps(self._last_diag.get("response"), indent=2, sort_keys=True))
            self.out.append("Verify status: " + json.dumps(status_after, indent=2, sort_keys=True))
            self.out.append(f"Reason: {reason}")

    def _retry_local_ingest(self) -> None:
        self.out.append("\nRetrying Push to DA…")
        self._run()

    def _copy_diagnostics(self) -> None:
        if not self._last_diag:
            return
        text = json.dumps(self._last_diag, indent=2, sort_keys=True)
        self.out.append("\nDiagnostics copied to output:\n" + text)

    def _show_curl_commands(self) -> None:
        if not self._last_diag:
            return
        cfg = self._last_diag.get("curl_configure")
        st = self._last_diag.get("curl_get_status")
        if cfg:
            self.out.append("\nRun this curl (configure):\n" + str(cfg))
        if st:
            self.out.append("\nRun this curl (status):\n" + str(st))

    def _retry_register(self) -> None:
        self.out.append("\nRetrying AICF register…")
        self._run()
