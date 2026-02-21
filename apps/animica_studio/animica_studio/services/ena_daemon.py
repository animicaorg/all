"""Local ENA daemon lifecycle management."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
from typing import Any

from animica_studio.util.paths import app_data_dir, logs_dir

log = logging.getLogger(__name__)


@dataclass
class EnaDaemonStatus:
    running: bool
    pid: int | None
    endpoint: str
    log_file: str


class EnaDaemonManager:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = int(port)
        self._state_dir = app_data_dir() / "ena"
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._pid_file = self._state_dir / "ena.pid"
        self._log_file = logs_dir() / "ena-daemon.log"
        self._ring: deque[str] = deque(maxlen=400)
        self._tail_thread: threading.Thread | None = None
        self._tail_stop = threading.Event()

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def status(self) -> EnaDaemonStatus:
        pid = self._read_pid()
        running = bool(pid and self._pid_alive(pid))
        return EnaDaemonStatus(running=running, pid=pid if running else None, endpoint=self.endpoint, log_file=str(self._log_file))

    def start(self) -> EnaDaemonStatus:
        st = self.status()
        if st.running:
            return st
        if not self._port_available(self.port):
            raise RuntimeError(f"Port {self.port} is in use")
        cmd = [
            "python",
            "-m",
            "animica_studio.services.ena_daemon_server",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        with self._log_file.open("a", encoding="utf-8") as fh:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=fh, start_new_session=True)  # noqa: S603
        self._pid_file.write_text(str(proc.pid), encoding="utf-8")
        self._tail_logs()
        return self.status()

    def stop(self) -> EnaDaemonStatus:
        pid = self._read_pid()
        if not pid:
            return self.status()
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        self._pid_file.unlink(missing_ok=True)
        return self.status()

    def restart(self) -> EnaDaemonStatus:
        self.stop()
        return self.start()

    def diagnostics(self) -> dict[str, Any]:
        return {
            "status": self.status().__dict__,
            "last_logs": list(self._ring),
            "log_file": str(self._log_file),
            "pid_file": str(self._pid_file),
        }

    def _read_pid(self) -> int | None:
        if not self._pid_file.exists():
            return None
        try:
            return int(self._pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _port_available(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, port))
            except OSError:
                return False
        return True

    def _tail_logs(self) -> None:
        if self._tail_thread and self._tail_thread.is_alive():
            return
        self._tail_stop.clear()

        def _tail() -> None:
            pos = 0
            while not self._tail_stop.is_set():
                if not self._log_file.exists():
                    self._tail_stop.wait(0.5)
                    continue
                with self._log_file.open("r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                if chunk:
                    for line in chunk.splitlines():
                        self._ring.append(line)
                self._tail_stop.wait(0.5)

        self._tail_thread = threading.Thread(target=_tail, daemon=True)
        self._tail_thread.start()

    def write_state(self, state: dict[str, Any]) -> None:
        (self._state_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
