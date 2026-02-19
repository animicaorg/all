"""ProcessManager: start/stop/restart a local Animica node process.

Design
------
* Configurable start command (defaults to ``["animica", "node", "start"]``).
* Writes a PID file in the app-data directory so the node can be tracked across
  restarts of the Studio app.
* Captures stdout/stderr to ``node.log`` in the app-data directory.
* Checks liveness via RPC ping; falls back to OS process existence check.
* Cross-platform: uses ``os.kill(pid, 0)`` on POSIX, ``OpenProcess`` via ctypes on Windows.
* Does NOT depend on Qt.
"""

from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from animica_studio.services.rpc_client import RpcClient, RpcTransportError
from animica_studio.util.paths import app_data_dir

log = logging.getLogger(__name__)

_START_WAIT_INTERVAL_S = 0.5
_START_MAX_WAIT_S = 20.0
_SHUTDOWN_GRACE_S = 5.0
_LOG_TAIL_LINES = 50


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with *pid* is alive.  Cross-platform."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_pid_alive_windows(pid)
    # POSIX
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True
    except OSError:
        return False


def _is_pid_alive_windows(pid: int) -> bool:
    """Use ctypes OpenProcess to check liveness on Windows."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if handle == 0:
        return False
    # GetExitCodeProcess to check if still running
    exit_code = ctypes.c_ulong(0)
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))  # type: ignore[attr-defined]
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    STILL_ACTIVE = 259
    return exit_code.value == STILL_ACTIVE


def _terminate_pid(pid: int, grace_s: float = _SHUTDOWN_GRACE_S) -> bool:
    """Terminate a process: SIGTERM then SIGKILL after grace period.

    Returns True if the process is no longer alive after the operation.
    """
    if not _is_pid_alive(pid):
        return True

    # Graceful
    try:
        if sys.platform == "win32":
            import subprocess  # noqa: PLC0415
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass

    deadline = time.time() + grace_s
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            return True
        time.sleep(0.1)

    # Force kill
    try:
        if sys.platform == "win32":
            import subprocess  # noqa: PLC0415
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass

    time.sleep(0.2)
    return not _is_pid_alive(pid)


def _tail_file(path: Path, n: int) -> list[str]:
    """Read the last *n* lines from *path*.  Returns empty list on error."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-n:]]
    except OSError:
        return []


