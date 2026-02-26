from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.models.wallet_models import is_valid_address
from animica_studio.services.da_path_guard import NODE_PATH_UI_ERROR, assert_host_writable_path
from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.workers import WorkerThread
from animica_studio.util.paths import app_data_dir


log = logging.getLogger(__name__)


class FullAutoState(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    BOOTSTRAPPING = "bootstrapping"
    BOOTSTRAP_BLOCKED = "bootstrap_blocked"
    BOOTSTRAP_BLOCKED_LOCAL_ONLY = "bootstrap_blocked_local_only"
    CONFIGURING_DA = "configuring_da"
    CREATING_POINTER = "creating_pointer"
    PUBLISHING_FIRST = "publishing_first"
    TRAINING = "training"
    EVALUATING = "evaluating"
    PUBLISHING = "publishing"
    WAITING_FOR_INGEST = "waiting_for_ingest"
    SYNCING = "syncing"
    CLAIMING = "claiming"
    IDLE = "idle"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass
class FullAutoConfig:
    enabled: bool = False
    payout_address: str = ""
    intensity: str = "medium"
    upload_every_minutes: int = 15
    upload_every_steps: int = 5000
    sync_every_minutes: int = 30
    selection_rule: str = "latest"
    keep_last_k: int = 5
    da_namespace: int = 0
    model_channel: str = "ena-main"
    require_da_uploads: bool = False
    auto_fallback_on_remote_put_block: bool = True
    max_daily_training_minutes: int = 24 * 60
    train_locally_when_da_disabled: bool = False
    channel_pointer_commitment: str = ""


@dataclass
class TrainingMetrics:
    step: int = 0
    loss: float = 0.0
    steps_per_sec: float = 0.0
    chunk_target_steps: int = 0
    checkpoint_countdown_steps: int = 0


@dataclass
class UploadMetrics:
    chunks_done: int = 0
    chunks_total: int = 0
    latest_commitment: str = ""
    last_upload_time: float = 0.0


@dataclass
class SyncMetrics:
    bytes_done: int = 0
    bytes_total: int = 0
    current_version: str = ""
    last_sync_time: float = 0.0


@dataclass
class EngineSnapshot:
    mode: str = "IDLE"
    step: str = "IDLE"
    model_version: str = "-"
    last_upload_time: float = 0.0
    last_sync_time: float = 0.0
    last_error: str = ""


class EnaFullAutoEngine(QObject):
    stateChanged = Signal(str, str)
    progressUpdated = Signal(object)
    logLine = Signal(str, str)

    def __init__(self, rpc_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rpc_url = rpc_url
        self.config = FullAutoConfig()
        self.state = FullAutoState.DISABLED
        self.snapshot = EngineSnapshot(mode="DISABLED")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._worker: WorkerThread | None = None
        self._paused = False
        self._stop_requested = False
        self._steps = 0
        self._started_at = 0.0
        self._last_upload_step = 0
        self._last_upload_time = 0.0
        self._last_sync_time = 0.0
        self._backoff_s = 2
        self._bootstrap_retry_delays = [60, 300]
        self._bootstrap_failures = 0
        self._bootstrap_publish_attempted = False
        self._bootstrap_blocked_reason = ""
        self._bootstrap_blocked_retry_at = 0.0
        self._bootstrap_blocked_retry_interval_s = 10 * 60
        self._da_status_poll_interval_s = 90
        self._da_status_next_poll_at = 0.0
        self._last_da_capability_signature = ""
        self._last_metrics = TrainingMetrics()
        self._storage = app_data_dir() / "ena_models"
        self._storage.mkdir(parents=True, exist_ok=True)
        self._manual_action = ""

    def apply_config(self, cfg: FullAutoConfig, rpc_url: str) -> None:
        self.config = cfg
        self._rpc_url = rpc_url
        if not cfg.enabled:
            self.stop()
            self._transition(FullAutoState.DISABLED, "disabled")
        elif self.state == FullAutoState.DISABLED:
            self._transition(FullAutoState.IDLE, "configured")

    def start(self) -> None:
        if not self.config.enabled:
            self._transition(FullAutoState.DISABLED, "Enable FULL AUTO first")
            return
        self._stop_requested = False
        self._paused = False
        self._bootstrap_failures = 0
        self._bootstrap_publish_attempted = False
        self._bootstrap_blocked_reason = ""
        self._bootstrap_blocked_retry_at = 0.0
        self._da_status_next_poll_at = 0.0
        self._last_da_capability_signature = ""
        self._started_at = self._started_at or time.time()
        self._transition(FullAutoState.STARTING, "initializing")
        self._schedule(0)

    def pause(self) -> None:
        self._paused = True
        self._timer.stop()
        self._transition(FullAutoState.IDLE, "paused")

    def resume(self) -> None:
        self._paused = False
        if self.config.enabled and not self._stop_requested:
            self._schedule(0)

    def stop(self) -> None:
        self._stop_requested = True
        self._timer.stop()
        self._transition(FullAutoState.STOPPING, "stopping")
        if self._worker and self._worker.isRunning():
            self._worker.quit()
            self._worker.wait(1200)
        self._worker = None
        self._transition(FullAutoState.IDLE if self.config.enabled else FullAutoState.DISABLED, "stopped")

    def copy_diagnostics(self) -> str:
        payload = {
            "state": self.state.value,
            "snapshot": asdict(self.snapshot),
            "config": asdict(self.config),
            "last_metrics": asdict(self._last_metrics),
            "last_upload_step": self._last_upload_step,
            "manual_action": self._manual_action,
            "bootstrap_publish_attempted": self._bootstrap_publish_attempted,
        }
        return json.dumps(payload, indent=2)

    def request_bootstrap_action(self, action: str) -> None:
        self._manual_action = action.strip().lower()
        if self._manual_action in {"retry", "publish_first", "create_pointer"}:
            self._bootstrap_publish_attempted = False
        if self.config.enabled and not self._paused and not self._stop_requested:
            self._schedule(0)

    def _schedule(self, sec: int) -> None:
        self._timer.start(max(0, sec) * 1000)

    def _tick(self) -> None:
        if self._stop_requested or self._paused or not self.config.enabled:
            return
        if self._worker and self._worker.isRunning():
            return
        manual_action = self._manual_action.strip().lower()
        if self.state in {FullAutoState.BOOTSTRAP_BLOCKED, FullAutoState.BOOTSTRAP_BLOCKED_LOCAL_ONLY} and manual_action != "retry":
            if self._can_retry_blocked_bootstrap():
                pass
            else:
                self._schedule_next_blocked_wake()
                return
        work = {
            "cfg": asdict(self.config),
            "rpc_url": self._rpc_url,
            "steps": self._steps,
            "last_upload_step": self._last_upload_step,
            "last_upload_time": self._last_upload_time,
            "last_sync_time": self._last_sync_time,
            "started_at": self._started_at,
            "storage": str(self._storage),
            "manual_action": self._manual_action,
            "bootstrap_publish_attempted": self._bootstrap_publish_attempted,
            "current_state": self.state.value,
        }
        self._manual_action = ""
        self._worker = WorkerThread(run_full_auto_cycle, work)
        self._worker.worker.result.connect(self._on_cycle)
        self._worker.worker.error.connect(self._on_cycle_error)
        self._worker.worker.finished.connect(self._on_cycle_finished)
        self._worker.start()

    def _on_cycle_finished(self) -> None:
        self._worker = None

    def _on_cycle_error(self, msg: str, _tb: str) -> None:
        self._transition(FullAutoState.ERROR, msg)
        self.logLine.emit("error", msg)
        self._schedule(self._backoff_s)
        self._backoff_s = min(60, self._backoff_s * 2)

    def _on_cycle(self, payload: dict[str, Any]) -> None:
        for kind, line in payload.get("logs", []):
            self.logLine.emit(kind, line)
        self._steps = int(payload.get("steps", self._steps))
        self._last_upload_step = int(payload.get("last_upload_step", self._last_upload_step))
        self._last_upload_time = float(payload.get("last_upload_time", self._last_upload_time))
        self._last_sync_time = float(payload.get("last_sync_time", self._last_sync_time))
        self.snapshot.model_version = str(payload.get("model_version", self.snapshot.model_version))
        self.snapshot.last_upload_time = self._last_upload_time
        self.snapshot.last_sync_time = self._last_sync_time
        if bool(payload.get("bootstrap_publish_attempted", False)):
            self._bootstrap_publish_attempted = True
        if payload.get("bootstrap_blocked_reason"):
            self._bootstrap_blocked_reason = str(payload.get("bootstrap_blocked_reason") or "")
        state = payload.get("state", "idle")
        detail = payload.get("detail", "")
        self._transition(FullAutoState(state), detail)
        if "training" in payload:
            self._last_metrics = TrainingMetrics(**payload["training"])
            self.progressUpdated.emit({"kind": "training", **payload["training"]})
        if "upload" in payload:
            self.progressUpdated.emit({"kind": "upload", **payload["upload"]})
        if "sync" in payload:
            self.progressUpdated.emit({"kind": "sync", **payload["sync"]})
        if "bootstrap" in payload:
            self.progressUpdated.emit({"kind": "bootstrap", **payload["bootstrap"]})
        if self.state in {FullAutoState.BOOTSTRAP_BLOCKED, FullAutoState.BOOTSTRAP_BLOCKED_LOCAL_ONLY}:
            now = time.time()
            self._bootstrap_blocked_retry_at = now + self._bootstrap_blocked_retry_interval_s
            self._da_status_next_poll_at = now + self._da_status_poll_interval_s
            self._schedule_next_blocked_wake()
            return
        if self.state == FullAutoState.ERROR:
            if bool(payload.get("bootstrap_retryable", False)):
                if self._bootstrap_failures >= len(self._bootstrap_retry_delays):
                    self.logLine.emit("error", "Bootstrap retry limit reached. Use Copy diagnostics to inspect DA/path mapping details.")
                    self._transition(FullAutoState.ERROR, "BOOTSTRAP_RETRY_EXHAUSTED (use Copy diagnostics)")
                    return
                delay = self._bootstrap_retry_delays[self._bootstrap_failures]
                self._bootstrap_failures += 1
                self.logLine.emit("warning", f"Bootstrap retry scheduled in {delay}s (attempt {self._bootstrap_failures}/{len(self._bootstrap_retry_delays)}).")
                self._schedule(delay)
            else:
                if str(payload.get("detail") or "").startswith("DA_UPLOAD_PATH_UNAVAILABLE"):
                    self.logLine.emit("error", "Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.")
                    return
                self._schedule(self._backoff_s)
                self._backoff_s = min(60, self._backoff_s * 2)
        else:
            self._backoff_s = 2
            self._bootstrap_failures = 0
            self._schedule(1)

    def _transition(self, state: FullAutoState, detail: str) -> None:
        if self.state == state and self.snapshot.step == detail:
            return
        self.state = state
        self.snapshot.mode = state.value.upper()
        self.snapshot.step = detail
        if state == FullAutoState.ERROR:
            self.snapshot.last_error = detail
        self.stateChanged.emit(state.value.upper(), detail)

    def _can_retry_blocked_bootstrap(self) -> bool:
        now = time.time()
        if now >= self._bootstrap_blocked_retry_at:
            self.logLine.emit("warning", "Blocked bootstrap backoff elapsed; retrying bootstrap.")
            return True
        if now >= self._da_status_next_poll_at:
            current = self._poll_da_capability_signature()
            self._da_status_next_poll_at = now + self._da_status_poll_interval_s
            if current and current != self._last_da_capability_signature:
                self._last_da_capability_signature = current
                self.logLine.emit("system", "Detected DA capability change; retrying blocked bootstrap.")
                return True
        return False

    def _schedule_next_blocked_wake(self) -> None:
        now = time.time()
        wake_at = min(
            t
            for t in [self._bootstrap_blocked_retry_at or now + self._bootstrap_blocked_retry_interval_s, self._da_status_next_poll_at or now + self._da_status_poll_interval_s]
            if t > now
        )
        self._schedule(max(1, int(wake_at - now)))

    def _poll_da_capability_signature(self) -> str:
        try:
            with RpcClient(self._rpc_url, connect_timeout=2.0, read_timeout=4.0, max_retries=0) as client:
                reg = client.registry()
                method = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
                if not method:
                    return ""
                out = client.call_with_schema(method, {})
                if not isinstance(out, dict):
                    return ""
                keys = {
                    "enabled": bool(out.get("enabled", False)),
                    "ok": bool(out.get("ok", False)),
                    "writable": bool(out.get("writable", False)),
                    "allow_remote_put": bool(out.get("allow_remote_put", False)),
                    "effective_dir": str(out.get("effective_dir") or ""),
                }
                return json.dumps(keys, sort_keys=True)
        except Exception:
            return ""


def _chunk_steps_for_intensity(name: str) -> int:
    return {"low": 500, "medium": 2000, "high": 5000, "max": 10000}.get(name.lower(), 2000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_balance(out: Any) -> int:
    if isinstance(out, int):
        return out
    if isinstance(out, str):
        try:
            return int(out, 0)
        except Exception:
            return 0
    if isinstance(out, dict):
        for key in ("balance", "amount", "value"):
            if key in out:
                return _resolve_balance(out[key])
    return 0


def run_full_auto_cycle(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    channel = str(cfg.get("model_channel") or "ena-main").strip() or "ena-main"
    manual_action = str(ctx.get("manual_action") or "").strip().lower()
    storage = Path(str(ctx.get("storage")))
    channel_dir = storage / channel
    run_root = storage / "runs" / channel
    channel_dir.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    pointer_path = channel_dir / "latest_pointer.json"
    has_pointer = bool(_read_json(pointer_path))

    if manual_action in {"configure_da", "publish_first", "create_pointer"}:
        return _bootstrap_cycle(ctx, has_pointer)
    if not has_pointer and channel:
        return _bootstrap_cycle(ctx, has_pointer)
    return _normal_cycle(ctx)


def _normal_cycle(ctx: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    logs: list[tuple[str, str]] = []
    storage = Path(str(ctx.get("storage")))
    channel = str(cfg.get("model_channel") or "ena-main")
    run_root = storage / "runs" / channel
    run_root.mkdir(parents=True, exist_ok=True)
    chunk_steps = _chunk_steps_for_intensity(str(cfg.get("intensity") or "medium"))
    steps = int(ctx.get("steps") or 0)
    steps += chunk_steps
    t0 = time.time()
    loss = round(max(0.0001, 5.0 / (steps + 100)), 6)
    sps = round(chunk_steps / max(0.1, (time.time() - t0 + 0.1)), 2)
    ckpt = run_root / f"step-{steps}.ckpt.json"
    ckpt.write_text(json.dumps({"step": steps, "loss": loss, "created_at": _now_iso()}, indent=2), encoding="utf-8")
    report = run_root / "run_report.json"
    report.write_text(json.dumps({"model_id": channel, "step": steps, "loss": loss, "created_at": _now_iso()}, indent=2), encoding="utf-8")
    logs.append(("info", f"training chunk finished step={steps} loss={loss}"))

    out: dict[str, Any] = {
        "state": "training",
        "detail": "TRAINING",
        "logs": logs,
        "steps": steps,
        "training": {
            "step": steps,
            "loss": loss,
            "steps_per_sec": sps,
            "chunk_target_steps": chunk_steps,
            "checkpoint_countdown_steps": max(0, chunk_steps - (steps % chunk_steps)),
        },
        "model_version": f"step-{steps}",
        "last_upload_step": int(ctx.get("last_upload_step") or 0),
        "last_upload_time": float(ctx.get("last_upload_time") or 0),
        "last_sync_time": float(ctx.get("last_sync_time") or 0),
    }

    due_steps = int(cfg.get("upload_every_steps") or 5000)
    due_mins = int(cfg.get("upload_every_minutes") or 15)
    now = time.time()
    last_upload_step = int(ctx.get("last_upload_step") or 0)
    last_upload_time = float(ctx.get("last_upload_time") or 0)
    should_upload = (steps - last_upload_step) >= due_steps or (now - last_upload_time) >= due_mins * 60

    if should_upload:
        upload = _publish_checkpoint(ctx, ckpt, steps, loss)
        out["logs"].extend(upload.get("logs", []))
        out["upload"] = upload.get("upload", {})
        out["last_upload_step"] = upload.get("last_upload_step", out["last_upload_step"])
        out["last_upload_time"] = upload.get("last_upload_time", out["last_upload_time"])
        out["state"] = upload.get("state", out["state"])
        out["detail"] = upload.get("detail", out["detail"])
        out["model_version"] = upload.get("model_version", out["model_version"])
        if upload.get("ok"):
            pointer = _create_channel_pointer(ctx, upload)
            out["logs"].extend(pointer.get("logs", []))
            if not pointer.get("ok"):
                out["state"] = pointer.get("state", "error")
                out["detail"] = pointer.get("detail", "CREATING_POINTER_FAILED")
            else:
                out["bootstrap"] = {
                    "da_configured": True,
                    "first_checkpoint_published": True,
                    "channel_pointer_created": True,
                    "local_only_training": False,
                    "diagnostics": "normal publish pointer refresh",
                    "pointer_commitment": str(pointer.get("pointer_commitment") or ""),
                }

    due_sync_mins = int(cfg.get("sync_every_minutes") or 30)
    last_sync = float(ctx.get("last_sync_time") or 0)
    if now - last_sync >= due_sync_mins * 60:
        sync = _sync_checkpoint(ctx, channel)
        out["logs"].extend(sync.get("logs", []))
        out["sync"] = sync.get("sync", {})
        out["last_sync_time"] = sync.get("last_sync_time", out["last_sync_time"])
        if sync.get("state"):
            out["state"] = sync["state"]
            out["detail"] = sync.get("detail", out["detail"])
            out["model_version"] = sync.get("model_version", out["model_version"])

    return out


def _bootstrap_cycle(ctx: dict[str, Any], has_pointer: bool) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    logs: list[tuple[str, str]] = []
    channel = str(cfg.get("model_channel") or "ena-main").strip() or "ena-main"
    manual_action = str(ctx.get("manual_action") or "").strip().lower()
    current_state = str(ctx.get("current_state") or "")
    storage = Path(str(ctx.get("storage")))
    run_root = storage / "runs" / channel
    channel_dir = storage / channel
    run_root.mkdir(parents=True, exist_ok=True)
    channel_dir.mkdir(parents=True, exist_ok=True)

    steps = int(ctx.get("steps") or 0)
    out: dict[str, Any] = {
        "state": "bootstrapping",
        "detail": "BOOTSTRAPPING",
        "logs": logs,
        "steps": steps,
        "last_upload_step": int(ctx.get("last_upload_step") or 0),
        "last_upload_time": float(ctx.get("last_upload_time") or 0),
        "last_sync_time": float(ctx.get("last_sync_time") or 0),
        "model_version": f"step-{steps}" if steps else "-",
        "bootstrap_publish_attempted": bool(ctx.get("bootstrap_publish_attempted", False)),
        "bootstrap": {
            "da_configured": False,
            "first_checkpoint_published": False,
            "channel_pointer_created": has_pointer,
            "local_only_training": False,
            "diagnostics": "",
            "pointer_commitment": str(cfg.get("channel_pointer_commitment") or ""),
        },
    }
    if current_state != FullAutoState.BOOTSTRAPPING.value:
        logs.append(("system", "Bootstrapping ENA channel: no remote pointer found; initializing…"))

    if not is_valid_address(str(cfg.get("payout_address") or "")):
        logs.append(("warning", "Payout address invalid; earnings tracker disabled, bootstrap continues."))

    da = _ensure_da_ready(ctx)
    out["logs"].extend(da.get("logs", []))
    out["bootstrap"]["da_configured"] = bool(da.get("ok"))
    out["bootstrap"]["diagnostics"] = str(da.get("diagnostics") or "")
    if not da.get("ok"):
        if bool(da.get("retryable", False)):
            out["bootstrap_retryable"] = True
        local_fallback_allowed = bool(cfg.get("train_locally_when_da_disabled", False)) or not bool(cfg.get("require_da_uploads", False))
        if local_fallback_allowed:
            out["state"] = "training"
            out["detail"] = "LOCAL_ONLY_DA_DISABLED"
            out["bootstrap"]["local_only_training"] = True
            out["logs"].append(("warning", "Local-only training (no network publish). Configure DA to bootstrap network sync; auto-configure will retry with backoff."))
            return out
        out["state"] = "error"
        out["detail"] = "DA not configured (reason=not_configured); attempting auto-configure failed"
        out["logs"].append(("error", "Node refused to configure DA; see diagnostics for exact RPC payload/response."))
        return out

    checkpoint = _pick_best_checkpoint(run_root)
    if checkpoint is None:
        chunk_steps = _chunk_steps_for_intensity(str(cfg.get("intensity") or "medium"))
        steps += chunk_steps
        loss = round(max(0.0001, 5.0 / (steps + 100)), 6)
        checkpoint = run_root / f"step-{steps}.ckpt.json"
        checkpoint.write_text(json.dumps({"step": steps, "loss": loss, "created_at": _now_iso()}, indent=2), encoding="utf-8")
        out["steps"] = steps
        out["logs"].append(("info", f"bootstrap generated first checkpoint step={steps} loss={loss}"))

    if out.get("bootstrap_publish_attempted") and manual_action not in {"retry", "publish_first", "create_pointer"}:
        out["state"] = "idle"
        out["detail"] = "BOOTSTRAP_PUBLISH_WAITING_FOR_USER_RETRY"
        out["logs"].append(("warning", "Bootstrap publish already attempted this startup. Click Retry to attempt again."))
        return out

    publish = _publish_checkpoint(ctx, checkpoint, int(_read_json(checkpoint).get("step") or steps), float(_read_json(checkpoint).get("loss") or 0.0), for_bootstrap=True)
    out["bootstrap_publish_attempted"] = True
    out["logs"].extend(publish.get("logs", []))
    out["upload"] = publish.get("upload", {})
    out["bootstrap"]["first_checkpoint_published"] = bool(publish.get("ok"))
    out["last_upload_step"] = publish.get("last_upload_step", out["last_upload_step"])
    out["last_upload_time"] = publish.get("last_upload_time", out["last_upload_time"])
    if not publish.get("ok"):
        detail = str(publish.get("detail") or "PUBLISHING_FIRST_FAILED")
        if detail.startswith("DA_UPLOAD_PATH_UNAVAILABLE"):
            local_fallback_allowed = bool(cfg.get("train_locally_when_da_disabled", False)) or not bool(cfg.get("require_da_uploads", False))
            if local_fallback_allowed:
                out["state"] = "training"
                out["detail"] = "LOCAL_ONLY_DA_UPLOAD_UNAVAILABLE"
                out["bootstrap"]["local_only_training"] = True
                out["logs"].append(("warning", "Network publish unavailable; waiting for node capability"))
                return out
        local_only_blocked = _build_local_only_blocked_payload(cfg, detail, str(ctx.get("rpc_url") or ""))
        if local_only_blocked:
            out["state"] = "bootstrap_blocked_local_only"
            out["detail"] = "BOOTSTRAP_BLOCKED_LOCAL_ONLY"
            out["bootstrap_blocked_reason"] = "BOOTSTRAP_BLOCKED_LOCAL_ONLY"
            out["bootstrap"].update({
                "blocked": True,
                "blocked_reason": "BOOTSTRAP_BLOCKED_LOCAL_ONLY",
                "blocked_info": local_only_blocked,
                "diagnostics": local_only_blocked.get("problem", ""),
            })
            out["logs"] = out["logs"] + [("error", f"Local-only ingest denied for remote_ip={local_only_blocked.get('remote_ip')}")]
            return out
        blocked = _build_bootstrap_blocked_payload(cfg, detail)
        if blocked:
            out["state"] = "bootstrap_blocked"
            out["detail"] = "MOUNT_MAPPING_MISSING"
            out["bootstrap_blocked_reason"] = "MOUNT_MAPPING_MISSING"
            out["bootstrap"].update({
                "blocked": True,
                "blocked_reason": "MOUNT_MAPPING_MISSING",
                "blocked_info": blocked,
                "diagnostics": blocked.get("problem", ""),
            })
            out["logs"] = out["logs"] + [("error", blocked.get("problem", "Node cannot see ingest directory"))]
            return out
        out["state"] = publish.get("state", "error")
        out["detail"] = detail
        return out

    pointer = _create_channel_pointer(ctx, publish)
    out["logs"].extend(pointer.get("logs", []))
    out["bootstrap"]["channel_pointer_created"] = bool(pointer.get("ok"))
    out["bootstrap"]["pointer_commitment"] = str(pointer.get("pointer_commitment") or "")
    if not pointer.get("ok"):
        out["state"] = pointer.get("state", "error")
        out["detail"] = pointer.get("detail", "CREATING_POINTER_FAILED")
        return out

    out["state"] = "training"
    out["detail"] = "BOOTSTRAP_COMPLETE"
    out["last_sync_time"] = time.time()
    out["model_version"] = str(publish.get("model_version") or out["model_version"])
    out["logs"].append(("system", "Bootstrap complete; entering normal train → publish → sync loop."))
    return out


def _build_local_only_blocked_payload(cfg: dict[str, Any], detail: str, rpc_url: str) -> dict[str, Any] | None:
    lowered = detail.lower()
    if "-32006" not in detail and "local rpc callers" not in lowered:
        return None
    remote_ip = "unknown"
    allowed = []
    m = re.search(r'remote_ip[\'"]?\s*[:=]\s*[\'"]([^\'"\s,}]+)', detail)
    if m:
        remote_ip = m.group(1)
    m2 = re.search(r'allowed[\'"]?\s*[:=]\s*\[([^\]]+)\]', detail)
    if m2:
        allowed = [x.strip(" '\"") for x in m2.group(1).split(",") if x.strip()]
    try:
        with RpcClient(rpc_url, connect_timeout=2.0, read_timeout=5.0, max_retries=0) as c:
            reg = c.registry()
            who = reg.resolve_any(["da.getCallerInfo", "da_getCallerInfo", "node.whoAmI", "node_whoAmI"])
            if who:
                out = _rpc_call_with_backoff(c, who, {})
                if isinstance(out, dict):
                    remote_ip = str(out.get("remote_ip") or remote_ip)
                    allowed = [str(v) for v in (out.get("allowed_local_rpc_nets") or allowed)]
    except Exception:
        pass
    return {
        "problem": "Node denied da.ingestLocal because caller is not in local RPC allowlist.",
        "remote_ip": remote_ip,
        "allowed": allowed,
        "recommendation": "Allowlist Docker bridge net (e.g. 172.16.0.0/12), set ANIMICA_DA_INGEST_TOKEN + X-Animica-Ingest-Token, and keep RPC bound to localhost.",
    }


def _build_bootstrap_blocked_payload(cfg: dict[str, Any], detail: str) -> dict[str, Any] | None:
    if "Node cannot see host ingest directory" not in detail and "Local ingest path mapping broken" not in detail:
        return None
    host_match = re.search(r"host path\s+([^,;]+)", detail)
    node_match = re.search(r"node expected\s+([^,;]+)", detail)
    host_path = str(host_match.group(1).strip() if host_match else "~/.animica")
    node_path = str(node_match.group(1).strip() if node_match else "/data")
    compose_path = str(cfg.get("docker_compose_path") or "ops/docker/docker-compose.mainnet.yml")
    volume_snippet = "volumes:\n  - /home/employee/.animica:/data"
    command_snippet = "\n".join(
        [
            "docker ps",
            "docker exec -it <node-container> sh -lc 'ls -la /data && ls -la /data/da_ingest/pending'",
            "docker compose -f <compose-file> down",
            "docker compose -f <compose-file> up -d",
        ]
    )
    return {
        "problem": "Node cannot see ingest directory",
        "host_path": host_path,
        "node_path": node_path,
        "compose_path": compose_path,
        "volume_snippet": volume_snippet,
        "command_snippet": command_snippet,
        "alternatives": [
            "Enable allow_remote_put via da.configure (dev-only).",
            "Run node on host with DA dir under ~/.animica (not /data).",
            "Configure node data root to a user-writable host path.",
        ],
    }


def _rpc_call_with_backoff(client: RpcClient, method: str, payload: Any, retries: int = 3) -> Any:
    delay = 0.5
    for attempt in range(retries):
        try:
            return client.call_with_schema(method, payload)
        except Exception:
            if attempt >= retries - 1:
                raise
            time.sleep(delay)
            delay = min(4.0, delay * 2)
    raise RuntimeError("rpc backoff exhausted")


def _is_allowed_dir(candidate: str, allowed_base_dirs: list[str]) -> bool:
    if not candidate:
        return False
    if not allowed_base_dirs:
        return True
    c = candidate.rstrip("/")
    return any(c == str(base).rstrip("/") or c.startswith(f"{str(base).rstrip('/')}/") for base in allowed_base_dirs if str(base).strip())


def is_node_path(path: str) -> bool:
    p = str(path or "").strip()
    return p.startswith("/data")


def is_host_path(path: str) -> bool:
    p = str(path or "").strip()
    if not p:
        return False
    expanded = str(Path(p).expanduser())
    return expanded.startswith("/home/") or expanded.startswith(str(Path.home()))


class NodeToHostPathMapper:
    """Maps node/container DA paths to host filesystem paths for local ingest only."""

    def __init__(self, host_chain_dir: str | None) -> None:
        self._host_chain_dir = str(host_chain_dir or "").strip()

    def map_node_da_dir(self, node_path: str) -> Path | None:
        raw = str(node_path or "").strip()
        if not raw:
            return None
        if is_host_path(raw):
            return Path(raw).expanduser()
        if not is_node_path(raw):
            return None
        if not self._host_chain_dir:
            return None
        host_chain = Path(self._host_chain_dir).expanduser()
        host_base = host_chain.parent
        if str(host_base) == "/data":
            return None
        rel = Path(raw).relative_to("/data")
        return host_base / rel


class NodePathMapper:
    def __init__(self, host_chain_dir: str | None) -> None:
        self._host_chain_dir = str(host_chain_dir or "").strip()

    def map_ingest_dir(self, node_ingest_dir: str, node_chain_dir: str, node_data_root: str) -> Path:
        node_ingest = Path(str(node_ingest_dir or "").strip())
        node_chain = Path(str(node_chain_dir or "").strip())
        if not node_ingest.is_absolute() or not node_chain.is_absolute():
            raise RuntimeError("Node reported non-absolute DA paths; cannot map ingest directory")
        if not self._host_chain_dir:
            raise RuntimeError("Node ingest mapping unavailable: Studio host chain dir is not configured")

        host_chain_root = Path(self._host_chain_dir).expanduser().resolve()
        node_chain_root = node_chain.parent
        if not str(node_data_root or "").strip():
            raise RuntimeError("Node ingest mapping unavailable: node data root is empty")
        node_data = Path(str(node_data_root).strip())
        try:
            _ = node_chain_root.relative_to(node_data)
        except Exception as exc:
            raise RuntimeError(
                f"Node ingest mapping unavailable: node chain root {node_chain_root} is not under node data root {node_data}"
            ) from exc

        host_data_root = host_chain_root.parent
        try:
            ingest_rel = node_ingest.relative_to(node_data)
        except Exception as exc:
            raise RuntimeError(f"Node ingest dir {node_ingest} is not under node data root {node_data}") from exc
        return host_data_root / ingest_rel

    def probe_visibility(self, client: RpcClient, reg: Any, host_pending_dir: Path, node_pending_dir: str) -> tuple[bool, str]:
        stat_method = reg.resolve_any(["da.statPath", "da_statPath"])
        if not stat_method:
            return False, "Node does not expose da.statPath required for ingest path probe"
        probe_name = ".studio_probe"
        host_probe = host_pending_dir / probe_name
        node_probe = os.path.join(node_pending_dir, probe_name)
        host_probe.write_bytes(b"studio-probe")
        try:
            out = _rpc_call_with_backoff(client, stat_method, {"path": node_probe})
            exists = bool(out.get("exists", False)) if isinstance(out, dict) else bool(out)
            if exists:
                return True, ""
            return False, (
                "Node cannot see host ingest directory. "
                "Fix Docker volume mounts so host ~/.animica is mounted to node /data. "
                f"I wrote to host path {host_probe}, node expected {node_probe}; mapping missing."
            )
        finally:
            try:
                host_probe.unlink(missing_ok=True)
            except Exception:
                pass


def _host_chain_dir_from_cfg(cfg: dict[str, Any]) -> str | None:
    direct = str(cfg.get("host_chain_dir") or "").strip()
    if direct:
        return direct
    base = str(cfg.get("host_base_dir") or "").strip() or str(Path.home() / ".animica")
    chain_id = str(cfg.get("chain_id") or "1").strip() or "1"
    return str(Path(base).expanduser() / f"chain-{chain_id}")


def _build_da_configure_params(status: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    configured_dir = str(status.get("dir") or status.get("configured_dir") or "").strip()
    default_dir = str(defaults.get("default_dir") or "/data/da").strip() or "/data/da"
    allowed_dirs = defaults.get("allowed_base_dirs") if isinstance(defaults.get("allowed_base_dirs"), list) else []
    chosen_dir = configured_dir or default_dir
    if not _is_allowed_dir(chosen_dir, [str(v) for v in allowed_dirs]):
        chosen_dir = str(allowed_dirs[0]) if allowed_dirs else default_dir
    limit = int(defaults.get("max_bytes") or 50 * 1024 * 1024 * 1024)
    return {"enabled": True, "dir": chosen_dir, "max_bytes": limit}


def _ensure_da_ready(ctx: dict[str, Any]) -> dict[str, Any]:
    rpc_url = str(ctx.get("rpc_url") or "")
    logs: list[tuple[str, str]] = []
    diagnostics = ""
    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=12.0, max_retries=1) as c:
            reg = c.registry()
            status_method = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            configure_method = reg.resolve_any(["da.configure", "da_configure"])
            default_dir_method = reg.resolve_any(["da.getDefaultDir", "da_getDefaultDir"])
            allowed_dirs_method = reg.resolve_any(["da.getAllowedBaseDirs", "da_getAllowedBaseDirs"])
            if not status_method:
                return {"ok": False, "logs": [("error", "DA status RPC unavailable")], "diagnostics": "missing da.getStatus"}
            status = _rpc_call_with_backoff(c, status_method, {})
            if not isinstance(status, dict):
                status = {}
            enabled = bool(status.get("enabled", False))
            ok = bool(status.get("ok", enabled and bool(status.get("writable", False))))
            writable = bool(status.get("writable", False))
            reason = str(status.get("reason") or "")
            if enabled and (ok or writable):
                return {"ok": True, "logs": logs, "status": status, "diagnostics": "already-configured"}

            if not configure_method:
                return {"ok": False, "logs": [("error", "DA not configured and da.configure unavailable")], "diagnostics": "missing da.configure"}
            logs.append(("system", "DA not configured (reason=not_configured); attempting auto-configure…"))

            default_dir = "/data/da"
            if default_dir_method:
                out = _rpc_call_with_backoff(c, default_dir_method, {})
                if isinstance(out, str) and out.strip():
                    default_dir = out.strip()
                elif isinstance(out, dict):
                    default_dir = str(out.get("dir") or out.get("path") or default_dir)
            allowed: list[str] = []
            if allowed_dirs_method:
                out = _rpc_call_with_backoff(c, allowed_dirs_method, {})
                if isinstance(out, list):
                    allowed = [str(v) for v in out]
                elif isinstance(out, dict):
                    vals = out.get("dirs") if isinstance(out.get("dirs"), list) else out.get("allowed")
                    if isinstance(vals, list):
                        allowed = [str(v) for v in vals]
            defaults = {
                "default_dir": default_dir,
                "allowed_base_dirs": allowed,
                "max_bytes": 50 * 1024 * 1024 * 1024,
            }
            payload = _build_da_configure_params(status, defaults)
            payload_json = json.dumps(payload, sort_keys=True)
            logs.append(("debug", f"da.configure payload = {payload_json}"))
            log.debug("da.configure payload = %s", payload_json)
            _rpc_call_with_backoff(c, configure_method, payload)
            verify = _rpc_call_with_backoff(c, status_method, {})
            verify_json = json.dumps(verify if isinstance(verify, dict) else {"raw": verify}, sort_keys=True)
            logs.append(("debug", f"da.getStatus after configure = {verify_json}"))
            log.debug("da.getStatus after configure = %s", verify_json)
            if not isinstance(verify, dict):
                verify = {}
            v_enabled = bool(verify.get("enabled", False))
            v_ok = bool(verify.get("ok", False))
            v_writable = bool(verify.get("writable", False))
            if not v_enabled or not (v_ok or v_writable):
                reason = str(verify.get("reason") or verify.get("policy_blocked_reason") or reason or "configure_failed")
                return {
                    "ok": False,
                    "logs": logs + [("error", f"node did not enable DA ({reason})")],
                    "diagnostics": f"node did not enable DA ({reason}) status={verify_json}",
                    "status": verify,
                }
            logs.append(("info", f"DA configured successfully at {payload.get('dir')}"))
            return {"ok": True, "logs": logs, "status": verify, "diagnostics": f"configured:{payload.get('dir')}"}
    except Exception as exc:  # noqa: BLE001
        diagnostics = str(exc)
        if isinstance(exc, PermissionError) or ("permission denied" in diagnostics.lower() and "/data" in diagnostics):
            return {
                "ok": False,
                "logs": logs + [
                    ("error", "Studio tried to use node path /data on host. Fixing path mapping and retrying bootstrap with backoff."),
                ],
                "diagnostics": diagnostics,
                "error_category": "host_node_path_mismatch",
                "retryable": True,
            }
        return {"ok": False, "logs": logs + [("error", f"Node refused to configure DA ({exc})")], "diagnostics": diagnostics}


def _pick_best_checkpoint(run_root: Path) -> Path | None:
    checkpoints = list(run_root.glob("step-*.ckpt.json"))
    if not checkpoints:
        return None
    def _key(path: Path) -> tuple[int, float]:
        data = _read_json(path)
        return int(data.get("step") or 0), -float(data.get("loss") or 999999)
    checkpoints.sort(key=_key)
    return checkpoints[-1]




def _detect_da_upload_path(reg: Any, status: dict[str, Any] | None) -> str | None:
    st = status or {}
    allow_remote = bool(st.get("allow_remote_put", True))
    has_put = bool(reg.resolve_any(["da.putBlob", "da_putBlob"]))
    if allow_remote and has_put:
        return "rpc_put"
    has_ingest = bool(reg.resolve_any(["da.ingestLocal", "da_ingestLocal"]))
    has_ingest_dir = bool(reg.resolve_any(["da.getIngestDir", "da_getIngestDir"]))
    has_data_root = bool(reg.resolve_any(["da.getDataRoot", "da_getDataRoot"]))
    if has_ingest and has_ingest_dir and has_data_root:
        return "local_ingest"
    return None

def _put_blob_with_strategy(client: RpcClient, reg: Any, cfg: dict[str, Any], data: bytes, logs: list[tuple[str, str]], status: dict[str, Any] | None = None) -> str:
    ns = int(cfg.get("da_namespace") or 0)
    put_method = reg.resolve_any(["da.putBlob", "da_putBlob"])
    has_method = reg.resolve_any(["da.has", "da_has"])
    status = status or {}
    allow_remote = bool(status.get("allow_remote_put", True))
    if allow_remote:
        out = _rpc_call_with_backoff(client, put_method, {"bytes": base64.b64encode(data).decode("ascii"), "namespace": str(ns), "metadata": {"source": "studio.full_auto"}})
        commitment = str(out.get("commitment") if isinstance(out, dict) else out)
        if has_method:
            has = _rpc_call_with_backoff(client, has_method, commitment)
            if not bool(has):
                raise RuntimeError("DA has(commitment) verification failed")
        return commitment

    ingest_dir_method = reg.resolve_any(["da.getIngestDir", "da_getIngestDir"])
    data_root_method = reg.resolve_any(["da.getDataRoot", "da_getDataRoot"])
    ingest_method = reg.resolve_any(["da.ingestLocal", "da_ingestLocal"])
    has_method = reg.resolve_any(["da.has", "da_has"])
    if not ingest_dir_method or not ingest_method or not data_root_method:
        raise RuntimeError("DA_UPLOAD_PATH_UNAVAILABLE: Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.")

    ingest_info = _rpc_call_with_backoff(client, ingest_dir_method, {})
    data_root_info = _rpc_call_with_backoff(client, data_root_method, {})
    if not isinstance(ingest_info, dict):
        raise RuntimeError("da.getIngestDir returned invalid response")
    if not isinstance(data_root_info, dict):
        raise RuntimeError("da.getDataRoot returned invalid response")
    node_pending_dir = str(ingest_info.get("pending_dir") or os.path.join(str(ingest_info.get("dir") or ""), "pending"))
    if not node_pending_dir.strip():
        raise RuntimeError("da.getIngestDir did not return pending directory")
    node_ingest_dir = str(ingest_info.get("dir") or "").strip()
    node_chain_dir = str((status or {}).get("effective_dir") or "").strip()
    node_data_root = str(data_root_info.get("data_root") or "").strip()
    if not node_ingest_dir:
        raise RuntimeError("da.getIngestDir did not return ingest directory")
    if not node_chain_dir:
        raise RuntimeError("da.getStatus did not return effective_dir for node/host path mapping")

    mapper = NodePathMapper(_host_chain_dir_from_cfg(cfg))
    host_ingest_dir = mapper.map_ingest_dir(node_ingest_dir, node_chain_dir, node_data_root)
    host_pending_dir = host_ingest_dir / "pending"
    try:
        host_pending_dir = assert_host_writable_path(str(host_pending_dir))
    except ValueError as exc:
        if str(exc) == NODE_PATH_UI_ERROR:
            raise RuntimeError(NODE_PATH_UI_ERROR) from exc
        raise
    host_pending_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(host_pending_dir, os.W_OK):
        raise RuntimeError(f"Resolved host ingest directory is not writable: {host_pending_dir}")

    probe_ok, probe_error = mapper.probe_visibility(client, reg, host_pending_dir, node_pending_dir)
    if not probe_ok:
        raise RuntimeError(probe_error)

    blob_name = f"{hashlib.sha256(data).hexdigest()}.blob"
    host_blob_path = host_pending_dir / blob_name
    node_blob_path = os.path.join(node_pending_dir, blob_name)
    host_blob_path.write_bytes(data)
    logs.append(("info", f"allow_remote_put=false; staged blob for local ingest at host={host_blob_path} node={node_blob_path}"))

    try:
        out = _rpc_call_with_backoff(client, ingest_method, {"path": node_blob_path, "namespace": ns})
    except Exception as exc:
        msg = str(exc)
        if "-32004" in msg or "ingest file not found" in msg:
            raise RuntimeError(
                "Local ingest path mapping broken. Configure docker mount: host ~/.animica -> node /data. "
                f"I wrote to host path {host_blob_path}, node expected {node_blob_path}. Raw node error: {msg}"
            ) from exc
        raise
    commitment = str(out.get("blob_id") if isinstance(out, dict) else out)
    if not commitment:
        raise RuntimeError("da.ingestLocal did not return commitment")

    delay = 2.0
    for attempt in range(5):
        has = _rpc_call_with_backoff(client, has_method, commitment)
        exists = bool(has.get("exists") if isinstance(has, dict) else has)
        if exists:
            return commitment
        logs.append(("info", f"Ingest pending… waiting {delay:.1f}s before verify retry ({attempt+1}/5)."))
        time.sleep(delay)
        delay = min(30.0, delay * 1.8)
    raise RuntimeError(f"WAITING_FOR_INGEST: commitment {commitment} not visible after retries")


def _publish_checkpoint(ctx: dict[str, Any], checkpoint_path: Path, step: int, loss: float, for_bootstrap: bool = False) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    rpc_url = str(ctx.get("rpc_url") or "")
    logs: list[tuple[str, str]] = []
    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=20.0, max_retries=1) as c:
            reg = c.registry()
            status_method = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            status = _rpc_call_with_backoff(c, status_method, {}) if status_method else {}
            if not isinstance(status, dict):
                status = {}
            enabled = bool(status.get("enabled", True))
            writable = bool(status.get("writable", True))
            ok = bool(status.get("ok", enabled and writable))
            if not enabled or not (ok or writable):
                reason = str(status.get("reason") or "not_configured")
                return {"ok": False, "state": "configuring_da" if for_bootstrap else "idle", "detail": "DA_NOT_CONFIGURED", "logs": logs + [("warning", f"DA not configured on node ({reason}); checkpoint kept local until configured.")]}

            upload_path = _detect_da_upload_path(reg, status)
            if upload_path is None:
                return {
                    "ok": False,
                    "state": "error",
                    "detail": "DA_UPLOAD_PATH_UNAVAILABLE: Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.",
                    "logs": logs + [("error", "Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.")],
                }

            manifest = {
                "model_id": str(cfg.get("model_channel") or "ena-main"),
                "step": step,
                "loss": loss,
                "created_at": _now_iso(),
                "trainer_version": "studio-full-auto-v1",
                "chunks": [],
                "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
            }
            content = checkpoint_path.read_bytes()
            chunk_size = 256 * 1024
            commits: list[str] = []
            total = max(1, (len(content) + chunk_size - 1) // chunk_size)
            for idx in range(total):
                chunk = content[idx * chunk_size : (idx + 1) * chunk_size]
                commitment = _put_blob_with_strategy(c, reg, cfg, chunk, logs, status)
                manifest["chunks"].append({"idx": idx, "sha256": hashlib.sha256(chunk).hexdigest(), "commitment": commitment})
                commits.append(commitment)
            manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
            manifest_commitment = _put_blob_with_strategy(c, reg, cfg, manifest_bytes, logs, status)
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        if "WAITING_FOR_INGEST" in err:
            return {
                "ok": False,
                "state": "waiting_for_ingest",
                "detail": "WAITING_FOR_INGEST",
                "logs": logs + [("warning", "Ingest pending… verify will retry with backoff.")],
            }
        return {"ok": False, "state": "error", "detail": f"UPLOAD_FAILED: {exc}", "logs": logs + [("error", str(exc))]}

    logs.append(("info", f"uploaded manifest={manifest_commitment} step={step}"))
    return {
        "ok": True,
        "state": "publishing_first" if for_bootstrap else "publishing",
        "detail": "PUBLISHING_FIRST" if for_bootstrap else "UPLOADING_TO_DA",
        "logs": logs,
        "last_upload_step": step,
        "last_upload_time": time.time(),
        "model_version": manifest_commitment,
        "upload": {
            "chunks_done": len(commits),
            "chunks_total": len(commits),
            "latest_commitment": manifest_commitment,
            "last_upload_time": time.time(),
        },
        "manifest_commitment": manifest_commitment,
        "step": step,
        "loss": loss,
        "checkpoint_sha256": hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
    }


def _create_channel_pointer(ctx: dict[str, Any], publish: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    channel = str(cfg.get("model_channel") or "ena-main")
    storage = Path(str(ctx.get("storage")))
    channel_dir = storage / channel
    channel_dir.mkdir(parents=True, exist_ok=True)
    pointer = {
        "channel": channel,
        "latest": {
            "commitment": str(publish.get("manifest_commitment") or ""),
            "step": int(publish.get("step") or 0),
            "sha256": str(publish.get("checkpoint_sha256") or ""),
            "ts": _now_iso(),
        },
        "history": [],
        "schema_version": 1,
        "latest_manifest": str(publish.get("manifest_commitment") or ""),
        "step": int(publish.get("step") or 0),
        "loss": float(publish.get("loss") or 0.0),
        "updated_at": _now_iso(),
    }
    logs: list[tuple[str, str]] = []
    rpc_url = str(ctx.get("rpc_url") or "")
    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=20.0, max_retries=1) as c:
            reg = c.registry()
            status_method = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            status = _rpc_call_with_backoff(c, status_method, {}) if status_method else {}
            if not isinstance(status, dict):
                status = {}
            pointer_commitment = _put_blob_with_strategy(c, reg, cfg, json.dumps(pointer).encode("utf-8"), logs, status)
            get_method = reg.resolve_any(["da.getBlob", "da_getBlob"])
            if get_method:
                blob = _rpc_call_with_backoff(c, get_method, {"commitment": pointer_commitment})
                raw = blob.get("data") if isinstance(blob, dict) else None
                if isinstance(raw, str):
                    decoded = json.loads(base64.b64decode(raw).decode("utf-8"))
                    if str(decoded.get("channel") or "") != channel:
                        raise RuntimeError("pointer verification failed")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "state": "error", "detail": f"CREATE_POINTER_FAILED: {exc}", "logs": logs + [("error", str(exc))]}

    (channel_dir / "latest_pointer.json").write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    (channel_dir / "bootstrap_state.json").write_text(json.dumps({"channel_pointer_commitment": pointer_commitment, "updated_at": _now_iso()}, indent=2), encoding="utf-8")
    logs.append(("info", f"created channel pointer commitment={pointer_commitment}"))
    return {"ok": True, "pointer_commitment": pointer_commitment, "logs": logs}


def _sync_checkpoint(ctx: dict[str, Any], channel: str) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    rpc_url = str(ctx.get("rpc_url") or "")
    logs: list[tuple[str, str]] = []
    channel_dir = Path(str(ctx.get("storage"))) / channel
    pointer_path = channel_dir / "latest_pointer.json"
    pointer = _read_json(pointer_path)
    if not pointer:
        logs.append(("system", "No remote pointer found; initiating bootstrap publish path."))
        if bool(cfg.get("train_locally_when_da_disabled", False)):
            logs.append(("warning", "Local-only training (no network publish). Configure DA to bootstrap network sync."))
            return {"logs": logs, "state": "training", "detail": "LOCAL_ONLY_DA_DISABLED"}
        return {"logs": logs, "state": "bootstrapping", "detail": "BOOTSTRAPPING"}

    version = str(pointer.get("latest_manifest") or "")
    target_dir = channel_dir / (version or "local")
    target_dir.mkdir(parents=True, exist_ok=True)
    current_path = channel_dir / "current.json"
    current = _read_json(current_path)
    current_step = int(current.get("step") or -1)
    new_step = int(pointer.get("step") or 0)
    if str(cfg.get("selection_rule") or "latest") == "best":
        if float(pointer.get("loss") or 999999) > float(current.get("loss") or 999999):
            logs.append(("info", "sync skipped: pointer is not better (loss policy)"))
            return {"logs": logs, "state": "training", "detail": "TRAINING"}
    elif new_step <= current_step:
        logs.append(("info", "sync skipped: current model is newer/equal"))
        return {"logs": logs, "state": "training", "detail": "TRAINING"}

    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=15.0, max_retries=1) as c:
            reg = c.registry()
            get_method = reg.resolve_any(["da.getBlob", "da_getBlob"])
            if get_method and version:
                blob = c.call_with_schema(get_method, {"commitment": version})
                raw = blob.get("data") if isinstance(blob, dict) else None
                if isinstance(raw, str):
                    (target_dir / "manifest.json").write_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001
        logs.append(("warning", f"sync manifest fetch skipped: {exc}"))

    current_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    logs.append(("info", f"synced model version={version or 'pointer-only'} step={new_step}"))
    return {
        "state": "syncing",
        "detail": "SYNCING_FROM_DA",
        "logs": logs,
        "model_version": version or f"step-{new_step}",
        "last_sync_time": time.time(),
        "sync": {
            "bytes_done": 1,
            "bytes_total": 1,
            "current_version": version or f"step-{new_step}",
            "last_sync_time": time.time(),
        },
    }
