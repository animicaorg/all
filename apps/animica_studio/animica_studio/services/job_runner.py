"""Shared async job runner for Studio UI.

Provides a QObject-based API for subprocess and callable jobs with safe lifetime
management, streaming output, and hard timeouts.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThreadPool, QTimer, Signal


class JobHandle(QObject):
    started = Signal(str)
    output = Signal(str, str, str)  # job_id, stream(stdout|stderr|system), text
    progress = Signal(str, str)
    finished = Signal(str, int, object)
    error = Signal(str, str, str)  # job_id, message, details

    def __init__(self, job_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.job_id = job_id


class _CallableTaskSignals(QObject):
    result = Signal(object)
    error = Signal(str, str)


class _CallableTask:
    def __init__(self, fn: Callable[[], Any], signals: _CallableTaskSignals) -> None:
        self._fn = fn
        self._signals = signals

    def run(self) -> None:
        try:
            self._signals.result.emit(self._fn())
        except Exception as exc:  # noqa: BLE001
            self._signals.error.emit(str(exc), repr(exc))


class JobRunner(QObject):
    _instance: "JobRunner | None" = None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs: dict[str, JobHandle] = {}
        self._processes: dict[str, QProcess] = {}
        self._timeouts: dict[str, QTimer] = {}
        self._grace_timers: dict[str, QTimer] = {}
        self._stdout_buffers: dict[str, str] = {}
        self._stderr_buffers: dict[str, str] = {}
        self._pool = QThreadPool.globalInstance()

    @classmethod
    def instance(cls) -> "JobRunner":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def run_cli(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int = 120,
    ) -> JobHandle:
        job_id = str(uuid.uuid4())
        handle = JobHandle(job_id, self)
        self._jobs[job_id] = handle

        resolved = resolve_cli_argv(argv)
        if not resolved:
            QTimer.singleShot(0, lambda: self._emit_missing_cli(handle))
            return handle

        program, *args = resolved
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        if cwd:
            proc.setWorkingDirectory(cwd)
        if env:
            pe = QProcessEnvironment.systemEnvironment()
            for k, v in env.items():
                pe.insert(k, v)
            proc.setProcessEnvironment(pe)

        self._processes[job_id] = proc
        self._stdout_buffers[job_id] = ""
        self._stderr_buffers[job_id] = ""

        proc.started.connect(lambda: handle.started.emit(job_id))
        proc.readyReadStandardOutput.connect(lambda: self._read_stream(job_id, handle, "stdout"))
        proc.readyReadStandardError.connect(lambda: self._read_stream(job_id, handle, "stderr"))
        proc.finished.connect(lambda code, _status: self._on_finished(job_id, handle, int(code)))
        proc.errorOccurred.connect(lambda err: self._on_process_error(job_id, handle, err))

        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: self._on_timeout(job_id, handle))
        self._timeouts[job_id] = timeout
        timeout.start(max(1, timeout_s) * 1000)

        proc.start()
        return handle

    def run_callable(self, fn: Callable[[], Any], timeout_s: int = 30) -> JobHandle:
        job_id = str(uuid.uuid4())
        handle = JobHandle(job_id, self)
        self._jobs[job_id] = handle
        signals = _CallableTaskSignals(self)
        task = _CallableTask(fn, signals)

        signals.result.connect(lambda value: self._finalize_callable(job_id, handle, value))
        signals.error.connect(lambda msg, details: self._fail(job_id, handle, msg, details))

        QTimer.singleShot(0, lambda: handle.started.emit(job_id))

        timeout = QTimer(self)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: self._fail(job_id, handle, f"Timed out after {timeout_s}s", ""))
        self._timeouts[job_id] = timeout
        timeout.start(max(1, timeout_s) * 1000)

        self._pool.start(task.run)
        return handle

    def cancel(self, job_id: str) -> None:
        proc = self._processes.get(job_id)
        if proc is None:
            return
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.terminate()
            grace = QTimer(self)
            grace.setSingleShot(True)
            grace.timeout.connect(lambda: proc.kill())
            self._grace_timers[job_id] = grace
            grace.start(1500)

    def _emit_missing_cli(self, handle: JobHandle) -> None:
        handle.started.emit(handle.job_id)
        msg = "animica CLI not found. Install it or configure its path in Settings."
        handle.output.emit(handle.job_id, "system", msg)
        handle.error.emit(handle.job_id, msg, "")
        handle.finished.emit(handle.job_id, 127, {"error": msg})
        self._cleanup(handle.job_id)

    def _read_stream(self, job_id: str, handle: JobHandle, stream: str) -> None:
        proc = self._processes.get(job_id)
        if proc is None:
            return
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace") if stream == "stdout" else bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
        buf_key = self._stdout_buffers if stream == "stdout" else self._stderr_buffers
        pending = buf_key.get(job_id, "") + data
        lines = pending.splitlines(keepends=True)
        remainder = ""
        for line in lines:
            if line.endswith("\n") or line.endswith("\r"):
                handle.output.emit(job_id, stream, line.rstrip("\r\n"))
            else:
                remainder = line
        buf_key[job_id] = remainder

    def _flush_partial(self, job_id: str, handle: JobHandle) -> None:
        for stream, mapping in (("stdout", self._stdout_buffers), ("stderr", self._stderr_buffers)):
            rem = mapping.get(job_id, "")
            if rem:
                handle.output.emit(job_id, stream, rem)
                mapping[job_id] = ""

    def _on_timeout(self, job_id: str, handle: JobHandle) -> None:
        handle.error.emit(job_id, "Process timed out", "Exceeded configured timeout")
        handle.output.emit(job_id, "system", "[timeout] terminating process")
        self.cancel(job_id)

    def _on_process_error(self, job_id: str, handle: JobHandle, err: QProcess.ProcessError) -> None:
        handle.error.emit(job_id, "Process error", f"QProcess error: {int(err)}")

    def _on_finished(self, job_id: str, handle: JobHandle, exit_code: int) -> None:
        self._flush_partial(job_id, handle)
        self._stop_timers(job_id)
        payload = {"ended_ts": time.time()}
        if exit_code != 0:
            handle.error.emit(job_id, f"Command exited with code {exit_code}", "")
        handle.finished.emit(job_id, exit_code, payload)
        self._cleanup(job_id)

    def _finalize_callable(self, job_id: str, handle: JobHandle, value: Any) -> None:
        if job_id not in self._jobs:
            return
        self._stop_timers(job_id)
        handle.finished.emit(job_id, 0, value)
        self._cleanup(job_id)

    def _fail(self, job_id: str, handle: JobHandle, message: str, details: str) -> None:
        if job_id not in self._jobs:
            return
        self._stop_timers(job_id)
        handle.error.emit(job_id, message, details)
        handle.finished.emit(job_id, 1, {"error": message, "details": details})
        self._cleanup(job_id)

    def _stop_timers(self, job_id: str) -> None:
        timer = self._timeouts.pop(job_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        grace = self._grace_timers.pop(job_id, None)
        if grace is not None:
            grace.stop()
            grace.deleteLater()

    def _cleanup(self, job_id: str) -> None:
        proc = self._processes.pop(job_id, None)
        if proc is not None:
            proc.deleteLater()
        self._stdout_buffers.pop(job_id, None)
        self._stderr_buffers.pop(job_id, None)
        self._jobs.pop(job_id, None)


def resolve_cli_argv(argv: list[str]) -> list[str]:
    """Resolve the animica executable for dev and packaged Studio layouts."""
    if not argv:
        return []
    cmd = argv[0]
    if cmd != "animica":
        return argv

    found = shutil.which("animica")
    if found:
        return [found, *argv[1:]]

    exe_dir = Path(os.path.dirname(os.path.abspath(os.getenv("PYTHONEXECUTABLE") or os.sys.executable)))
    candidates = [
        exe_dir / "animica",
        exe_dir / "_internal" / "animica",
        exe_dir / "animica.exe",
        exe_dir / "_internal" / "animica.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate), *argv[1:]]

    return []
