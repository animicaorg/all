from __future__ import annotations

# Existing modules map (Phase 0 baseline):
# - services/: rpc_client, da_service, ena_service, process_manager, diagnostics
# - ui/pages/: dashboard/wallet/node/mining/aicf/da/quantum/console/ide/settings
# - ui/widgets/: ena_panel (legacy IDE side panel)
# - storage/: config JSON persistence
# - models/: profile/rpc/diagnostics dataclasses

import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_agent import AgentSession, EnaAgent
from animica_studio.services.ena_client import EnaClient, EnaMode, EnaProfile
from animica_studio.services.ena_daemon import EnaDaemonManager
from animica_studio.services.ena_tools import ToolPolicy
from animica_studio.services.training_push import TrainingPushService
from animica_studio.storage.config import Config, save_config


class _ChatWorker(QThread):
    event = Signal(dict)
    failed = Signal(str)

    def __init__(self, agent: EnaAgent, session: AgentSession, prompt: str, as_agent: bool, include_diffs: bool) -> None:
        super().__init__()
        self._agent = agent
        self._session = session
        self._prompt = prompt
        self._as_agent = as_agent
        self._include_diffs = include_diffs

    def run(self) -> None:
        try:
            for event in self._agent.run(
                self._session,
                self._prompt,
                tool_policy=ToolPolicy.ALLOW_READONLY,
                include_context={"include_diffs": self._include_diffs},
                approve_cb=lambda _e: self._as_agent,
            ):
                self.event.emit(event)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class EnaPage(QWidget):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._cfg = config
        self._daemon = EnaDaemonManager(port=int(self._cfg.ena.get("local_port", 8765)))
        self._session = AgentSession(session_id="default", workspace=Path(self._cfg.workspace_root or Path.cwd()))
        self._worker: _ChatWorker | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(QLabel("ENA"))

        top = QHBoxLayout()
        self._mode = QComboBox()
        self._mode.addItems([EnaMode.LOCAL_DAEMON.value, EnaMode.REMOTE_HTTP.value, EnaMode.NETWORK_RPC.value])
        self._mode.setCurrentText(self._cfg.ena.get("mode", EnaMode.LOCAL_DAEMON.value))
        self._status = QLabel("idle")
        ping_btn = QPushButton("Test")
        ping_btn.clicked.connect(self._on_ping)
        start_btn = QPushButton("Start ENA (CPU)")
        start_btn.clicked.connect(self._on_start_local)
        top.addWidget(QLabel("Mode")); top.addWidget(self._mode); top.addWidget(self._status); top.addStretch(1); top.addWidget(ping_btn); top.addWidget(start_btn)
        root.addLayout(top)

        self._chat = QTextEdit(); self._chat.setReadOnly(True); root.addWidget(self._chat, 1)
        ctx = QHBoxLayout()
        self._ctx_tree = QCheckBox("Project tree")
        self._ctx_diff = QCheckBox("Diffs")
        self._ctx_err = QCheckBox("Errors")
        for w in (self._ctx_tree, self._ctx_diff, self._ctx_err):
            ctx.addWidget(w)
        root.addLayout(ctx)

        self._prompt = QPlainTextEdit(); self._prompt.setMaximumHeight(100); root.addWidget(self._prompt)
        row = QHBoxLayout()
        send = QPushButton("Send")
        send.clicked.connect(lambda: self._run_chat(False))
        agent = QPushButton("Run as Agent")
        agent.clicked.connect(lambda: self._run_chat(True))
        export = QPushButton("Export JSON")
        export.clicked.connect(self._export)
        row.addWidget(send); row.addWidget(agent); row.addWidget(export)
        root.addLayout(row)

        form = QFormLayout()
        self._endpoint = QLineEdit(self._cfg.ena.get("endpoint", "http://127.0.0.1:8765"))
        self._ws = QLineEdit(self._cfg.ena.get("ws_endpoint", ""))
        self._token = QLineEdit(self._cfg.ena.get("auth_token", "")); self._token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Endpoint", self._endpoint); form.addRow("WS Endpoint", self._ws); form.addRow("Auth Token", self._token)
        root.addLayout(form)

        root.addWidget(QLabel("Push Training Bundle"))
        push_row = QHBoxLayout()
        self._bundle_files = QLineEdit(); self._bundle_files.setReadOnly(True)
        choose = QPushButton("Select Files")
        choose.clicked.connect(self._pick_files)
        self._push_btn = QPushButton("Push to Chain")
        self._push_btn.clicked.connect(self._push_bundle)
        self._progress = QProgressBar(); self._progress.setRange(0, 100)
        push_row.addWidget(self._bundle_files, 1); push_row.addWidget(choose); push_row.addWidget(self._push_btn)
        root.addLayout(push_row)
        root.addWidget(self._progress)

    def _profile(self) -> EnaProfile:
        return EnaProfile(
            mode=EnaMode(self._mode.currentText()),
            endpoint=self._endpoint.text().strip(),
            ws_endpoint=self._ws.text().strip(),
            auth_token=self._token.text().strip(),
            rpc_url=self._cfg.get_active_profile().rpc_url,
        )

    def _append(self, text: str) -> None:
        self._chat.append(text)

    def _on_start_local(self) -> None:
        try:
            st = self._daemon.start()
            self._status.setText(f"running:{st.pid}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "ENA", str(exc))

    def _on_ping(self) -> None:
        try:
            resp = EnaClient(self._profile()).ping()
            self._status.setText("ok" if resp.get("ok") else "unavailable")
            self._append(f"[ping] {json.dumps(resp, ensure_ascii=False)}")
        except Exception as exc:  # noqa: BLE001
            self._status.setText("error")
            self._append(f"[ping:error] {exc}")

    def _run_chat(self, as_agent: bool) -> None:
        prompt = self._prompt.toPlainText().strip()
        if not prompt:
            return
        client = EnaClient(self._profile())
        agent = EnaAgent(client)
        self._append(f"[you] {prompt}")
        self._worker = _ChatWorker(agent, self._session, prompt, as_agent, self._ctx_diff.isChecked())
        self._worker.event.connect(self._on_event)
        self._worker.failed.connect(lambda err: self._append(f"[error] {err}"))
        self._worker.start()

    def _on_event(self, event: dict) -> None:
        if event.get("type") == "token":
            self._append(event.get("text", ""))
        else:
            self._append(json.dumps(event, ensure_ascii=False))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export conversation", "ena-session.json", "JSON (*.json)")
        if not path:
            return
        Path(path).write_text(json.dumps(self._session.messages, indent=2), encoding="utf-8")

    def _pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Training files")
        if files:
            self._bundle_files.setText(";".join(files))

    def _push_bundle(self) -> None:
        files = [Path(x) for x in self._bundle_files.text().split(";") if x]
        if not files:
            QMessageBox.information(self, "Push Training", "Select files first")
            return
        svc = TrainingPushService(self._cfg.get_active_profile().rpc_url)
        self._progress.setValue(10)
        bundle = svc.build_bundle(files, bundle_type="dataset", metadata={"name": "studio bundle", "privacy": "public"})
        self._progress.setValue(40)
        upload = svc.upload_bundle(bundle, resume_key=bundle["bundle_root"])
        self._progress.setValue(70)
        tx = svc.submit_bundle_tx(upload, bundle["manifest"])
        self._progress.setValue(100)
        self._append(f"[push] {json.dumps({'bundle': bundle['bundle_root'], 'tx': tx}, ensure_ascii=False)}")

        self._cfg.ena.update({"mode": self._mode.currentText(), "endpoint": self._endpoint.text(), "ws_endpoint": self._ws.text(), "auth_token": self._token.text()})
        save_config(self._cfg)
