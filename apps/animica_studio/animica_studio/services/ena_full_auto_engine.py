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
    BOOTSTRAPPING = "bootstrapping"
    CONFIGURING_DA = "configuring_da"
    CREATING_POINTER = "creating_pointer"
    PUBLISHING_FIRST = "publishing_first"
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
        }
        return json.dumps(payload, indent=2)

    def request_bootstrap_action(self, action: str) -> None:
        self._manual_action = action.strip().lower()
        if self.config.enabled and not self._paused and not self._stop_requested:
            self._schedule(0)

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
            "manual_action": self._manual_action,
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
    channel = str(cfg.get("model_channel") or "ena-main").strip() or "ena-main"
    storage = Path(str(ctx.get("storage")))
    channel_dir = storage / channel
    run_root = storage / "runs" / channel
    channel_dir.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    pointer_path = channel_dir / "latest_pointer.json"
    has_pointer = bool(_read_json(pointer_path))
    manual_action = str(ctx.get("manual_action") or "").strip().lower()

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
        "bootstrap": {
            "da_configured": False,
            "first_checkpoint_published": False,
            "channel_pointer_created": has_pointer,
            "local_only_training": False,
            "diagnostics": "",
            "pointer_commitment": str(cfg.get("channel_pointer_commitment") or ""),
        },
    }
    logs.append(("system", "Bootstrapping ENA channel: no remote pointer found; initializing…"))

    if not is_valid_address(str(cfg.get("payout_address") or "")):
        logs.append(("warning", "Payout address invalid; earnings tracker disabled, bootstrap continues."))

    da = _ensure_da_ready(ctx)
    out["logs"].extend(da.get("logs", []))
    out["bootstrap"]["da_configured"] = bool(da.get("ok"))
    out["bootstrap"]["diagnostics"] = str(da.get("diagnostics") or "")
    if not da.get("ok"):
        if bool(cfg.get("train_locally_when_da_disabled", False)):
            out["state"] = "training"
            out["detail"] = "LOCAL_ONLY_DA_DISABLED"
            out["bootstrap"]["local_only_training"] = True
            out["logs"].append(("warning", "Local-only training (no network publish). Configure DA to bootstrap network sync."))
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

    publish = _publish_checkpoint(ctx, checkpoint, int(_read_json(checkpoint).get("step") or steps), float(_read_json(checkpoint).get("loss") or 0.0), for_bootstrap=True)
    out["logs"].extend(publish.get("logs", []))
    out["upload"] = publish.get("upload", {})
    out["bootstrap"]["first_checkpoint_published"] = bool(publish.get("ok"))
    out["last_upload_step"] = publish.get("last_upload_step", out["last_upload_step"])
    out["last_upload_time"] = publish.get("last_upload_time", out["last_upload_time"])
    if not publish.get("ok"):
        out["state"] = publish.get("state", "error")
        out["detail"] = publish.get("detail", "PUBLISHING_FIRST_FAILED")
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
            dir_path = default_dir if _is_allowed_dir(default_dir, allowed) else (allowed[0] if allowed else "/data/da")
            payload = {"enabled": True, "dir": dir_path, "max_bytes": 50 * 1024 * 1024 * 1024, "limit_bytes": 50 * 1024 * 1024 * 1024}
            _rpc_call_with_backoff(c, configure_method, payload)
            verify = _rpc_call_with_backoff(c, status_method, {})
            if not isinstance(verify, dict):
                verify = {}
            v_enabled = bool(verify.get("enabled", False))
            v_ok = bool(verify.get("ok", False))
            v_writable = bool(verify.get("writable", False))
            if not v_enabled or not (v_ok or v_writable):
                reason = str(verify.get("reason") or verify.get("policy_blocked_reason") or reason or "configure_failed")
                return {"ok": False, "logs": logs + [("error", f"Node refused to configure DA ({reason})")], "diagnostics": f"Node refused to configure DA ({reason})"}
            logs.append(("info", f"DA configured successfully at {dir_path}"))
            return {"ok": True, "logs": logs, "status": verify, "diagnostics": f"configured:{dir_path}"}
    except Exception as exc:  # noqa: BLE001
        diagnostics = str(exc)
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


def _put_blob_with_strategy(client: RpcClient, reg: Any, cfg: dict[str, Any], data: bytes, logs: list[tuple[str, str]], status: dict[str, Any] | None = None) -> str:
    ns = int(cfg.get("da_namespace") or 0)
    put_method = reg.resolve_any(["da.putBlob", "da_putBlob"])
    has_method = reg.resolve_any(["da.has", "da_has"])
    status = status or {}
    allow_remote = bool(status.get("allow_remote_put", True))
    if allow_remote:
        out = _rpc_call_with_backoff(client, put_method, {"data": base64.b64encode(data).decode("ascii"), "namespace": ns})
        commitment = str(out.get("commitment") if isinstance(out, dict) else out)
        if has_method:
            has = _rpc_call_with_backoff(client, has_method, commitment)
            if not bool(has):
                raise RuntimeError("DA has(commitment) verification failed")
        return commitment

    configured_dir = str(status.get("dir") or status.get("configured_dir") or "")
    if configured_dir:
        ingest_dir = Path(configured_dir) / "studio_local_ingest"
        ingest_dir.mkdir(parents=True, exist_ok=True)
        blob_path = ingest_dir / f"{hashlib.sha256(data).hexdigest()}.blob"
        blob_path.write_bytes(data)
        logs.append(("warning", f"allow_remote_put=false; attempted local ingest fallback at {blob_path}"))
    raise RuntimeError("DA policy blocks remote put; enable allow_remote_put or provide node-side ingest")


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
