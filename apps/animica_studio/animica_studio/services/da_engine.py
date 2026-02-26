from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.services.da_client import DaClient
from animica_studio.services.da_dir_usage_service import DaDirUsageService
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
    host_directory: str = ""
    node_directory: str = ""
    limit_bytes: int = 0
    used_bytes: int = 0
    remaining_bytes: int = 0
    disk_used_bytes: int = 0
    disk_total_bytes: int = 0
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
    host_data_dir: str = ""
    node_data_dir: str = ""
    mode: str = "quota"
    limit_bytes: int = 50 * 1024**3
    rpc_url: str = ""
    contributor_id: str = ""
    auto_start: bool = True
    allowed_base_dirs: list[str] | None = None


class DaContributionEngine(QObject):
    stateChanged = Signal(str)
    healthChanged = Signal(bool, str)
    metricsUpdated = Signal(object)
    logLine = Signal(str, str)

    def __init__(self, config: DaEngineConfig) -> None:
        super().__init__()
        self._path_warning = ""
        config = self._normalize_data_dirs(config)
        self.config = config
        self.state = DaEngineState.DISABLED if not config.enabled else DaEngineState.CONFIGURED
        self.metrics = DaMetrics(
            host_directory=config.host_data_dir,
            node_directory=config.node_data_dir,
            limit_bytes=config.limit_bytes,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(4000)
        self._timer.timeout.connect(self._tick)
        self._busy_worker: WorkerThread | None = None
        self._dir_usage = DaDirUsageService(cache_ttl_seconds=5.0, scan_time_budget_seconds=2.5)
        self._known_uploaded: set[str] = set()
        self._last_uploaded_bytes = 0
        self._autostart_attempted = False
        self._last_state_transition_ts = self._utc_now()
        self._start_attempts = 0
        self._config_validation_reasons: list[str] = []
        self._last_applied_node_dir = ""
        self._last_applied_limit_bytes = 0
        self._last_applied_mode = ""
        self._start_in_progress = False

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

    @staticmethod
    def _derive_node_dir(host_dir: Path) -> str:
        compose = Path.home() / "animica" / "ops" / "docker" / "docker-compose.mainnet.yml"
        try:
            if compose.exists():
                text = compose.read_text(encoding="utf-8")
                if re.search(r"\n\s*-\s*[^\n:]+:/data\b", text):
                    return "/data/da"
        except Exception:
            pass
        try:
            parts = host_dir.expanduser().parts
        except Exception:
            return "/data/da"
        if "chain-" in "".join(parts):
            return "/data/da"
        return "/data/da"

    def _normalize_data_dirs(self, cfg: DaEngineConfig) -> DaEngineConfig:
        """Normalize host/node DA paths, preserving backwards-compatible behavior."""
        host_selected = Path((cfg.host_data_dir or "").strip() or os.path.expanduser("~/animica-da")).expanduser()
        node_selected = (cfg.node_data_dir or "").strip() or self._derive_node_dir(host_selected)
        return DaEngineConfig(
            enabled=cfg.enabled,
            host_data_dir=str(host_selected),
            node_data_dir=node_selected,
            mode=cfg.mode,
            limit_bytes=cfg.limit_bytes,
            rpc_url=cfg.rpc_url,
            contributor_id=cfg.contributor_id,
            auto_start=cfg.auto_start,
            allowed_base_dirs=cfg.allowed_base_dirs,
        )

    def client(self) -> DaClient:
        return DaClient(self.config.rpc_url)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _transition_to(self, new_state: DaEngineState, reason: str) -> None:
        old = self.state
        self.state = new_state
        self._last_state_transition_ts = self._utc_now()
        log.info("DA state: %s -> %s (%s)", old.value, new_state.value, reason)
        self.stateChanged.emit(self.state.value)

    @staticmethod
    def _validate_rpc_url(rpc_url: str) -> str | None:
        parsed = urlparse((rpc_url or "").strip())
        if parsed.scheme not in {"http", "https"}:
            return "RPC URL must start with http:// or https://"
        if not parsed.netloc:
            return "RPC URL must include host"
        return None

    def config_validation_details(self, cfg: DaEngineConfig) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not cfg.host_data_dir:
            reasons.append("Host directory is required")
        if not cfg.node_data_dir:
            reasons.append("Node directory is required")
        else:
            allowed_base_dirs = [str(v).rstrip("/") for v in list(cfg.allowed_base_dirs or []) if str(v).strip()]
            if allowed_base_dirs:
                node_dir = cfg.node_data_dir.rstrip("/")
                if not any(node_dir == base or node_dir.startswith(f"{base}/") for base in allowed_base_dirs):
                    reasons.append(
                        "Selected node dir must be under one of the node's allowed base dirs: "
                        + ", ".join(allowed_base_dirs)
                    )
        if cfg.limit_bytes <= 0:
            reasons.append("Limit must be greater than 0")
        rpc_err = self._validate_rpc_url(cfg.rpc_url)
        if rpc_err:
            reasons.append(rpc_err)
        p = Path(cfg.host_data_dir).expanduser()
        if cfg.host_data_dir:
            writable, detail = self._is_writable_dir(p)
            if not writable:
                reasons.append(f"Host directory not writable: {detail}")
        return (len(reasons) == 0), reasons

    def ensure_enabled_if_autostart(self) -> bool:
        valid, reasons = self.config_validation_details(self.config)
        self._config_validation_reasons = reasons
        if self.config.auto_start and valid and not self.config.enabled:
            self.config.enabled = True
            self.metrics.last_error = ""
            self._transition_to(DaEngineState.CONFIGURED, "auto-enable for auto_start")
            self.healthChanged.emit(True, "Configured")
            self.logLine.emit("system", "DA auto-enabled due to auto_start")
            return True
        return False

    def autostart_if_configured(self) -> None:
        if self._autostart_attempted:
            return
        self._autostart_attempted = True
        self.ensure_enabled_if_autostart()
        valid, reasons = self.config_validation_details(self.config)
        self._config_validation_reasons = reasons
        if self.config.auto_start and self.config.enabled and valid and self.state != DaEngineState.RUNNING:
            self.start()

    def apply_config(self, config: DaEngineConfig) -> tuple[bool, str]:
        config = self._normalize_data_dirs(config)
        ok, detail = self.validate_config(config)
        if not ok:
            self._set_error(detail)
            return False, detail
        config_changed = (
            self.config.enabled != config.enabled
            or self.config.host_data_dir != config.host_data_dir
            or self.config.node_data_dir != config.node_data_dir
            or self.config.mode != config.mode
            or self.config.limit_bytes != config.limit_bytes
            or self.config.rpc_url != config.rpc_url
            or self.config.contributor_id != config.contributor_id
            or self.config.auto_start != config.auto_start
            or (self.config.allowed_base_dirs or []) != (config.allowed_base_dirs or [])
        )
        if not config_changed:
            return True, "unchanged"
        self.config = config
        self.metrics.host_directory = config.host_data_dir
        self.metrics.node_directory = config.node_data_dir
        self.metrics.limit_bytes = config.limit_bytes
        self.metrics.last_error = ""
        if config.enabled:
            self._transition_to(DaEngineState.CONFIGURED, "apply")
            self.healthChanged.emit(True, "Configured")
        else:
            self._transition_to(DaEngineState.DISABLED, "apply (toggle off)")
            self.healthChanged.emit(True, "Disabled (toggle off)")
        self.logLine.emit(
            "system",
            f"Applied DA config host_dir={config.host_data_dir} node_dir={config.node_data_dir} limit={config.limit_bytes}",
        )
        if self._path_warning:
            self.logLine.emit("warn", self._path_warning)
        self._update_local_metrics()
        return True, "ok"

    def validate_config(self, cfg: DaEngineConfig) -> tuple[bool, str]:
        ok, reasons = self.config_validation_details(cfg)
        self._config_validation_reasons = reasons
        if ok:
            return True, "ok"
        return False, "; ".join(reasons)

    def start(self) -> None:
        if self.state == DaEngineState.RUNNING or self._start_in_progress:
            return
        self._start_in_progress = True
        self._start_attempts += 1
        self.logLine.emit("system", f"DA start requested (attempt={self._start_attempts})")
        try:
            ok, detail = self.validate_config(self.config)
            if not ok:
                self._set_error(detail)
                return
            if not self.config.enabled:
                self.config.enabled = True
                self._transition_to(DaEngineState.CONFIGURED, "start enabled DA feature")
            self._transition_to(DaEngineState.STARTING, "start")
            desired_on_full = "evict" if self.config.mode == "quota" else "reject"
            should_configure = (
                self._last_applied_node_dir != self.config.node_data_dir
                or self._last_applied_limit_bytes != self.config.limit_bytes
                or self._last_applied_mode != desired_on_full
            )
            if should_configure:
                self.client().configure(
                    {
                        "enabled": True,
                        "dir": self.config.node_data_dir,
                        "max_bytes": self.config.limit_bytes,
                        "on_full": desired_on_full,
                    }
                )
                self._last_applied_node_dir = self.config.node_data_dir
                self._last_applied_limit_bytes = self.config.limit_bytes
                self._last_applied_mode = desired_on_full
            self._timer.start()
            self._transition_to(DaEngineState.RUNNING, "start success")
            self.healthChanged.emit(True, "Running")
            self.logLine.emit("system", "DA contribution engine running")
            if self._path_warning:
                self.logLine.emit("warn", self._path_warning)
            self._tick()
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            self._start_in_progress = False

    def stop(self) -> None:
        if self.state not in {DaEngineState.RUNNING, DaEngineState.STARTING, DaEngineState.ERROR}:
            return
        self._transition_to(DaEngineState.STOPPING, "stop")
        self._timer.stop()
        if self._busy_worker and self._busy_worker.isRunning():
            self._busy_worker.quit()
            self._busy_worker.wait(1000)
        self._transition_to(DaEngineState.CONFIGURED, "stop complete")
        self._start_in_progress = False
        self.healthChanged.emit(True, "Stopped")
        self.logLine.emit("system", "DA contribution engine stopped")

    def _update_local_metrics(self) -> None:
        try:
            snap = self._dir_usage.get_snapshot(self.config.host_data_dir)
            used = int(snap.used_bytes)
            self.metrics.used_bytes = used
            self.metrics.disk_used_bytes = int(snap.disk_used_bytes)
            self.metrics.disk_total_bytes = int(snap.disk_total_bytes)
            if self.config.mode == "quota":
                self.metrics.remaining_bytes = max(int(self.config.limit_bytes - used), 0)
            else:
                self.metrics.remaining_bytes = 0
            self.metrics.last_error = snap.warning
            self.metricsUpdated.emit(self.metrics)
        except Exception as exc:
            self._set_error(str(exc))

    def _tick(self) -> None:
        if self.state != DaEngineState.RUNNING or (self._busy_worker and self._busy_worker.isRunning()):
            return
        self._busy_worker = WorkerThread(lambda: self._run_cycle())
        self._busy_worker.worker.result.connect(self._on_cycle)
        self._busy_worker.worker.error.connect(lambda m, _tb: self._set_error(m))
        self._busy_worker.start()

    def _run_cycle(self) -> dict[str, Any]:
        p = Path(self.config.host_data_dir)
        if not p.exists() or not os.access(p, os.R_OK):
            raise RuntimeError(f"Contribution directory unreadable: {p}")
        files = [f for f in p.glob("**/*") if f.is_file() and not f.name.startswith(".")]
        queued = [f for f in files if str(f) not in self._known_uploaded]
        uploaded = []
        for f in queued[:3]:
            data = f.read_bytes()
            res = self.client().upload_bytes(data)
            self._known_uploaded.add(str(f))
            uploaded.append({"file": str(f), "blob_id": res["blob_id"], "size": len(data)})
        snap = self._dir_usage.get_snapshot(str(p))
        return {
            "queued": len(queued),
            "uploaded": uploaded,
            "da_used": int(snap.used_bytes),
            "disk_used": int(snap.disk_used_bytes),
            "disk_total": int(snap.disk_total_bytes),
            "scan_warning": snap.warning,
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
        self.metrics.used_bytes = int(out.get("da_used", 0))
        self.metrics.disk_used_bytes = int(out.get("disk_used", 0))
        self.metrics.disk_total_bytes = int(out.get("disk_total", 0))
        if self.config.mode == "quota":
            self.metrics.remaining_bytes = max(int(self.config.limit_bytes - self.metrics.used_bytes), 0)
        else:
            self.metrics.remaining_bytes = 0
        scan_warning = str(out.get("scan_warning", "") or "")
        if scan_warning:
            self.metrics.last_error = scan_warning
        status = out.get("status") or {}
        if isinstance(status, dict) and status.get("last_error"):
            self.metrics.last_error = str(status.get("last_error"))
            self.metrics.failure_count += 1
            self.healthChanged.emit(False, self.metrics.last_error)
        else:
            self.healthChanged.emit(True, "Healthy")
        self.metricsUpdated.emit(self.metrics)

    def _set_error(self, detail: str) -> None:
        self._transition_to(DaEngineState.ERROR, "error")
        self.metrics.last_error = detail
        self.healthChanged.emit(False, detail)
        self.logLine.emit("error", detail)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "config": self.config.__dict__,
            "metrics": self.metrics.__dict__,
            "config_valid": len(self._config_validation_reasons) == 0,
            "config_validation_reasons": self._config_validation_reasons,
            "last_state_transition_ts": self._last_state_transition_ts,
            "start_attempts": self._start_attempts,
        }
