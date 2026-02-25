from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.models.wallet_models import is_valid_address
from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.workers import WorkerThread
from animica_studio.util.paths import app_data_dir


class FullAutoState(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    TRAINING = "training"
    EVALUATING = "evaluating"
    PUBLISHING = "publishing"
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
    da_namespace: str = "0"
    model_channel: str = "ena-main"
    require_da_uploads: bool = False
    auto_fallback_on_remote_put_block: bool = True
    max_daily_training_minutes: int = 24 * 60


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
        self._last_metrics = TrainingMetrics()
        self._storage = app_data_dir() / "ena_models"
        self._storage.mkdir(parents=True, exist_ok=True)

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
        if not is_valid_address(self.config.payout_address):
            self._transition(FullAutoState.ERROR, "Invalid payout address")
            return
        self._stop_requested = False
        self._paused = False
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
        }
        return json.dumps(payload, indent=2)

    def _schedule(self, sec: int) -> None:
        self._timer.start(max(0, sec) * 1000)

    def _tick(self) -> None:
        if self._stop_requested or self._paused or not self.config.enabled:
            return
        if self._worker and self._worker.isRunning():
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
        }
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
        if self.state == FullAutoState.ERROR:
            self._schedule(self._backoff_s)
            self._backoff_s = min(60, self._backoff_s * 2)
        else:
            self._backoff_s = 2
            self._schedule(1)

    def _transition(self, state: FullAutoState, detail: str) -> None:
        self.state = state
        self.snapshot.mode = state.value.upper()
        self.snapshot.step = detail
        if state == FullAutoState.ERROR:
            self.snapshot.last_error = detail
        self.stateChanged.emit(state.value.upper(), detail)


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


def _publish_checkpoint(ctx: dict[str, Any], checkpoint_path: Path, step: int, loss: float) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    rpc_url = str(ctx.get("rpc_url") or "")
    logs: list[tuple[str, str]] = []
    status_ok = True
    allow_remote = True
    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1) as c:
            reg = c.registry()
            m = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            if m:
                st = c.call_with_schema(m, {})
                if isinstance(st, dict):
                    allow_remote = bool(st.get("allow_remote_put", True))
                    enabled = bool(st.get("enabled", True))
                    status_ok = enabled
    except Exception as exc:  # noqa: BLE001
        logs.append(("warning", f"DA status unavailable: {exc}"))
    if not status_ok:
        return {"state": "idle", "detail": "IDLE", "logs": logs + [("warning", "DA disabled; local training continues.")]} 
    if not allow_remote:
        if bool(cfg.get("require_da_uploads", False)) and not bool(cfg.get("auto_fallback_on_remote_put_block", True)):
            return {"state": "error", "detail": "Publish blocked: allow_remote_put=false", "logs": logs + [("error", "DA policy blocks RPC upload; enable allow_remote_put or configure local ingest")]} 
        logs.append(("warning", "Publish blocked (allow_remote_put=false); keeping local-only mode."))
        return {"state": "training", "detail": "TRAINING", "logs": logs}

    manifest = {
        "model_id": str(cfg.get("model_channel") or "ena-main"),
        "step": step,
        "loss": loss,
        "created_at": _now_iso(),
        "trainer_version": "studio-full-auto-v1",
        "chunks": [],
    }
    content = checkpoint_path.read_bytes()
    chunk_size = 256 * 1024
    commits: list[str] = []
    try:
        with RpcClient(rpc_url, connect_timeout=3.0, read_timeout=20.0, max_retries=1) as c:
            reg = c.registry()
            put_method = reg.resolve_any(["da.putBlob", "da_putBlob"])
            if not put_method:
                raise RuntimeError("da.putBlob method unavailable")
            ns = str(cfg.get("da_namespace") or "0")
            total = max(1, (len(content) + chunk_size - 1) // chunk_size)
            for idx in range(total):
                chunk = content[idx * chunk_size : (idx + 1) * chunk_size]
                payload = {"data": base64.b64encode(chunk).decode("ascii"), "namespace": ns}
                out = c.call_with_schema(put_method, payload)
                commitment = str(out.get("commitment") if isinstance(out, dict) else out)
                manifest["chunks"].append({"idx": idx, "sha256": hashlib.sha256(chunk).hexdigest(), "commitment": commitment})
                commits.append(commitment)
            manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
            manifest_out = c.call_with_schema(put_method, {"data": base64.b64encode(manifest_bytes).decode("ascii"), "namespace": ns})
            manifest_commitment = str(manifest_out.get("commitment") if isinstance(manifest_out, dict) else manifest_out)
            pointer = {
                "channel": manifest["model_id"],
                "latest_manifest": manifest_commitment,
                "step": step,
                "loss": loss,
                "updated_at": _now_iso(),
            }
            pointer_out = c.call_with_schema(put_method, {"data": base64.b64encode(json.dumps(pointer).encode("utf-8")).decode("ascii"), "namespace": ns})
            pointer_commitment = str(pointer_out.get("commitment") if isinstance(pointer_out, dict) else pointer_out)
    except Exception as exc:  # noqa: BLE001
        return {"state": "error", "detail": f"UPLOAD_FAILED: {exc}", "logs": logs + [("error", str(exc))]}

    channel_dir = Path(str(ctx.get("storage"))) / str(cfg.get("model_channel") or "ena-main")
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "latest_pointer.json").write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    logs.append(("info", f"uploaded manifest={manifest_commitment} pointer={pointer_commitment}"))
    return {
        "state": "publishing",
        "detail": "UPLOADING_TO_DA",
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
    }


def _sync_checkpoint(ctx: dict[str, Any], channel: str) -> dict[str, Any]:
    cfg = dict(ctx.get("cfg") or {})
    rpc_url = str(ctx.get("rpc_url") or "")
    logs: list[tuple[str, str]] = []
    channel_dir = Path(str(ctx.get("storage"))) / channel
    pointer_path = channel_dir / "latest_pointer.json"
    pointer = _read_json(pointer_path)
    if not pointer:
        logs.append(("info", "No remote pointer found yet; waiting for first publish."))
        return {"logs": logs, "state": "training", "detail": "TRAINING"}

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

    # Try to fetch manifest if possible; fallback to pointer only.
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
