from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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
from animica_studio.ui.components.primitives import SectionHeader

log = logging.getLogger(__name__)


class _ChatWorker(QThread):
    event = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        agent: EnaAgent,
        session: AgentSession,
        prompt: str,
        *,
        as_agent: bool,
        tool_policy: ToolPolicy,
        allow_mutations: bool,
        allow_exec: bool,
        include_context: dict[str, object],
    ) -> None:
        super().__init__()
        self._agent = agent
        self._session = session
        self._prompt = prompt
        self._as_agent = as_agent
        self._tool_policy = tool_policy
        self._allow_mutations = allow_mutations
        self._allow_exec = allow_exec
        self._include_context = include_context

    def run(self) -> None:
        try:
            for event in self._agent.run(
                self._session,
                self._prompt,
                tool_policy=self._tool_policy,
                allow_mutations=self._allow_mutations,
                allow_exec=self._allow_exec,
                include_context=self._include_context,
                approve_cb=lambda _event: self._as_agent,
            ):
                self.event.emit(event)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class EnaPage(QWidget):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cfg = config
        self._daemon = EnaDaemonManager(port=int(self._cfg.ena.get("local_port", 8765)))
        self._session = AgentSession(session_id="default", workspace=self._workspace_path())
        self._worker: _ChatWorker | None = None
        self._assistant_line_open = False
        self._build()
        self._load_settings()
        self._refresh_workspace_label()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)
        root.addWidget(
            SectionHeader(
                "Animica ENA",
                "Use ENA as the built-in Studio assistant for project analysis, agent workflows, and node-aware developer tasks.",
            )
        )

        runtime_box = QGroupBox("Assistant Runtime")
        runtime_layout = QVBoxLayout(runtime_box)
        top = QHBoxLayout()
        self._mode = QComboBox()
        self._mode.addItems([EnaMode.LOCAL_DAEMON.value, EnaMode.REMOTE_HTTP.value, EnaMode.NETWORK_RPC.value])
        self._status = QLabel("Idle")
        self._workspace_label = QLabel("")
        self._workspace_label.setWordWrap(True)
        ping_btn = QPushButton("Test")
        ping_btn.clicked.connect(self._on_ping)
        start_btn = QPushButton("Start Local ENA")
        start_btn.clicked.connect(self._on_start_local)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_settings)
        top.addWidget(QLabel("Mode"))
        top.addWidget(self._mode)
        top.addWidget(self._status)
        top.addStretch(1)
        top.addWidget(ping_btn)
        top.addWidget(start_btn)
        top.addWidget(save_btn)
        runtime_layout.addLayout(top)

        endpoint_form = QFormLayout()
        self._endpoint = QLineEdit()
        self._ws = QLineEdit()
        self._token = QLineEdit()
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        endpoint_form.addRow("Endpoint", self._endpoint)
        endpoint_form.addRow("WS Endpoint", self._ws)
        endpoint_form.addRow("Auth Token", self._token)
        endpoint_form.addRow("Workspace", self._workspace_label)
        runtime_layout.addLayout(endpoint_form)
        root.addWidget(runtime_box)

        context_box = QGroupBox("Agent Context And Permissions")
        context_layout = QVBoxLayout(context_box)
        ctx = QHBoxLayout()
        self._ctx_tree = QCheckBox("Project tree")
        self._ctx_tree.setChecked(True)
        self._ctx_diff = QCheckBox("Git diff")
        self._ctx_err = QCheckBox("Node/RPC errors")
        for widget in (self._ctx_tree, self._ctx_diff, self._ctx_err):
            ctx.addWidget(widget)
        ctx.addStretch(1)
        context_layout.addLayout(ctx)

        permissions = QHBoxLayout()
        self._allow_readonly_tools = QCheckBox("Read-only tools")
        self._allow_readonly_tools.setChecked(True)
        self._allow_modify_files = QCheckBox("Allow file edits")
        self._allow_exec = QCheckBox("Allow commands")
        for widget in (self._allow_readonly_tools, self._allow_modify_files, self._allow_exec):
            permissions.addWidget(widget)
        permissions.addStretch(1)
        context_layout.addLayout(permissions)
        root.addWidget(context_box)

        convo_box = QGroupBox("Assistant Session")
        convo_layout = QVBoxLayout(convo_box)
        self._chat = QTextEdit()
        self._chat.setReadOnly(True)
        convo_layout.addWidget(self._chat, 1)
        self._prompt = QPlainTextEdit()
        self._prompt.setPlaceholderText("Ask ENA about this workspace, the node state, or an implementation task…")
        self._prompt.setMaximumHeight(110)
        convo_layout.addWidget(self._prompt)
        convo_buttons = QHBoxLayout()
        self._ask_btn = QPushButton("Ask")
        self._ask_btn.clicked.connect(lambda: self._run_chat(False))
        self._agent_btn = QPushButton("Run Agent")
        self._agent_btn.clicked.connect(lambda: self._run_chat(True))
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_chat)
        export_btn = QPushButton("Export JSON")
        export_btn.clicked.connect(self._export)
        convo_buttons.addWidget(self._ask_btn)
        convo_buttons.addWidget(self._agent_btn)
        convo_buttons.addWidget(clear_btn)
        convo_buttons.addWidget(export_btn)
        convo_buttons.addStretch(1)
        convo_layout.addLayout(convo_buttons)
        root.addWidget(convo_box, 1)

        push_box = QGroupBox("Training Bundle Push")
        push_layout = QVBoxLayout(push_box)
        push_row = QHBoxLayout()
        self._bundle_files = QLineEdit()
        self._bundle_files.setReadOnly(True)
        choose = QPushButton("Select Files")
        choose.clicked.connect(self._pick_files)
        self._push_btn = QPushButton("Push To Chain")
        self._push_btn.clicked.connect(self._push_bundle)
        push_row.addWidget(self._bundle_files, 1)
        push_row.addWidget(choose)
        push_row.addWidget(self._push_btn)
        push_layout.addLayout(push_row)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        push_layout.addWidget(self._progress)
        root.addWidget(push_box)

    def _workspace_path(self) -> Path:
        raw = str(self._cfg.ide_workspace_root or self._cfg.workspace_root or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.exists() and path.is_dir():
                return path.resolve()
        return Path.cwd().resolve()

    def _refresh_workspace_label(self) -> None:
        self._session.workspace = self._workspace_path()
        self._workspace_label.setText(str(self._session.workspace))

    def _profile(self) -> EnaProfile:
        active = self._cfg.get_active_profile()
        return EnaProfile(
            mode=EnaMode(self._mode.currentText()),
            endpoint=self._endpoint.text().strip() or "http://127.0.0.1:8765",
            ws_endpoint=self._ws.text().strip(),
            auth_token=self._token.text().strip(),
            rpc_url=active.rpc_url,
        )

    def _tool_policy(self) -> ToolPolicy:
        return ToolPolicy.ALLOW_READONLY if self._allow_readonly_tools.isChecked() else ToolPolicy.ASK

    def _load_settings(self) -> None:
        ena = dict(self._cfg.ena or {})
        self._mode.setCurrentText(str(ena.get("mode") or EnaMode.LOCAL_DAEMON.value))
        self._endpoint.setText(str(ena.get("endpoint") or "http://127.0.0.1:8765"))
        self._ws.setText(str(ena.get("ws_endpoint") or ""))
        self._token.setText(str(ena.get("auth_token") or ""))
        self._allow_modify_files.setChecked(bool(ena.get("allow_modify_files", False)))
        self._allow_exec.setChecked(bool(ena.get("allow_exec", False)))
        self._allow_readonly_tools.setChecked(str(ena.get("tool_policy") or "allow_readonly") != ToolPolicy.ASK.value)

    def _save_settings(self) -> None:
        ena = dict(self._cfg.ena or {})
        ena.update(
            {
                "mode": self._mode.currentText(),
                "endpoint": self._endpoint.text().strip(),
                "ws_endpoint": self._ws.text().strip(),
                "auth_token": self._token.text().strip(),
                "tool_policy": self._tool_policy().value,
                "allow_modify_files": self._allow_modify_files.isChecked(),
                "allow_exec": self._allow_exec.isChecked(),
            }
        )
        self._cfg.ena = ena
        save_config(self._cfg)

    def _set_busy(self, busy: bool) -> None:
        self._ask_btn.setEnabled(not busy)
        self._agent_btn.setEnabled(not busy)

    def _append(self, text: str) -> None:
        self._finish_assistant_line()
        self._chat.append(text)

    def _append_assistant_chunk(self, text: str) -> None:
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self._assistant_line_open:
            cursor.insertText(text)
        else:
            if self._chat.toPlainText():
                cursor.insertText("\n")
            cursor.insertText(f"[ena] {text}")
            self._assistant_line_open = True
        self._chat.setTextCursor(cursor)
        self._chat.ensureCursorVisible()

    def _finish_assistant_line(self) -> None:
        if not self._assistant_line_open:
            return
        cursor = self._chat.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("\n")
        self._chat.setTextCursor(cursor)
        self._assistant_line_open = False

    def _on_start_local(self) -> None:
        try:
            status = self._daemon.start()
            self._status.setText(f"Local daemon running (pid={status.pid})")
            self._append(f"[ena:system] Started local daemon at {status.endpoint}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Animica ENA", str(exc))

    def _on_ping(self) -> None:
        self._save_settings()
        try:
            response = EnaClient(self._profile()).ping()
            if response.get("ok"):
                self._status.setText("Ready")
            else:
                self._status.setText("Unavailable")
            self._append(f"[ena:health] {json.dumps(response, ensure_ascii=False)}")
        except Exception as exc:  # noqa: BLE001
            self._status.setText("Error")
            self._append(f"[ena:error] {exc}")

    def _run_chat(self, as_agent: bool) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        prompt = self._prompt.toPlainText().strip()
        if not prompt:
            return

        self._refresh_workspace_label()
        self._save_settings()
        client = EnaClient(self._profile())
        agent = EnaAgent(
            client,
            rpc_caller=lambda method, params: self._rpc_call(method, params),
            logs_provider=lambda _service: self._chat.toPlainText().splitlines()[-40:],
        )
        include_context = {
            "workspace": str(self._session.workspace),
            "include_project_tree": self._ctx_tree.isChecked(),
            "include_diffs": self._ctx_diff.isChecked(),
            "include_errors": self._ctx_err.isChecked(),
        }
        self._append(f"[you] {prompt}")
        self._status.setText("Running" if as_agent else "Thinking")
        self._set_busy(True)
        self._worker = _ChatWorker(
            agent,
            self._session,
            prompt,
            as_agent=as_agent,
            tool_policy=self._tool_policy(),
            allow_mutations=self._allow_modify_files.isChecked(),
            allow_exec=self._allow_exec.isChecked(),
            include_context=include_context,
        )
        self._worker.event.connect(self._on_event)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_chat_finished)
        self._worker.start()

    def _rpc_call(self, method: str, params: object) -> object:
        from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

        client = RpcClient(self._profile().rpc_url)
        try:
            return client.call(method, params if isinstance(params, list) else [params])
        finally:
            client.close()

    def _on_event(self, event: dict) -> None:
        event_type = str(event.get("type") or "token")
        if event_type == "token":
            self._append_assistant_chunk(str(event.get("text") or ""))
            return
        if event_type == "tool_result":
            tool_event = event.get("event") if isinstance(event.get("event"), dict) else {}
            tool_name = str(tool_event.get("name") or "tool")
            result = event.get("result")
            rendered = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            self._append(f"[tool:{tool_name}] {rendered}")
            return
        if event_type == "done":
            self._append("[ena:done]")
            return
        if event_type == "error":
            self._append(f"[ena:error] {json.dumps(event, ensure_ascii=False)}")
            return
        self._append(json.dumps(event, ensure_ascii=False))

    def _on_failed(self, message: str) -> None:
        self._status.setText("Error")
        self._append(f"[ena:error] {message}")

    def _on_chat_finished(self) -> None:
        self._finish_assistant_line()
        self._set_busy(False)
        if self._status.text() not in {"Error", "Unavailable"}:
            self._status.setText("Ready")
        self._prompt.clear()
        self._worker = None

    def _clear_chat(self) -> None:
        self._assistant_line_open = False
        self._chat.clear()
        self._session.messages = []
        self._session.diagnostics = []

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
        files = [Path(item) for item in self._bundle_files.text().split(";") if item]
        if not files:
            QMessageBox.information(self, "Push Training", "Select files first")
            return
        service = TrainingPushService(self._cfg.get_active_profile().rpc_url)
        self._progress.setValue(10)
        bundle = service.build_bundle(files, bundle_type="dataset", metadata={"name": "studio bundle", "privacy": "public"})
        self._progress.setValue(40)
        upload = service.upload_bundle(bundle, resume_key=bundle["bundle_root"])
        self._progress.setValue(70)
        tx = service.submit_bundle_tx(upload, bundle["manifest"])
        self._progress.setValue(100)
        self._append(f"[training:push] {json.dumps({'bundle': bundle['bundle_root'], 'tx': tx}, ensure_ascii=False)}")
