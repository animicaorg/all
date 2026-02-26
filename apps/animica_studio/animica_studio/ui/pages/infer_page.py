from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_studio.ena_mm.infer.chat import generate_text
from animica_studio.ena_mm.infer.decoding.render import save_mp4_placeholder, save_png
from animica_studio.ena_mm.infer.image_gen import generate_image
from animica_studio.ena_mm.infer.safety_filters import validate_prompt
from animica_studio.ena_mm.infer.video_gen import generate_video_frames
from animica_studio.services.ena_model_repository import EnaModelRepository, ModelEntry
from animica_studio.storage.config import Config, save_config


class InferPage(QWidget):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Use ENA (Inference)")
        self._cfg = config
        self._repo = EnaModelRepository()
        self._models: list[ModelEntry] = []
        self._turns: list[dict[str, str]] = []

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.model_combo = QComboBox()
        self.refresh_models_btn = QPushButton("Refresh models")
        top.addWidget(QLabel("Local ENA-MM checkpoint package"))
        top.addWidget(self.model_combo, 1)
        top.addWidget(self.refresh_models_btn)
        root.addLayout(top)

        self.no_models = QLabel("No local models found. Train a model in the Training page first.")
        root.addWidget(self.no_models)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_chat_tab(), "Chat")
        self.tabs.addTab(self._build_image_tab(), "Image")
        self.tabs.addTab(self._build_video_tab(), "Video")

        self.refresh_models_btn.clicked.connect(self._refresh_models)
        self._load_settings()
        self._refresh_models()

    def _build_chat_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        settings = QFormLayout()
        self.use_context = QCheckBox("Use conversation context")
        self.use_context.setChecked(True)
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0.0, 2.0); self.temperature.setValue(0.7)
        self.top_p = QDoubleSpinBox(); self.top_p.setRange(0.0, 1.0); self.top_p.setValue(0.95)
        self.max_tokens = QSpinBox(); self.max_tokens.setRange(1, 8192); self.max_tokens.setValue(128)
        settings.addRow("", self.use_context)
        settings.addRow("temperature", self.temperature)
        settings.addRow("top_p", self.top_p)
        settings.addRow("max_new_tokens", self.max_tokens)
        root.addLayout(settings)
        self.history = QListWidget()
        root.addWidget(self.history, 1)
        self.prompt = QTextEdit(); self.prompt.setPlaceholderText("Type a prompt for ENA-MM chat")
        self.prompt.setMaximumHeight(120)
        root.addWidget(self.prompt)
        row = QHBoxLayout()
        self.send_btn = QPushButton("Send")
        self.clear_btn = QPushButton("Clear chat")
        self.send_btn.clicked.connect(self._send_chat)
        self.clear_btn.clicked.connect(self._clear_chat)
        row.addWidget(self.send_btn); row.addWidget(self.clear_btn); row.addStretch(1)
        root.addLayout(row)
        return w

    def _build_image_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        form = QFormLayout()
        self.image_prompt = QTextEdit(); self.image_prompt.setMaximumHeight(90)
        self.image_steps = QSpinBox(); self.image_steps.setRange(1, 200); self.image_steps.setValue(20)
        self.image_guidance = QDoubleSpinBox(); self.image_guidance.setRange(0.0, 20.0); self.image_guidance.setValue(7.5)
        self.image_seed = QSpinBox(); self.image_seed.setRange(0, 2**31 - 1); self.image_seed.setValue(42)
        self.image_size = QComboBox(); self.image_size.addItems(["64x64", "128x128", "256x256"])
        self.image_sampler = QComboBox(); self.image_sampler.addItems(["ddim", "euler"])
        form.addRow("Prompt", self.image_prompt)
        form.addRow("Size", self.image_size)
        form.addRow("Steps", self.image_steps)
        form.addRow("Guidance", self.image_guidance)
        form.addRow("Seed", self.image_seed)
        form.addRow("Sampler", self.image_sampler)
        root.addLayout(form)
        self.image_out = QLabel("No image generated yet")
        self.image_gen_btn = QPushButton("Generate image")
        self.image_gen_btn.clicked.connect(self._generate_image)
        root.addWidget(self.image_gen_btn)
        root.addWidget(self.image_out)
        root.addStretch(1)
        return w

    def _build_video_tab(self) -> QWidget:
        w = QWidget()
        root = QVBoxLayout(w)
        form = QFormLayout()
        self.video_prompt = QTextEdit(); self.video_prompt.setMaximumHeight(90)
        self.video_duration = QSpinBox(); self.video_duration.setRange(1, 8); self.video_duration.setValue(2)
        self.video_fps = QSpinBox(); self.video_fps.setRange(4, 24); self.video_fps.setValue(8)
        self.video_res = QComboBox(); self.video_res.addItems(["64x64", "128x128"])
        self.video_steps = QSpinBox(); self.video_steps.setRange(1, 100); self.video_steps.setValue(12)
        self.video_seed = QSpinBox(); self.video_seed.setRange(0, 2**31 - 1); self.video_seed.setValue(99)
        form.addRow("Prompt", self.video_prompt)
        form.addRow("Duration (s)", self.video_duration)
        form.addRow("FPS", self.video_fps)
        form.addRow("Resolution", self.video_res)
        form.addRow("Diffusion steps", self.video_steps)
        form.addRow("Seed", self.video_seed)
        root.addLayout(form)
        self.video_notice = QLabel("GPU recommended for video generation. CPU may run tiny clips only.")
        self.video_out = QLabel("No video generated yet")
        self.video_btn = QPushButton("Generate video")
        self.video_btn.clicked.connect(self._generate_video)
        root.addWidget(self.video_notice)
        root.addWidget(self.video_btn)
        root.addWidget(self.video_out)
        root.addStretch(1)
        return w

    def _refresh_models(self) -> None:
        self._models = self._repo.list_models()
        self.model_combo.clear()
        for m in self._models:
            flags = m.modality_flags or {"text": True}
            self.model_combo.addItem(f"{m.name} [{','.join(k for k,v in flags.items() if v)}]", m.checkpoint_path)
        has = bool(self._models)
        self.no_models.setVisible(not has)

    def _selected_model(self) -> ModelEntry | None:
        idx = self.model_combo.currentIndex()
        if idx < 0 or idx >= len(self._models):
            return None
        return self._models[idx]

    def _send_chat(self) -> None:
        model = self._selected_model()
        if not model:
            QMessageBox.warning(self, "Inference", "Select a local checkpoint.")
            return
        prompt = self.prompt.toPlainText().strip()
        ok, reason = validate_prompt(prompt)
        if not ok:
            QMessageBox.warning(self, "Safety", reason)
            return
        self.history.addItem(f"You: {prompt}")
        answer = generate_text(prompt, self._turns if self.use_context.isChecked() else None)
        self.history.addItem(answer)
        self._turns.append({"user": prompt, "assistant": answer})
        self.prompt.clear()
        self._save_settings()

    def _generate_image(self) -> None:
        prompt = self.image_prompt.toPlainText().strip()
        ok, reason = validate_prompt(prompt)
        if not ok:
            QMessageBox.warning(self, "Safety", reason)
            return
        w, h = [int(v) for v in self.image_size.currentText().split("x")]
        img = generate_image(prompt, w, h, int(self.image_seed.value()))
        out = Path("./ena-training-runs/mm-infer") / f"img-{int(time.time())}.png"
        path = save_png(img, str(out))
        self.image_out.setText(f"Saved image: {path}")
        self._save_settings()

    def _generate_video(self) -> None:
        prompt = self.video_prompt.toPlainText().strip()
        ok, reason = validate_prompt(prompt)
        if not ok:
            QMessageBox.warning(self, "Safety", reason)
            return
        w, h = [int(v) for v in self.video_res.currentText().split("x")]
        frames = int(self.video_duration.value()) * int(self.video_fps.value())
        model = self._selected_model()
        flags = model.modality_flags if model else {}
        if flags and not flags.get("video", False):
            QMessageBox.information(self, "Video", "Selected model package does not include video head.")
            return
        imgs = generate_video_frames(prompt, w, h, max(1, frames), int(self.video_seed.value()))
        out = Path("./ena-training-runs/mm-infer") / f"vid-{int(time.time())}.mp4"
        path = save_mp4_placeholder(imgs, str(out))
        self.video_out.setText(f"Saved video: {path}")
        self._save_settings()

    def _clear_chat(self) -> None:
        self.history.clear()
        self._turns = []

    def _settings_bucket(self) -> dict:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        return ena.get("inference") if isinstance(ena.get("inference"), dict) else {}

    def _load_settings(self) -> None:
        inf = self._settings_bucket()
        self.temperature.setValue(float(inf.get("temperature", 0.7)))
        self.top_p.setValue(float(inf.get("top_p", 0.95)))
        self.max_tokens.setValue(int(inf.get("max_new_tokens", 128)))

    def _save_settings(self) -> None:
        ena = self._cfg.ena if isinstance(self._cfg.ena, dict) else {}
        ena["inference"] = {
            "temperature": self.temperature.value(),
            "top_p": self.top_p.value(),
            "max_new_tokens": self.max_tokens.value(),
            "last_selected_model": self.model_combo.currentData(),
        }
        self._cfg.ena = ena
        save_config(self._cfg)
