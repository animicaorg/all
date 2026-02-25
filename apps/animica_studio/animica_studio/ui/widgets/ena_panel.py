from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_inference_service import GenerationConfig
from animica_studio.services.ena_service import (
    EnaEditProposal,
    EnaIdeAssistantProvider,
    EnaProvider,
    LocalEnaProvider,
    WorkspaceIndexService,
    apply_edit_atomic,
    command_is_allowed,
)


class EnaPanel(QWidget):
    def __init__(
        self,
        get_workspace,
        get_current_file_text,
        get_selection_text,
        get_cursor_position,
        get_open_tabs,
        ena_config: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._get_workspace = get_workspace
        self._get_current_file_text = get_current_file_text
        self._get_selection_text = get_selection_text
        self._get_cursor_position = get_cursor_position
        self._get_open_tabs = get_open_tabs
        self._ena_config = ena_config
        self._provider: EnaProvider = LocalEnaProvider()
        self._assistant_provider = EnaIdeAssistantProvider(ena_config, self)
        self._history: list[dict[str, str]] = []
        self._assistant_line = ""
        self._streaming = False
        self._last_user_prompt = ""
        self._index = WorkspaceIndexService(
            max_file_bytes=200_000,
            max_total_bytes=int(ena_config.get("context", {}).get("max_bytes", 1_000_000)),
        )
        self._process: QProcess | None = None
        self._last_proposal: EnaEditProposal | None = None
        self._build_ui()
        self._assistant_provider.chunk.connect(self._on_chunk)
        self._assistant_provider.error.connect(self._on_inference_error)
        self._assistant_provider.finished.connect(self._on_finished)
        QTimer.singleShot(0, self._init_provider)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("ENA"))
        self._transcript = QTextEdit()
        self._transcript.setReadOnly(True)
        layout.addWidget(self._transcript, 1)

        top = QHBoxLayout()
        top.addWidget(QLabel("Model"))
        self._model_combo = QComboBox()
        top.addWidget(self._model_combo, 1)
        self._refresh_models_btn = QPushButton("Refresh")
        self._refresh_models_btn.clicked.connect(self._refresh_models)
        self._model_combo.currentIndexChanged.connect(self._on_model_selected)
        top.addWidget(self._refresh_models_btn)
        self._open_models_btn = QPushButton("Open Training/Models")
        self._open_models_btn.clicked.connect(self._show_model_help)
        top.addWidget(self._open_models_btn)
        layout.addLayout(top)

        settings = QFormLayout()
        self._use_memory = QCheckBox("Use conversation context")
        self._use_memory.setChecked(True)
        self._temperature = QDoubleSpinBox(); self._temperature.setRange(0.0, 2.0); self._temperature.setSingleStep(0.05)
        self._top_p = QDoubleSpinBox(); self._top_p.setRange(0.0, 1.0); self._top_p.setSingleStep(0.05)
        self._max_new_tokens = QSpinBox(); self._max_new_tokens.setRange(1, 8192)
        self._context_chars = QSpinBox(); self._context_chars.setRange(500, 500_000)
        self._threads = QSpinBox(); self._threads.setRange(0, 128)
        self._device = QComboBox(); self._device.addItems(["auto", "cpu", "cuda", "mps"])
        self._system_prompt = QPlainTextEdit(); self._system_prompt.setMaximumHeight(68)
        settings.addRow("", self._use_memory)
        settings.addRow("temperature", self._temperature)
        settings.addRow("top_p", self._top_p)
        settings.addRow("max_new_tokens", self._max_new_tokens)
        settings.addRow("context size (chars)", self._context_chars)
        settings.addRow("threads", self._threads)
        settings.addRow("device", self._device)
        settings.addRow("system prompt", self._system_prompt)
        layout.addLayout(settings)

        row = QHBoxLayout()
        self._ctx_current_file = QCheckBox("Current file")
        self._ctx_current_file.setChecked(True)
        self._ctx_selection = QCheckBox("Selection")
        self._ctx_selection.setChecked(True)
        self._ctx_tree = QCheckBox("Project tree")
        self._ctx_open_files = QCheckBox("Open tabs")
        for w in [self._ctx_current_file, self._ctx_selection, self._ctx_open_files, self._ctx_tree]:
            row.addWidget(w)
        layout.addLayout(row)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText("Ask ENA for help…")
        self._input.setMaximumHeight(84)
        layout.addWidget(self._input)

        btns = QHBoxLayout()
        ask = QPushButton("Send")
        ask.clicked.connect(self._on_ask)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.clicked.connect(self._on_stop)
        self._stop_btn.setEnabled(False)
        propose = QPushButton("Propose Patch")
        propose.clicked.connect(self._on_propose_patch)
        apply_btn = QPushButton("Apply Patch…")
        apply_btn.clicked.connect(self._on_apply_patch)
        run_btn = QPushButton("Run Checks")
        run_btn.clicked.connect(self._on_run_checks)
        btns.addWidget(ask)
        btns.addWidget(self._stop_btn)
        btns.addWidget(propose)
        btns.addWidget(apply_btn)
        btns.addWidget(run_btn)
        layout.addLayout(btns)

        self._ask_btn = ask
        self._load_settings()

    def _init_provider(self) -> None:
        self._provider = LocalEnaProvider()
        self._refresh_models()
        self._append_provider_status()

    def _append(self, line: str) -> None:
        self._transcript.append(line)

    def _append_provider_status(self) -> None:
        available, reason = self._assistant_provider.available_status()
        if available:
            self._append("[system] Provider: local, available=True")
        else:
            self._append(f"[system] Provider: local, available=False (reason={reason})")

    def _show_model_help(self) -> None:
        QMessageBox.information(
            self,
            "Local ENA model required",
            "No local ENA model configured. Train or download a model first.\n"
            "Go to ENA > Training or ENA > Use ENA (Inference), then select a local checkpoint here.",
        )

    def _refresh_models(self) -> None:
        models = self._assistant_provider.list_models()
        selected = self._assistant_provider.selected_model_path()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for model in models:
            self._model_combo.addItem(f"{model.name} ({model.training_run_id or 'run'})", model.checkpoint_path)
        if selected:
            idx = self._model_combo.findData(selected)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        if self._model_combo.count() == 0:
            self._model_combo.addItem("No local models", "")
        self._model_combo.blockSignals(False)
        self._on_model_selected(self._model_combo.currentIndex())
        self._append_provider_status()

    def _on_model_selected(self, _index: int) -> None:
        model_path = str(self._model_combo.currentData() or "")
        self._assistant_provider.set_selected_model_path(model_path)
        self._save_settings()

    def _context_payload(self) -> dict:
        ws = self._get_workspace()
        payload = {
            "current_file": None,
            "selection": "",
            "cursor_line": 1,
            "cursor_column": 1,
            "language": "text",
            "open_tabs": [],
            "context_window": "",
            "project_root": str(ws) if ws else "",
        }
        if ws is None:
            return payload

        if self._ctx_current_file.isChecked():
            path, text = self._get_current_file_text()
            payload["current_file"] = path
            payload["language"] = self._guess_language(path)
            payload["current_file_text"] = text[:200_000]
            line, col = self._get_cursor_position()
            payload["cursor_line"] = max(1, line)
            payload["cursor_column"] = max(1, col)
            payload["context_window"] = self._extract_context_window(text, payload["cursor_line"])
        if self._ctx_selection.isChecked():
            payload["selection"] = self._get_selection_text()[:20_000]
        if self._ctx_open_files.isChecked():
            payload["open_tabs"] = list(self._get_open_tabs())[:10]
        if self._ctx_tree.isChecked():
            files = self._index.list_workspace_files(ws)
            payload["project_tree"] = [str(p.relative_to(ws)) for p in files[:120]]
        return payload

    def _compose_prompt(self, user_text: str, context: dict) -> str:
        system_prompt = self._effective_system_prompt()
        blocks = [
            f"System instruction:\n{system_prompt}",
            f"User request:\n{user_text}",
            "Current file context:",
            f"- path: {context.get('current_file') or '(none)'}",
            f"- language: {context.get('language')}",
            f"- cursor: line {context.get('cursor_line')}, col {context.get('cursor_column')}",
        ]
        selection = str(context.get("selection") or "").strip()
        if selection:
            blocks.append("Selected text (highest priority):\n```\n" + selection + "\n```")
        win = str(context.get("context_window") or "")
        if win:
            blocks.append("Surrounding file window:\n```\n" + win + "\n```")
        tabs = context.get("open_tabs") or []
        if tabs:
            blocks.append("Open tabs: " + ", ".join(str(t) for t in tabs[:10]))
        return "\n\n".join(blocks)

    def _on_ask(self) -> None:
        if self._streaming:
            return
        prompt = self._input.toPlainText().strip()
        if not prompt:
            return

        available, _reason = self._assistant_provider.available_status()
        if not available:
            self._append_provider_status()
            self._show_model_help()
            return

        cfg = self._generation_cfg()
        ctx = self._context_payload()
        built_prompt = self._compose_prompt(prompt, ctx)
        history = list(self._history[-8:]) if self._use_memory.isChecked() else []

        self._append(f"[you] {prompt}")
        self._assistant_line = "[ena] …"
        self._append(self._assistant_line)
        self._streaming = True
        self._last_user_prompt = prompt
        self._ask_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        started = self._assistant_provider.start(built_prompt, history, cfg)
        if not started:
            self._streaming = False
            self._ask_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._append_provider_status()
            self._show_model_help()
            return
        self._save_settings()

    def _on_stop(self) -> None:
        self._assistant_provider.cancel()
        self._streaming = False
        self._ask_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _on_chunk(self, text: str) -> None:
        self._assistant_line += text
        lines = self._transcript.toPlainText().splitlines()
        if not lines:
            self._transcript.setPlainText(self._assistant_line)
            return
        lines[-1] = self._assistant_line
        self._transcript.setPlainText("\n".join(lines))

    def _on_inference_error(self, message: str, details: str) -> None:
        self._append(f"[ena:error] {message}")
        if details:
            self._append(f"[ena:error:details] {details[:300]}")

    def _on_finished(self, ok: bool, stats: dict) -> None:
        self._streaming = False
        self._ask_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        if not ok:
            self._assistant_line += " [stopped]"
            return
        assistant = str(stats.get("text") or "").strip()
        if self._use_memory.isChecked() and assistant:
            self._history.append({"user": self._last_user_prompt, "assistant": assistant})
        self._input.clear()

    def _effective_system_prompt(self) -> str:
        custom = self._system_prompt.toPlainText().strip()
        if custom:
            return custom
        return (
            "You are ENA, a coding assistant for this project. Answer directly. "
            "If code changes are requested, produce a minimal patch-like response. "
            "Ask a question only if absolutely necessary."
        )

    def _generation_cfg(self) -> GenerationConfig:
        temp = float(self._temperature.value())
        top_p = float(self._top_p.value())
        max_new = int(self._max_new_tokens.value())
        threads = int(self._threads.value())
        if top_p <= 0:
            top_p = 0.01
            self._top_p.setValue(top_p)
            self._append("[system] top_p was clamped to 0.01")
        if max_new < 1:
            max_new = 1
            self._max_new_tokens.setValue(max_new)
            self._append("[system] max_new_tokens was clamped to 1")
        return GenerationConfig(
            temperature=max(0.0, min(2.0, temp)),
            top_p=max(0.01, min(1.0, top_p)),
            max_new_tokens=max(1, min(8192, max_new)),
            system_prompt=self._effective_system_prompt(),
            context_tokens=int(self._context_chars.value()),
            device=self._device.currentText(),
            threads=max(0, min(128, threads)),
            use_conversation_context=self._use_memory.isChecked(),
        )

    def _extract_context_window(self, text: str, cursor_line: int) -> str:
        max_chars = int(self._context_chars.value())
        lines = text.splitlines()
        if not lines:
            return ""
        pivot = max(1, min(cursor_line, len(lines))) - 1
        start = max(0, pivot - 200)
        end = min(len(lines), pivot + 201)
        block = "\n".join(lines[start:end])
        if len(block) <= max_chars:
            return block
        return block[:max_chars]

    def _guess_language(self, path: str) -> str:
        suffix = Path(path).suffix.lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".json": "json",
            ".md": "markdown",
            ".rs": "rust",
            ".go": "go",
        }
        return mapping.get(suffix, "text")

    def _load_settings(self) -> None:
        ide_cfg = self._ena_config.get("ide_assistant") if isinstance(self._ena_config.get("ide_assistant"), dict) else {}
        self._temperature.setValue(float(ide_cfg.get("temperature", 0.7)))
        self._top_p.setValue(float(ide_cfg.get("top_p", 0.95)))
        self._max_new_tokens.setValue(int(ide_cfg.get("max_new_tokens", 256)))
        self._context_chars.setValue(int(ide_cfg.get("context_chars", 12000)))
        self._threads.setValue(int(ide_cfg.get("threads", 0)))
        self._use_memory.setChecked(bool(ide_cfg.get("use_conversation_context", True)))
        self._system_prompt.setPlainText(str(ide_cfg.get("system_prompt", "")))
        device = str(ide_cfg.get("device", "auto"))
        idx = self._device.findText(device)
        self._device.setCurrentIndex(idx if idx >= 0 else 0)

    def _save_settings(self) -> None:
        self._ena_config["ide_assistant"] = {
            "model_path": str(self._model_combo.currentData() or ""),
            "temperature": float(self._temperature.value()),
            "top_p": float(self._top_p.value()),
            "max_new_tokens": int(self._max_new_tokens.value()),
            "context_chars": int(self._context_chars.value()),
            "threads": int(self._threads.value()),
            "device": self._device.currentText(),
            "use_conversation_context": self._use_memory.isChecked(),
            "system_prompt": self._system_prompt.toPlainText(),
        }

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
