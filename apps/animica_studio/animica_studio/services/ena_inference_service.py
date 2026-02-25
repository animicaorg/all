from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from animica_studio.services.ena_model_repository import ModelEntry


@dataclass(slots=True)
class GenerationConfig:
    temperature: float = 0.7
    top_p: float = 0.95
    max_new_tokens: int = 128
    repetition_penalty: float = 1.0
    seed: int | None = None
    system_prompt: str = ""
    context_tokens: int = 2048
    device: str = "auto"
    threads: int = 0


class _InferenceWorker(QThread):
    chunk = Signal(str)
    completed = Signal(dict)
    failed = Signal(str, str)

    def __init__(self, prompt: str, model: ModelEntry, cfg: GenerationConfig, cancel_event: threading.Event) -> None:
        super().__init__()
        self._prompt = prompt
        self._model = model
        self._cfg = cfg
        self._cancel = cancel_event

    def run(self) -> None:
        try:
            from ena.inference import create_inference_engine

            engine = create_inference_engine(self._model.checkpoint_path, self._model.name)
            joined = f"{self._cfg.system_prompt}\n\n{self._prompt}".strip()
            result = engine.infer(joined, max_tokens=self._cfg.max_new_tokens, temperature=self._cfg.temperature)
            text = str(result.get("answer") or "")
            assembled = ""
            for token in text.split(" "):
                if self._cancel.is_set():
                    self.completed.emit({"cancelled": True})
                    return
                piece = token + " "
                assembled += piece
                self.chunk.emit(piece)
                self.msleep(20)
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            self.completed.emit({"cancelled": False, "usage": usage, "text": assembled.strip()})
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc), repr(exc))


class EnaInferenceService(QObject):
    chunk = Signal(str)
    finished = Signal(bool, dict)
    error = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._workers: dict[str, tuple[_InferenceWorker, threading.Event]] = {}

    def start(self, prompt: str, model_entry: ModelEntry, generation_config: GenerationConfig) -> str:
        handle = f"infer-{uuid.uuid4().hex[:12]}"
        cancel_event = threading.Event()
        worker = _InferenceWorker(prompt, model_entry, generation_config, cancel_event)
        worker.chunk.connect(self.chunk.emit)
        worker.completed.connect(lambda stats, h=handle: self._on_done(h, stats))
        worker.failed.connect(lambda msg, details, h=handle: self._on_error(h, msg, details))
        self._workers[handle] = (worker, cancel_event)
        worker.start()
        return handle

    def cancel(self, handle: str) -> None:
        entry = self._workers.get(handle)
        if not entry:
            return
        worker, cancel_event = entry
        cancel_event.set()
        if worker.isRunning():
            worker.quit()
            worker.wait(500)

    def _on_done(self, handle: str, stats: dict[str, Any]) -> None:
        self._workers.pop(handle, None)
        self.finished.emit(not bool(stats.get("cancelled")), stats)

    def _on_error(self, handle: str, message: str, details: str) -> None:
        self._workers.pop(handle, None)
        self.error.emit(message, details)
        self.finished.emit(False, {"error": message})