class ProcessManager:
    """Manage the lifecycle of a local Animica node process.

    Parameters
    ----------
    start_cmd:
        Command and arguments to start the node.
    rpc_url:
        Local RPC URL used to check liveness and attempt graceful shutdown.
    data_dir:
        Directory where ``node.pid`` and ``node.log`` are written.
        Defaults to :func:`~animica_studio.util.paths.app_data_dir`.
    log_file_name:
        Basename for the node log file.
    pid_file_name:
        Basename for the PID file.
    """

    def __init__(
        self,
        start_cmd: list[str] | None = None,
        rpc_url: str = "http://127.0.0.1:8545/rpc",
        data_dir: Path | None = None,
        log_file_name: str = "node.log",
        pid_file_name: str = "node.pid",
    ) -> None:
        self._start_cmd = start_cmd or ["animica", "node", "start"]
        self._rpc_url = rpc_url
        self._data_dir = data_dir or app_data_dir()
        self._log_file = self._data_dir / log_file_name
        self._pid_file = self._data_dir / pid_file_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the node unless it is already running.

        Returns a status dict (see :meth:`status`).
        """
        current = self.status()
        if current["running"]:
            log.info("ProcessManager: node already running (pid=%s)", current.get("pid"))
            return current

        log.info("ProcessManager: starting node: %s", self._start_cmd)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        log_fd = open(self._log_file, "a", encoding="utf-8")  # noqa: WPS515
        try:
            import subprocess  # noqa: PLC0415

            extra_flags: int = 0
            if sys.platform == "win32":
                extra_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                # Detach from the process group so the node survives Studio exit
                pass

            proc = subprocess.Popen(
                self._start_cmd,
                stdout=log_fd,
                stderr=log_fd,
                stdin=subprocess.DEVNULL,
                creationflags=extra_flags,
                start_new_session=(sys.platform != "win32"),
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            log_fd.close()
            log.error("ProcessManager: failed to start node: %s", exc)
            return {"running": False, "pid": None, "rpc_reachable": False, "error": str(exc)}

        self._write_pid(proc.pid)
        log.info("ProcessManager: node started with pid=%d", proc.pid)

        # Wait for RPC readiness
        ready = self._wait_for_rpc(timeout_s=_START_MAX_WAIT_S)
        if not ready:
            log.warning("ProcessManager: node did not become RPC-ready within %.1fs", _START_MAX_WAIT_S)

        result = self.status()
        result["just_started"] = True
        return result

    def stop(self) -> dict[str, Any]:
        """Stop the node gracefully, falling back to SIGKILL.

        Returns a status dict.
        """
        pid = self._read_pid()
        if pid is None or not _is_pid_alive(pid):
            log.info("ProcessManager: node not running")
            self._remove_pid()
            return {"running": False, "stopped": True, "pid": None}

        # Try graceful RPC shutdown first
        if self._try_rpc_shutdown():
            # Wait briefly for process to exit
            deadline = time.time() + _SHUTDOWN_GRACE_S
            while time.time() < deadline:
                if not _is_pid_alive(pid):
                    break
                time.sleep(0.2)

        if _is_pid_alive(pid):
            log.info("ProcessManager: sending SIGTERM/terminate to pid=%d", pid)
            _terminate_pid(pid, grace_s=_SHUTDOWN_GRACE_S)

        alive = _is_pid_alive(pid)
        self._remove_pid()
        log.info("ProcessManager: stop complete, alive=%s", alive)
        return {"running": alive, "stopped": not alive, "pid": pid}

    def restart(self) -> dict[str, Any]:
        """Stop then start the node."""
        self.stop()
        return self.start()

    def status(self) -> dict[str, Any]:
        """Return a structured status dict.

        Keys
        ----
        running: bool
        pid: int | None
        rpc_reachable: bool
        last_log_lines: list[str]
        """
        pid = self._read_pid()
        pid_alive = pid is not None and _is_pid_alive(pid)
        rpc_reachable = self._ping_rpc()

        running = pid_alive or rpc_reachable

        # Clean up stale PID file
        if pid is not None and not pid_alive and not rpc_reachable:
            self._remove_pid()
            pid = None

        return {
            "running": running,
            "pid": pid if pid_alive else None,
            "rpc_reachable": rpc_reachable,
            "log_file": str(self._log_file),
            "last_log_lines": _tail_file(self._log_file, _LOG_TAIL_LINES),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ping_rpc(self) -> bool:
        try:
            client = RpcClient(self._rpc_url, connect_timeout=2.0, read_timeout=3.0, max_retries=1)
            result = client.ping()
            client.close()
            return result
        except Exception:  # noqa: BLE001
            return False

    def _try_rpc_shutdown(self) -> bool:
        """Attempt graceful node shutdown via RPC.  Returns True if call succeeded."""
        try:
            client = RpcClient(self._rpc_url, connect_timeout=2.0, read_timeout=5.0, max_retries=1)
            for method in ("node_stop", "node.stop", "node_shutdown", "node.shutdown"):
                try:
                    client.call(method)
                    client.close()
                    log.info("ProcessManager: graceful shutdown via RPC method %r", method)
                    return True
                except Exception:  # noqa: BLE001
                    continue
            client.close()
        except Exception:  # noqa: BLE001
            pass
        return False

    def _wait_for_rpc(self, timeout_s: float) -> bool:
        """Poll RPC ping until ready or *timeout_s* elapsed."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self._ping_rpc():
                return True
            time.sleep(_START_WAIT_INTERVAL_S)
        return False

    def _write_pid(self, pid: int) -> None:
        try:
            self._pid_file.write_text(str(pid), encoding="utf-8")
        except OSError as exc:
            log.warning("ProcessManager: could not write PID file: %s", exc)

    def _read_pid(self) -> int | None:
        try:
            text = self._pid_file.read_text(encoding="utf-8").strip()
            return int(text) if text else None
        except (OSError, ValueError):
            return None

    def _remove_pid(self) -> None:
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass
