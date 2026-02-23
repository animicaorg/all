from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.services.da_client import DaClient
from animica_studio.services.workers import WorkerThread

log = logging.getLogger(__name__)


"""DAInterfaceSpec
Canonical DA interfaces used by Studio:
- RPC upload: da_putBlob / da.putBlob with params [{"data": <base64>, "namespace"?: str}] -> commitment/blob_id string.
- RPC retrieval: da_getBlob / da.getBlob with params [commitment] -> {"data": <base64>} or raw.
- RPC status/config: da.status({}), da.configure({...}), da.list({limit,order}), da.has({blob_id}), da.storage.register(...), da.storage.heartbeat(...).
- CLI fallback: `animica da submit|put|get|verify|status|configure|storage register|storage heartbeat`.
Identifiers:
- commitment/blob_id (typically 0x-hex string); Studio stores this as `blob_id`.
Verification:
- fetch by blob_id via get_blob, decode bytes, compare SHA256 with upload-time hash.
"""


class DaEngineState(str, Enum):
    DISABLED = "disabled"
    CONFIGURED = "configured"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class DaMetrics:
    directory: str = ""
    limit_bytes: int = 0
    used_bytes: int = 0
    remaining_bytes: int = 0
    queued_files: int = 0
    uploaded_blobs: int = 0
    success_count: int = 0
    failure_count: int = 0
    upload_rate_bps: float = 0.0
    last_upload_time: float = 0.0
    last_error: str = ""


@dataclass
class DaEngineConfig:
    enabled: bool = False
    data_dir: str = ""
    mode: str = "quota"
    limit_bytes: int = 50 * 1024**3
    rpc_url: str = ""
    contributor_id: str = ""
    auto_start: bool = True


class DaContributionEngine(QObject):
    stateChanged = Signal(str)
    healthChanged = Signal(bool, str)
    metricsUpdated = Signal(object)
    logLine = Signal(str, str)

    def __init__(self, config: DaEngineConfig) -> None:
        super().__init__()
        self._path_warning = ""
        config = self._normalize_data_dir(config)
        self.config = config
        self.state = DaEngineState.DISABLED if not config.enabled else DaEngineState.CONFIGURED
        self.metrics = DaMetrics(directory=config.data_dir, limit_bytes=config.limit_bytes)
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._tick)
        self._busy_worker: WorkerThread | None = None
        self._known_uploaded: set[str] = set()
        self._last_uploaded_bytes = 0

    @staticmethod
    def _is_writable_dir(path: Path) -> tuple[bool, str]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            test = path / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _normalize_data_dir(self, cfg: DaEngineConfig) -> DaEngineConfig:
        """Normalize DA config paths, but never silently switch to a fallback directory."""
        selected = Path((cfg.data_dir or "").strip() or os.path.expanduser("~/animica-da")).expanduser()
        return DaEngineConfig(
            enabled=cfg.enabled,
            data_dir=str(selected),
            mode=cfg.mode,
            limit_bytes=cfg.limit_bytes,
            rpc_url=cfg.rpc_url,
            contributor_id=cfg.contributor_id,
            auto_start=cfg.auto_start,
        )

    def client(self) -> DaClient:
        return DaClient(self.config.rpc_url)

    def apply_config(self, config: DaEngineConfig) -> tuple[bool, str]:
        config = self._normalize_data_dir(config)
        ok, detail = self.validate_config(config)
        if not ok:
            self._set_error(detail)
            return False, detail
        self.config = config
        self.metrics.directory = config.data_dir
        self.metrics.limit_bytes = config.limit_bytes
        self.state = DaEngineState.CONFIGURED if config.enabled else DaEngineState.DISABLED
        self.stateChanged.emit(self.state.value)
        self.healthChanged.emit(True, "Configured")
        self.logLine.emit("system", f"Applied DA config dir={config.data_dir} limit={config.limit_bytes}")
        if self._path_warning:
            self.logLine.emit("warn", self._path_warning)
        return True, "ok"

    def validate_config(self, cfg: DaEngineConfig) -> tuple[bool, str]:
        p = Path(cfg.data_dir).expanduser()
        if not cfg.enabled:
            return True, "disabled"
        if not cfg.data_dir:
            return False, "Data directory is required"
        if cfg.limit_bytes < 1024**3:
            return False, "Limit must be at least 1 GiB"
        try:
            writable, detail = self._is_writable_dir(p)
            if not writable:
                raise OSError(detail)
        except Exception as exc:
            return False, f"Data directory not writable: {exc}"
        try:
            self.client().status()
        except Exception as exc:
            return False, f"DA endpoint check failed: {exc}"
        return True, "ok"

    def start(self) -> None:
        if self.state == DaEngineState.RUNNING:
            return
        if not self.config.enabled:
            self.state = DaEngineState.DISABLED
            self.stateChanged.emit(self.state.value)
            self.healthChanged.emit(True, "Disabled")
            return
        self.state = DaEngineState.STARTING
        self.stateChanged.emit(self.state.value)
        try:
            self.client().configure(
                {
                    "enabled": True,
                    "dir": self.config.data_dir,
                    "max_bytes": self.config.limit_bytes,
                    "on_full": "evict" if self.config.mode == "quota" else "reject",
                }
            )
            self._timer.start()
            self.state = DaEngineState.RUNNING
            self.stateChanged.emit(self.state.value)
            self.healthChanged.emit(True, "Running")
            self.logLine.emit("system", "DA contribution engine running")
            if self._path_warning:
                self.logLine.emit("warn", self._path_warning)
            self._tick()
        except Exception as exc:
            self._set_error(str(exc))

    def stop(self) -> None:
        if self.state not in {DaEngineState.RUNNING, DaEngineState.STARTING, DaEngineState.ERROR}:
            return
        self.state = DaEngineState.STOPPING
        self.stateChanged.emit(self.state.value)
        self._timer.stop()
        if self._busy_worker and self._busy_worker.isRunning():
            self._busy_worker.quit()
            self._busy_worker.wait(1000)
        self.state = DaEngineState.CONFIGURED
        self.stateChanged.emit(self.state.value)
        self.healthChanged.emit(True, "Stopped")
        self.logLine.emit("system", "DA contribution engine stopped")

    def _tick(self) -> None:
        if self.state != DaEngineState.RUNNING or (self._busy_worker and self._busy_worker.isRunning()):
            return
        self._busy_worker = WorkerThread(lambda: self._run_cycle())
        self._busy_worker.worker.result.connect(self._on_cycle)
        self._busy_worker.worker.error.connect(lambda m, _tb: self._set_error(m))
        self._busy_worker.start()

    def _run_cycle(self) -> dict[str, Any]:
        p = Path(self.config.data_dir)
        files = [f for f in p.glob("**/*") if f.is_file() and not f.name.startswith(".")]
        queued = [f for f in files if str(f) not in self._known_uploaded]
        uploaded = []
        for f in queued[:3]:
            data = f.read_bytes()
            res = self.client().upload_bytes(data)
            self._known_uploaded.add(str(f))
            uploaded.append({"file": str(f), "blob_id": res["blob_id"], "size": len(data)})
        disk = shutil.disk_usage(p)
        return {
            "queued": len(queued),
            "uploaded": uploaded,
            "used": disk.used,
            "free": disk.free,
            "status": self.client().status(),
        }

    def _on_cycle(self, out: dict[str, Any]) -> None:
        self.metrics.queued_files = int(out.get("queued", 0))
        uploaded = out.get("uploaded", [])
        if uploaded:
            now = time.time()
            bytes_up = sum(int(i.get("size", 0)) for i in uploaded)
            self.metrics.uploaded_blobs += len(uploaded)
            self.metrics.success_count += len(uploaded)
            self.metrics.last_upload_time = now
            self.metrics.upload_rate_bps = float(bytes_up)
            for item in uploaded:
                self.logLine.emit("stdout", f"Uploaded {item['file']} -> {item['blob_id']}")
        self.metrics.used_bytes = int(out.get("used", 0))
        self.metrics.remaining_bytes = int(out.get("free", 0))
        status = out.get("status") or {}
        if isinstance(status, dict) and status.get("last_error"):
            self.metrics.last_error = str(status.get("last_error"))
            self.metrics.failure_count += 1
            self.healthChanged.emit(False, self.metrics.last_error)
        else:
            self.healthChanged.emit(True, "Healthy")
        self.metricsUpdated.emit(self.metrics)

    def _set_error(self, detail: str) -> None:
        self.state = DaEngineState.ERROR
        self.metrics.last_error = detail
        self.stateChanged.emit(self.state.value)
        self.healthChanged.emit(False, detail)
        self.logLine.emit("error", detail)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "config": self.config.__dict__,
            "metrics": self.metrics.__dict__,
        }
