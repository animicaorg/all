from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import requests
from animica_studio.services.ena_remote_preflight import ServicesPreflight
from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.services.rpc_client import RpcClient
from animica_studio.services.workers import WorkerThread

log = logging.getLogger(__name__)


class EnaContributionState(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    SUBMITTING = "submitting"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass
class EnaContributionMetrics:
    running_since: float = 0.0
    current_job_id: str = ""
    jobs_completed: int = 0
    jobs_failed: int = 0
    submissions_ok: int = 0
    submissions_failed: int = 0
    credits_earned: float = 0.0
    cpu_threads_in_use: int = 0
    last_submit_time: float = 0.0
    backoff_seconds: int = 0


@dataclass
class EnaContributionConfig:
    enabled: bool = False
    intensity: str = "medium"
    mode: str = "local"  # local/rpc/remote
    services_url: str = ""
    auto_start: bool = False
    rpc_url: str = ""
    worker_id: str = ""


class EnaContributionEngine(QObject):
    stateChanged = Signal(str)
    metricsUpdated = Signal(object)
    logLine = Signal(str, str)
    lastErrorChanged = Signal(str)

    def __init__(self, config: EnaContributionConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self.state = EnaContributionState.DISABLED if not config.enabled else EnaContributionState.IDLE
        self.metrics = EnaContributionMetrics()
        self._last_error = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._busy_worker: WorkerThread | None = None
        self._stop_requested = False
        self._concurrency_lock = threading.Lock()
        self._backoff_s = 2
        self._max_backoff_s = 60

    def apply_config(self, config: EnaContributionConfig) -> None:
        self.config = config
        if not config.enabled:
            self.stop()
            self._transition(EnaContributionState.DISABLED)
        else:
            if self.state == EnaContributionState.DISABLED:
                self._transition(EnaContributionState.IDLE)
        self._emit_metrics()

    def start_if_configured(self) -> None:
        if self.config.enabled and self.config.auto_start:
            self.start()

    def start(self) -> None:
        if not self.config.enabled:
            self._set_error("Auto-contribute is disabled. Enable the toggle first.")
            self._transition(EnaContributionState.DISABLED)
            return
        if self._busy_worker and self._busy_worker.isRunning():
            return
        self._stop_requested = False
        if self.metrics.running_since <= 0:
            self.metrics.running_since = time.time()
        self._transition(EnaContributionState.STARTING)
        self._schedule_next(0)

    def stop(self) -> None:
        self._stop_requested = True
        self._timer.stop()
        self._transition(EnaContributionState.STOPPING)
        if self._busy_worker and self._busy_worker.isRunning():
            self._busy_worker.quit()
            self._busy_worker.wait(1500)
        self._busy_worker = None
        if self.config.enabled:
            self._transition(EnaContributionState.IDLE)
        else:
            self._transition(EnaContributionState.DISABLED)

    def copy_diagnostics(self) -> str:
        payload = {
            "state": self.state.value,
            "config": asdict(self.config),
            "metrics": asdict(self.metrics),
            "last_error": self._last_error,
        }
        return json.dumps(payload, indent=2)

    def test_connection(self) -> tuple[bool, str]:
        return self._preflight_check()

    def _transition(self, state: EnaContributionState) -> None:
        self.state = state
        self.stateChanged.emit(state.value)

    def _set_error(self, msg: str) -> None:
        self._last_error = msg
        self.lastErrorChanged.emit(msg)
        self.logLine.emit("error", msg)

    def _emit_metrics(self) -> None:
        self.metricsUpdated.emit(self.metrics)

    def _schedule_next(self, seconds: int) -> None:
        self.metrics.backoff_seconds = max(0, seconds)
        self._emit_metrics()
        self._timer.start(max(seconds, 0) * 1000)

    def _tick(self) -> None:
        if self._stop_requested or not self.config.enabled:
            return
        if self._busy_worker and self._busy_worker.isRunning():
            return
        self._busy_worker = WorkerThread(self._run_cycle)
        self._busy_worker.worker.result.connect(self._on_cycle_result)
        self._busy_worker.worker.error.connect(lambda msg, _tb: self._on_cycle_result({"ok": False, "error": msg}))
        self._busy_worker.start()

    def _on_cycle_result(self, result: dict[str, Any]) -> None:
        self._busy_worker = None
        if self._stop_requested:
            return
        if result.get("ok"):
            self._last_error = ""
            self.lastErrorChanged.emit("")
            self._backoff_s = 2
            if result.get("status") == "idle":
                self._transition(EnaContributionState.IDLE)
                self._schedule_next(2)
            else:
                self._transition(EnaContributionState.IDLE)
                self._schedule_next(0)
            return
        self._transition(EnaContributionState.ERROR)
        err = str(result.get("error") or "Unknown error")
        self._set_error(err)
        self._schedule_next(self._backoff_s)
        self._backoff_s = min(self._backoff_s * 2, self._max_backoff_s)

    def _preflight_check(self) -> tuple[bool, str]:
        mode = (self.config.mode or "local").strip().lower()
        if mode == "local":
            return True, "Local backend enabled (no remote job submission)."
        if mode == "remote":
            raw = (self.config.services_url or "").strip()
            preflight = ServicesPreflight.check(raw)
            if not preflight.ok:
                return False, f"Remote ENA services unreachable (DNS/HTTP). {preflight.message}"
            return True, f"Remote reachable: {preflight.checked_url}"
        if mode == "rpc":
            if not self.config.rpc_url:
                return False, "RPC URL is not configured for RPC mode."
            c = RpcClient(self.config.rpc_url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
            try:
                registry = c.registry()
                method = registry.resolve_any([
                    "ena.getJobs", "ena_getJobs", "ena.listJobs", "ena_listJobs", "aicf.getJobs", "aicf_getJobs", "aicf.listJobs", "aicf_listJobs",
                ])
                if not method:
                    return False, "RPC job queue not exposed; configure services_url or switch mode to services."
            except Exception as exc:
                return False, f"RPC discovery failed: {exc}"
            finally:
                c.close()
            return True, "RPC queue is available"
        return False, f"Unsupported mode: {self.config.mode}"

    def _run_cycle(self) -> dict[str, Any]:
        with self._concurrency_lock:
            ok, msg = self._preflight_check()
            if not ok:
                return {"ok": False, "error": msg, "status": "idle"}
            job = self._acquire_job()
            if not job:
                self.logLine.emit("info", "No jobs available; staying idle.")
                return {"ok": True, "status": "idle"}
            job_id = str(job.get("job_id") or job.get("jobId") or job.get("id") or "")
            self.metrics.current_job_id = job_id
            self._transition(EnaContributionState.WORKING)
            work_result = self._execute_cpu_work(job)
            self._transition(EnaContributionState.SUBMITTING)
            submit_out = self._submit_result(job, work_result)
            if submit_out.get("ok"):
                self.metrics.jobs_completed += 1
                self.metrics.submissions_ok += 1
                credits = submit_out.get("credits")
                if isinstance(credits, (int, float)):
                    self.metrics.credits_earned += float(credits)
                self.metrics.last_submit_time = time.time()
                self.logLine.emit("info", f"Submitted result for {job_id}")
            else:
                self.metrics.jobs_failed += 1
                self.metrics.submissions_failed += 1
                raise RuntimeError(str(submit_out.get("error") or "submit failed"))
            self.metrics.current_job_id = ""
            self._emit_metrics()
            return {"ok": True, "status": "worked"}

    def _acquire_job(self) -> dict[str, Any] | None:
        mode = (self.config.mode or "local").lower()
        if mode == "local":
            self.logLine.emit("system", "[system] ENA backend=local")
            return {"ok": True, "credits": 0.0}
        if mode == "rpc":
            c = RpcClient(self.config.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
            try:
                reg = c.registry()
                method = reg.resolve_any(["ena.getJobs", "ena_getJobs", "ena.listJobs", "ena_listJobs", "aicf.getJobs", "aicf_getJobs", "aicf.listJobs", "aicf_listJobs"])
                if not method:
                    return None
                out = c.call(method, [{"status": "available", "limit": 1}, {"limit": 1}, {}])
                if isinstance(out, list) and out:
                    return out[0] if isinstance(out[0], dict) else {"id": str(out[0])}
                if isinstance(out, dict):
                    jobs = out.get("jobs") if isinstance(out.get("jobs"), list) else []
                    if jobs:
                        return jobs[0] if isinstance(jobs[0], dict) else {"id": str(jobs[0])}
                return None
            finally:
                c.close()
        if mode != "remote":
            self.logLine.emit("system", "[system] ENA backend=local")
            return {"job_id": f"local-{int(time.time())}", "local": True}
        url = self.config.services_url.rstrip("/") + "/v1/aicf/jobs/available"
        params = {"worker_id": self._worker_id()}
        r = requests.get(url, params=params, timeout=12)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        payload = r.json() if r.content else {}
        if isinstance(payload, dict) and (payload.get("job_id") or payload.get("jobId") or payload.get("id")):
            return payload
        return None

    def _execute_cpu_work(self, job: dict[str, Any]) -> dict[str, Any]:
        intensity = (self.config.intensity or "medium").lower()
        cores = max(os.cpu_count() or 1, 1)
        mapping = {
            "low": (max(1, cores // 4), 32),
            "medium": (max(1, cores // 2), 64),
            "high": (max(1, (cores * 3) // 4), 96),
            "max": (0, 128),
        }
        threads, batch = mapping.get(intensity, mapping["medium"])
        if intensity != "max" and threads >= cores:
            threads = max(1, cores - 1)
        effective_threads = cores if threads == 0 else max(1, threads)
        self.metrics.cpu_threads_in_use = effective_threads
        self._emit_metrics()
        seed = (str(job.get("job_id") or job.get("id") or uuid.uuid4()) + str(time.time())).encode("utf-8")

        def _hash_worker(worker_idx: int) -> str:
            data = seed + f":{worker_idx}".encode("utf-8")
            out = data
            rounds = 15000 + (batch * 500)
            for _ in range(rounds):
                out = hashlib.sha256(out).digest()
            return out.hex()

        start = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=effective_threads) as pool:
            parts = list(pool.map(_hash_worker, range(effective_threads)))
        artifact = hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()
        return {
            "artifact_hash": artifact,
            "runtime_sec": round(time.time() - start, 3),
            "threads": effective_threads,
            "batch": batch,
            "status": "completed",
        }

    def _submit_result(self, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        if self.config.rpc_url:
            da_check = self._check_da_status()
            if not da_check[0]:
                return {"ok": False, "error": da_check[1]}
        mode = (self.config.mode or "local").lower()
        if mode == "local":
            self.logLine.emit("system", "[system] ENA backend=local")
            return {"ok": True, "credits": 0.0}
        if mode == "rpc":
            c = RpcClient(self.config.rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1)
            try:
                reg = c.registry()
                method = reg.resolve_any([
                    "ena.submitResult", "ena_submitResult", "aicf.submit", "aicf_submit", "aicf.submitJob", "aicf_submitJob", "aicf.registerArtifact", "aicf_registerArtifact", "ena.publishArtifact", "ena_publishArtifact",
                ])
                if not method:
                    return {"ok": False, "error": "No RPC submission method discovered."}
                payload = {
                    "job_id": job.get("job_id") or job.get("id"),
                    "worker_id": self._worker_id(),
                    "result": result,
                }
                out = c.call(method, [payload])
                credits = out.get("credits") if isinstance(out, dict) else None
                return {"ok": True, "credits": credits}
            finally:
                c.close()
        submit_url = self.config.services_url.rstrip("/") + "/v1/aicf/jobs/submit"
        payload = {
            "worker_id": self._worker_id(),
            "job_id": job.get("job_id") or job.get("jobId") or job.get("id"),
            "result": result,
        }
        r = requests.post(submit_url, json=payload, timeout=15)
        r.raise_for_status()
        out = r.json() if r.content else {}
        return {"ok": True, "credits": (out or {}).get("credits") if isinstance(out, dict) else None}

    def _check_da_status(self) -> tuple[bool, str]:
        c = RpcClient(self.config.rpc_url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
        try:
            reg = c.registry()
            m = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            if not m:
                return True, ""
            out = c.call(m, [{}])
            if isinstance(out, dict) and out.get("enabled") and out.get("allow_remote_put") is False:
                return False, "DA is enabled but allow_remote_put=false. Enable DA remote upload or switch to local put strategy."
            return True, ""
        except Exception:
            return True, ""
        finally:
            c.close()

    def _worker_id(self) -> str:
        if self.config.worker_id:
            return self.config.worker_id
        return f"studio-{uuid.getnode():x}"
