from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from threading import Event
from time import time
from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from animica_studio.models.bootstrap_run import BootstrapRun
from animica_studio.services.dataset_bootstrap_service import BootstrapOptions, DatasetBootstrapService as BootstrapEngine
from animica_studio.storage.config import load_config
from animica_studio.util.paths import app_data_dir


class BootstrapRunStore:
    def __init__(self) -> None:
        self._path = app_data_dir() / "datasets" / "bootstrap_runs.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, runs: list[BootstrapRun]) -> None:
        self._path.write_text(json.dumps([r.to_dict() for r in runs], indent=2), encoding="utf-8")

    def load(self) -> list[BootstrapRun]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return [BootstrapRun.from_dict(item) for item in raw if isinstance(item, dict)]


class _BootstrapWorker(QThread):
    progress = Signal(dict)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, options: BootstrapOptions, source_settings: dict[str, Any], output_dir: str) -> None:
        super().__init__()
        self._cancel = Event()
        self._options = options
        self._source_settings = source_settings
        self._output_dir = output_dir

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            svc = BootstrapEngine(source_settings=self._source_settings)
            result = svc.bootstrap(self._options, progress_cb=lambda payload: self.progress.emit(payload), cancel=self._cancel)
            self.finished_ok.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class DatasetBootstrapRuntime(QObject):
    stateChanged = Signal(str)
    progressUpdated = Signal(dict)
    logLine = Signal(str, str)
    finished = Signal(bool, dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = BootstrapRunStore()
        self._runs: list[BootstrapRun] = self._store.load()
        self._active_run: BootstrapRun | None = self._runs[0] if self._runs else None
        self._worker: _BootstrapWorker | None = None
        self._pause_requested = False
        self._persist_timer = QTimer(self)
        self._persist_timer.setInterval(1000)
        self._persist_timer.timeout.connect(self._persist)
        self._persist_timer.start()

    @property
    def active_run(self) -> BootstrapRun | None:
        return self._active_run

    def start(self, *, name: str, size_preset: str, output_dir: str | None = None) -> BootstrapRun:
        run = BootstrapRun(
            run_id=f"bootstrap-{uuid.uuid4().hex[:12]}",
            state="DOWNLOADING",
            target_size=size_preset,
            output_dir=output_dir or str(app_data_dir() / "datasets" / f"bootstrap-{name}"),
            source_providers=["wikipedia", "arxiv", "gutenberg", "vetted_repos"],
        )
        self._active_run = run
        self._runs.insert(0, run)
        self._start_worker(run, name=name)
        self._emit_state(run.state)
        self._persist()
        return run

    def retry(self, *, name: str, size_preset: str) -> BootstrapRun:
        return self.start(name=name, size_preset=size_preset)

    def pause(self) -> None:
        if not self._worker:
            return
        self._pause_requested = True
        self.cancel()

    def resume(self, *, name: str) -> None:
        run = self._active_run
        if run is None or run.state not in {"CANCELED", "ERROR", "PAUSED"}:
            return
        run.paused = False
        run.state = "DOWNLOADING"
        run.last_error = ""
        self._start_worker(run, name=name)
        self._emit_state(run.state)

    def cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        if self._active_run:
            self._active_run.state = "CANCELED"
            self._active_run.updated_at = time()
            self._emit_state("CANCELED")
            self._persist()

    def _start_worker(self, run: BootstrapRun, *, name: str) -> None:
        if self._worker and self._worker.isRunning():
            return
        ena = load_config().ena
        source_settings = ena.get("dataset_sources") if isinstance(ena, dict) else {}
        options = BootstrapOptions(name=name, size_preset=run.target_size, output_dir=Path(run.output_dir))
        self._worker = _BootstrapWorker(options, source_settings if isinstance(source_settings, dict) else {}, run.output_dir)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, payload: dict[str, Any]) -> None:
        run = self._active_run
        if run is None:
            return
        stage = str(payload.get("stage") or "").upper()
        state_map = {
            "DOWNLOADING": "DOWNLOADING",
            "EXTRACTING": "EXTRACTING",
            "PROCESSING": "PROCESSING",
            "SHARDING": "SHARDING",
            "DONE": "DONE",
            "PROVIDER_FAILED": "ERROR",
            "CACHED": "DOWNLOADING",
        }
        run.state = state_map.get(stage, run.state)
        run.bytes_downloaded = max(run.bytes_downloaded, int(payload.get("downloaded_bytes") or 0))
        if payload.get("download_total_bytes"):
            run.bytes_total = int(payload.get("download_total_bytes") or 0)
        if payload.get("target_bytes"):
            run.docs_total = int(payload.get("target_bytes") or 0)
        run.bytes_processed = int(payload.get("processed_bytes") or run.bytes_processed)
        run.docs_processed = int(payload.get("doc_count") or run.docs_processed)
        run.shards_count = int(payload.get("shards") or run.shards_count)
        run.output_bytes = run.bytes_processed
        run.updated_at = time()
        line_kind = "system"
        if stage == "PROVIDER_FAILED":
            line_kind = "error"
            run.last_error = str(payload.get("error") or "provider failure")
        text = json.dumps(payload, ensure_ascii=False)
        self._append_log(run, line_kind, text)
        self._emit_state(run.state)
        self.progressUpdated.emit(payload)

    def _on_finished(self, result: dict[str, Any]) -> None:
        run = self._active_run
        if run is None:
            return
        if self._pause_requested:
            self._pause_requested = False
            run.state = "PAUSED"
            run.paused = True
        elif result.get("cancelled"):
            run.state = "CANCELED"
        else:
            run.state = "DONE"
        run.updated_at = time()
        run.diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {"entries": result.get("diagnostics", [])}
        self._append_log(run, "system", f"finished: state={run.state}")
        self._emit_state(run.state)
        self.finished.emit(run.state == "DONE", result)
        self._persist()

    def _on_failed(self, err: str) -> None:
        run = self._active_run
        if run is None:
            return
        run.state = "ERROR"
        run.last_error = err
        run.updated_at = time()
        self._append_log(run, "error", err)
        self._emit_state("ERROR")
        self.finished.emit(False, {"error": err})
        self._persist()

    def diagnostics_text(self) -> str:
        run = self._active_run
        if run is None:
            return "No bootstrap run."
        disk = shutil.disk_usage(run.output_dir if Path(run.output_dir).exists() else str(Path.home()))
        recent = run.log_lines[-50:]
        payload = {
            "run_id": run.run_id,
            "state": run.state,
            "last_error": run.last_error,
            "output_dir": run.output_dir,
            "disk_free": disk.free,
            "urls_attempted": run.diagnostics.get("entries", []),
            "recent_logs": recent,
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _append_log(self, run: BootstrapRun, kind: str, text: str) -> None:
        run.log_lines.append({"kind": kind, "text": text})
        if len(run.log_lines) > 500:
            run.log_lines = run.log_lines[-500:]
        self.logLine.emit(kind, text)

    def _emit_state(self, state: str) -> None:
        self.stateChanged.emit(state)

    def _persist(self) -> None:
        if self._active_run:
            self._active_run.updated_at = time()
        self._store.save(self._runs[:20])


_RUNTIME: DatasetBootstrapRuntime | None = None


def bootstrap_runtime() -> DatasetBootstrapRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = DatasetBootstrapRuntime()
    return _RUNTIME
