"""Shared async job runner for Studio UI.

Provides a QObject-based API for subprocess and callable jobs with safe lifetime
management, streaming output, and hard timeouts.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import logging

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QThreadPool, QTimer, Signal

from animica_studio.storage.config import Config, discover_repo_root, load_config, save_config

log = logging.getLogger(__name__)


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




@dataclass
class ResolvedCli:
    argv_prefix: list[str]
    env: dict[str, str]
    repo_root: str | None = None
    error: str | None = None
    attempted_paths: list[str] | None = None


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _norm(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _venv_env(repo_root: Path) -> dict[str, str]:
    venv = repo_root / '.venv'
    venv_bin = venv / 'bin'
    existing_path = os.environ.get('PATH', '')
    merged_path = f"{venv_bin}:{existing_path}" if existing_path else str(venv_bin)
    return {'VIRTUAL_ENV': str(venv), 'PATH': merged_path}


def resolve_animica_cli(cfg: Config | None = None) -> ResolvedCli:
    cfg = cfg or load_config()
    attempted: list[str] = []

    if cfg.cli_path_override:
        override = Path(cfg.cli_path_override).expanduser()
        attempted.append(str(override))
        if _is_executable_file(override):
            cli = _norm(override)
            log.info('CLI resolved to: %s (settings override)', cli)
            return ResolvedCli(argv_prefix=[cli], env={})

    found = shutil.which('animica')
    if found:
        attempted.append(found)
    if found:
        cli = _norm(found)
        log.info('CLI resolved to: %s (PATH)', cli)
        return ResolvedCli(argv_prefix=[cli], env={})

    repo_root: Path | None = Path(cfg.repo_root).expanduser().resolve() if cfg.repo_root else None
    if repo_root is None or not repo_root.exists():
        discovered = discover_repo_root()
        if discovered is not None:
            cfg.repo_root = str(discovered)
            save_config(cfg)
            repo_root = discovered

    if repo_root and cfg.use_repo_venv_automatically:
        venv_bin = repo_root / '.venv' / 'bin'
        venv_cli = venv_bin / 'animica'
        venv_python = venv_bin / 'python'
        attempted.extend([str(venv_cli), str(venv_python)])
        env = _venv_env(repo_root)
        if _is_executable_file(venv_cli):
            cli = str(venv_cli)
            log.info('CLI resolved to: %s (repo .venv bin)', cli)
            return ResolvedCli(argv_prefix=[cli], env=env, repo_root=str(repo_root))
        if _is_executable_file(venv_python):
            cli = str(venv_python)
            log.info('CLI resolved to: %s -m animica (repo .venv python)', cli)
            return ResolvedCli(argv_prefix=[cli, '-m', 'animica'], env=env, repo_root=str(repo_root))

        err = 'Animica CLI not found. Install it or configure its path in Settings.'
        log.warning('Animica CLI resolution failed (repo .venv enabled). attempted_paths=%s which=%s repo_root=%s', attempted, found, repo_root)
        return ResolvedCli(argv_prefix=[], env={}, repo_root=str(repo_root), error=err, attempted_paths=attempted)

    if repo_root and not cfg.use_repo_venv_automatically:
        err = 'Animica CLI not found. Install it or configure its path in Settings.'
        log.warning('Animica CLI resolution failed (repo .venv disabled). attempted_paths=%s which=%s repo_root=%s', attempted, found, repo_root)
        return ResolvedCli(argv_prefix=[], env={}, repo_root=str(repo_root), error=err, attempted_paths=attempted)

    err = 'Animica CLI not found. Install it or configure its path in Settings.'
    log.warning('Animica CLI resolution failed. attempted_paths=%s which=%s repo_root=%s', attempted, found, repo_root)
    return ResolvedCli(
        argv_prefix=[],
        env={},
        repo_root=str(repo_root) if repo_root else None,
        error=err,
        attempted_paths=attempted,
    )


def resolve_animica_cli_program_and_env(cfg: Config | None = None) -> tuple[str, list[str], dict[str, str]]:
    resolved = resolve_animica_cli(cfg)
    if not resolved.argv_prefix:
        msg = resolved.error or 'Animica CLI not found. Install it or configure its path in Settings.'
        raise FileNotFoundError(msg)
    program, *base_args = resolved.argv_prefix
    return program, base_args, resolved.env


def resolve_cli_argv(argv: list[str]) -> tuple[list[str], dict[str, str], str | None]:
    if not argv:
        return [], {}, None
    cmd = argv[0]
    if cmd != 'animica':
        return argv, {}, None

    resolved = resolve_animica_cli()
    if not resolved.argv_prefix:
        return [], {}, resolved.error
    return [*resolved.argv_prefix, *argv[1:]], resolved.env, None


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
        self._stderr_captures: dict[str, str] = {}
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

        resolved_argv, resolved_env, resolve_error = resolve_cli_argv(argv)
        if not resolved_argv:
            QTimer.singleShot(0, lambda: self._emit_missing_cli(handle, resolve_error or "animica CLI not found."))
            return handle

        program, *args = resolved_argv
        log.info("Running argv: %r", [program, *args])
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        if cwd:
            proc.setWorkingDirectory(cwd)
        pe = QProcessEnvironment.systemEnvironment()
        merged_env = dict(resolved_env)
        if env:
            merged_env.update(env)
        for k, v in merged_env.items():
            pe.insert(k, v)
        proc.setProcessEnvironment(pe)

        self._processes[job_id] = proc
        self._stdout_buffers[job_id] = ""
        self._stderr_buffers[job_id] = ""
        self._stderr_captures[job_id] = ""

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

    def _emit_missing_cli(self, handle: JobHandle, msg: str) -> None:
        handle.started.emit(handle.job_id)
        handle.output.emit(handle.job_id, "system", msg)
        handle.error.emit(handle.job_id, msg, "")
        handle.finished.emit(handle.job_id, 127, {"error": msg})
        self._cleanup(handle.job_id)

    def _read_stream(self, job_id: str, handle: JobHandle, stream: str) -> None:
        proc = self._processes.get(job_id)
        if proc is None:
            return
        data = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace") if stream == "stdout" else bytes(proc.readAllStandardError()).decode("utf-8", errors="replace")
        if stream == "stderr" and data:
            self._stderr_captures[job_id] = (self._stderr_captures.get(job_id, "") + data)[-4000:]
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
        proc = self._processes.get(job_id)
        program = proc.program() if proc else "<unknown>"
        details = proc.errorString() if proc else f"QProcess error: {int(err)}"
        handle.error.emit(job_id, f"Process failed to start: {program}", details)

    def _on_finished(self, job_id: str, handle: JobHandle, exit_code: int) -> None:
        self._flush_partial(job_id, handle)
        self._stop_timers(job_id)
        payload = {"ended_ts": time.time()}
        if exit_code != 0:
            stderr_preview = self._stderr_captures.get(job_id, "")[:300]
            log.info("Exit code: %s", exit_code)
            log.info("stderr (first N chars): %s", stderr_preview)
            handle.error.emit(job_id, f"Command exited with code {exit_code}", "")
        else:
            log.info("Exit code: %s", exit_code)
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
        self._stderr_captures.pop(job_id, None)
        self._jobs.pop(job_id, None)
