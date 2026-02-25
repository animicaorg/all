from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.ena_inference_service import EnaInferenceService, GenerationConfig
from animica_studio.services.ena_model_repository import EnaModelRepository, ModelEntry
from animica_studio.storage.config import Config, save_config


class InferPage(QWidget):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Use ENA (Inference)")
        self._cfg = config
        self._repo = EnaModelRepository()
        self._svc = EnaInferenceService(self)
        self._models: list[ModelEntry] = []
        self._active_handle: str | None = None
        self._assistant_row: int | None = None

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.model_combo = QComboBox()
        self.refresh_models_btn = QPushButton("Refresh models")
        top.addWidget(QLabel("Local checkpoint"))
        top.addWidget(self.model_combo, 1)
        top.addWidget(self.refresh_models_btn)
        root.addLayout(top)

        self.no_models = QLabel("No local models found. Train a model in the Training page first.")
        root.addWidget(self.no_models)

        settings = QFormLayout()
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0.0, 2.0); self.temperature.setValue(0.7)
        self.top_p = QDoubleSpinBox(); self.top_p.setRange(0.0, 1.0); self.top_p.setValue(0.95)
        self.max_tokens = QSpinBox(); self.max_tokens.setRange(1, 8192); self.max_tokens.setValue(128)
        self.rep_penalty = QDoubleSpinBox(); self.rep_penalty.setRange(0.5, 3.0); self.rep_penalty.setValue(1.0)
        self.seed = QSpinBox(); self.seed.setRange(0, 2_147_483_647); self.seed.setSpecialValueText("auto")
        self.ctx_tokens = QSpinBox(); self.ctx_tokens.setRange(128, 65536); self.ctx_tokens.setValue(2048)
        self.device = QComboBox(); self.device.addItems(["auto", "cpu"])
        self.threads = QSpinBox(); self.threads.setRange(0, 128); self.threads.setValue(0)
        self.system_prompt = QPlainTextEdit(); self.system_prompt.setMaximumHeight(70)
        settings.addRow("temperature", self.temperature)
        settings.addRow("top_p", self.top_p)
        settings.addRow("max_new_tokens", self.max_tokens)
        settings.addRow("repetition penalty", self.rep_penalty)
        settings.addRow("seed", self.seed)
        settings.addRow("context tokens", self.ctx_tokens)
        settings.addRow("device", self.device)
        settings.addRow("threads", self.threads)
        settings.addRow("system prompt", self.system_prompt)
        root.addLayout(settings)

        self.history = QListWidget()
        root.addWidget(self.history, 1)
        self.prompt = QTextEdit(); self.prompt.setPlaceholderText("Prompt")
        root.addWidget(self.prompt)
        row = QHBoxLayout()
        self.send_btn = QPushButton("Send")
        self.stop_btn = QPushButton("Stop")
        self.reset_btn = QPushButton("Reset defaults")
        row.addWidget(self.send_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.reset_btn)
        row.addStretch(1)
        root.addLayout(row)

        self.refresh_models_btn.clicked.connect(self._refresh_models)
        self.send_btn.clicked.connect(self._send)
        self.stop_btn.clicked.connect(self._stop)
        self.reset_btn.clicked.connect(self._reset_defaults)
        self._svc.chunk.connect(self._on_chunk)
        self._svc.error.connect(self._on_error)
        self._svc.finished.connect(self._on_finished)

        self._load_settings()
        self._refresh_models()

    def _refresh_models(self) -> None:
        self._models = self._repo.list_models()
        self.model_combo.clear()
        for m in self._models:
            self.model_combo.addItem(f"{m.name} ({m.training_run_id or 'run'})", m.checkpoint_path)
        has = bool(self._models)
        self.no_models.setVisible(not has)
        self.send_btn.setEnabled(has)

    def _generation_cfg(self) -> GenerationConfig:
        return GenerationConfig(
            temperature=float(self.temperature.value()),
            top_p=float(self.top_p.value()),
            max_new_tokens=int(self.max_tokens.value()),
            repetition_penalty=float(self.rep_penalty.value()),
            seed=None if self.seed.value() == 0 else int(self.seed.value()),
            system_prompt=self.system_prompt.toPlainText().strip(),
            context_tokens=int(self.ctx_tokens.value()),
            device=self.device.currentText(),
            threads=int(self.threads.value()),
        )

    def _send(self) -> None:
        prompt = self.prompt.toPlainText().strip()
        if not prompt:
            return
        idx = self.model_combo.currentIndex()
        if idx < 0 or idx >= len(self._models):
            QMessageBox.warning(self, "Inference", "Missing model. Select a local checkpoint.")
            return
        self._save_settings()
        self.history.addItem(f"You: {prompt}")
        self.history.addItem("ENA: ")
        self._assistant_row = self.history.count() - 1
        self._active_handle = self._svc.start(prompt, self._models[idx], self._generation_cfg())
        self.send_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def _stop(self) -> None:
        if self._active_handle:
            self._svc.cancel(self._active_handle)
            self._active_handle = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _on_chunk(self, text: str) -> None:
        if self._assistant_row is None:
            return
        item = self.history.item(self._assistant_row)
        item.setText(item.text() + text)

    def _on_error(self, message: str, details: str) -> None:
        QMessageBox.warning(self, "Inference error", f"{message}\n\n{details}")

    def _on_finished(self, ok: bool, _stats: dict) -> None:
        self._active_handle = None
        self.send_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if not ok and self._assistant_row is not None:
            item = self.history.item(self._assistant_row)
            item.setText(item.text() + " [stopped]")

    def _settings_bucket(self) -> dict:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        infer = ena.get("inference") if isinstance(ena.get("inference"), dict) else {}
        return infer

    def _load_settings(self) -> None:
        inf = self._settings_bucket()
        self.temperature.setValue(float(inf.get("temperature", 0.7)))
        self.top_p.setValue(float(inf.get("top_p", 0.95)))
        self.max_tokens.setValue(int(inf.get("max_new_tokens", 128)))
        self.rep_penalty.setValue(float(inf.get("repetition_penalty", 1.0)))
        self.system_prompt.setPlainText(str(inf.get("system_prompt", "")))
        self.threads.setValue(int(inf.get("threads", 0)))
        self.device.setCurrentText(str(inf.get("device", "auto")))

    def _save_settings(self) -> None:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        ena["inference"] = {
            "temperature": self.temperature.value(),
            "top_p": self.top_p.value(),
            "max_new_tokens": self.max_tokens.value(),
            "repetition_penalty": self.rep_penalty.value(),
            "system_prompt": self.system_prompt.toPlainText(),
            "threads": self.threads.value(),
            "device": self.device.currentText(),
            "last_selected_model": self.model_combo.currentData(),
        }
        self._cfg.ena = ena
        save_config(self._cfg)

    def _reset_defaults(self) -> None:
        self.temperature.setValue(0.7)
        self.top_p.setValue(0.95)
        self.max_tokens.setValue(128)
        self.rep_penalty.setValue(1.0)
        self.system_prompt.clear()
        self.device.setCurrentText("auto")
        self.threads.setValue(0)
        self._save_settings()
