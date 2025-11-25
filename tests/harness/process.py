"""
tests.harness.process — subprocess management for integration tests
===================================================================

Utilities to spawn long-running child processes (nodes, services, proxies)
with:

- Line-buffered stdout/stderr capture to per-process log files
- In-memory ring buffer of recent output for quick debugging on failure
- Cross-platform graceful shutdown (SIGTERM/CTRL_BREAK → SIGKILL)
- Ready checks via:
    * regex match on output lines, and/or
    * a callable predicate, and/or
    * waiting for a TCP port to accept connections
- Small supervisor to track multiple processes and tear them down together

Typical usage
-------------
    from tests.harness.process import ManagedProcess, ProcessSupervisor
    from tests.harness.ports import reserve_port, wait_for_listen

    sup = ProcessSupervisor()

    with reserve_port("rpc") as rp:
        rpc_port = rp.port
        p = ManagedProcess(
            ["my-node", f"--rpc-port={rpc_port}"],
            name="node-1",
            ready_port=("127.0.0.1", rpc_port),
            ready_timeout=15.0,
        ).start()
        sup.add(p)
        rp.handoff()

        # ... run tests ...

    # On exit (or in a pytest fixture finalizer):
    sup.teardown()

Notes
-----
- All logs are written under TEST_ARTIFACTS_DIR/logs (if set) otherwise
  a temp dir like <tmp>/animica-tests/logs/<timestamp>/.
- For Windows, CTRL_BREAK is used for graceful shutdown if possible.
"""

from __future__ import annotations

import atexit
import io
import os
import re
import sys
import time
import queue
import shutil
import signal
import threading
import tempfile
import subprocess
from dataclasses import dataclass, field
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Iterable, List, Optional, Sequence, Tuple, Union

try:
    # Optional helper (used when waiting on a port)
    from tests.harness.ports import wait_for_listen
except Exception:  # pragma: no cover - optional import for standalone use
    def wait_for_listen(host: str, port: int, timeout: float = 10.0, interval: float = 0.05) -> bool:
        import socket
        import contextlib
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with contextlib.closing(socket.create_connection((host, port), timeout=interval)):
                    return True
            except OSError:
                time.sleep(interval)
        return False


# --------------------------------------------------------------------------------------
# Paths & logging
# --------------------------------------------------------------------------------------

def _default_artifacts_root() -> Path:
    root = os.getenv("TEST_ARTIFACTS_DIR")
    if root:
        return Path(root).absolute()
    base = Path(tempfile.gettempdir()) / "animica-tests" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = base / ts
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "proc"


# --------------------------------------------------------------------------------------
# Stream tee to file + ring buffer
# --------------------------------------------------------------------------------------

class _TeeReader(threading.Thread):
    """Reads lines from a pipe, writes to file, keeps a small ring buffer."""
    def __init__(self, stream, log_path: Path, ring: Deque[str], name: str):
        super().__init__(name=f"tee-{name}", daemon=True)
        self._stream = stream
        self._log_path = log_path
        self._ring = ring
        self._stop = threading.Event()

    def run(self) -> None:
        # Ensure parent exists
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8", buffering=1) as f:
            for line in self._iter_lines(self._stream):
                if self._stop.is_set():
                    break
                f.write(line)
                self._ring.append(line)

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _iter_lines(stream: io.TextIOBase):
        while True:
            chunk = stream.readline()
            if not chunk:
                break
            # Normalize to str
            if isinstance(chunk, bytes):
                chunk = chunk.decode(errors="replace")
            yield chunk


# --------------------------------------------------------------------------------------
# ManagedProcess
# --------------------------------------------------------------------------------------

ReadyPredicate = Callable[[], bool]

@dataclass
class ManagedProcess:
    """
    Wrapper for a long-running subprocess with log capture and graceful teardown.
    """
    args: Sequence[Union[str, os.PathLike]]
    name: str = "proc"
    cwd: Optional[Union[str, os.PathLike]] = None
    env: Optional[dict] = None
    log_dir: Optional[Union[str, os.PathLike]] = None
    ready_regex: Optional[Union[str, re.Pattern[str]]] = None
    ready_predicate: Optional[ReadyPredicate] = None
    ready_port: Optional[Tuple[str, int]] = None
    ready_timeout: float = 20.0
    kill_grace_period: float = 8.0       # seconds to wait after TERM/CTRL_BREAK
    kill_hard_timeout: float = 5.0       # seconds to wait after KILL
    extra_creationflags: int = 0         # windows-only
    start_new_session: bool = True       # posix: setsid; windows: new process group
    stdout_log_name: Optional[str] = None
    stderr_log_name: Optional[str] = None
    ring_size: int = 400                 # last N log lines kept in memory

    # Internal state (populated after start)
    proc: Optional[subprocess.Popen] = field(default=None, init=False)
    stdout_path: Optional[Path] = field(default=None, init=False)
    stderr_path: Optional[Path] = field(default=None, init=False)
    _stdout_ring: Deque[str] = field(default_factory=lambda: deque(maxlen=400), init=False)
    _stderr_ring: Deque[str] = field(default_factory=lambda: deque(maxlen=400), init=False)
    _tee_out: Optional[_TeeReader] = field(default=None, init=False)
    _tee_err: Optional[_TeeReader] = field(default=None, init=False)
    _ready_event: threading.Event = field(default_factory=threading.Event, init=False)

    def start(self) -> "ManagedProcess":
        if self.proc is not None:
            raise RuntimeError("Process already started")

        # Logs setup
        root = Path(self.log_dir) if self.log_dir else _default_artifacts_root()
        pname = _safe_name(self.name)
        self.stdout_path = root / f"{pname}.stdout.log"
        self.stderr_path = root / f"{pname}.stderr.log"

        # Prepare creation flags / session
        popen_kwargs = {}
        if os.name == "nt":
            # Create independent console group to allow CTRL_BREAK
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            popen_kwargs["creationflags"] = (self.extra_creationflags or 0) | (CREATE_NEW_PROCESS_GROUP if self.start_new_session else 0)
        else:
            if self.start_new_session:
                popen_kwargs["preexec_fn"] = os.setsid  # type: ignore

        # Launch
        self.proc = subprocess.Popen(
            list(map(str, self.args)),
            cwd=str(self.cwd) if self.cwd else None,
            env=self._build_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
            **popen_kwargs,  # type: ignore
        )

        # Begin tee threads
        assert self.proc.stdout and self.proc.stderr
        self._tee_out = _TeeReader(self.proc.stdout, self.stdout_path, self._stdout_ring, f"{self.name}-out")
        self._tee_err = _TeeReader(self.proc.stderr, self.stderr_path, self._stderr_ring, f"{self.name}-err")
        self._tee_out.start()
        self._tee_err.start()

        # Kick off background ready watcher if criteria provided
        if self.ready_regex or self.ready_predicate or self.ready_port:
            t = threading.Thread(target=self._wait_ready_bg, name=f"ready-{self.name}", daemon=True)
            t.start()
        else:
            self._ready_event.set()

        return self

    def _build_env(self) -> dict:
        env = os.environ.copy()
        if self.env:
            env.update({str(k): str(v) for k, v in self.env.items()})
        return env

    # ---- Ready logic -------------------------------------------------------

    def _wait_ready_bg(self) -> None:
        pattern: Optional[re.Pattern[str]] = None
        if self.ready_regex is not None:
            pattern = re.compile(self.ready_regex) if isinstance(self.ready_regex, str) else self.ready_regex

        start = time.time()
        while time.time() - start < self.ready_timeout:
            if self.ready_predicate and self._safe_predicate(self.ready_predicate):
                self._ready_event.set()
                return
            if self.ready_port:
                host, port = self.ready_port
                if wait_for_listen(host, int(port), timeout=0.25, interval=0.05):
                    self._ready_event.set()
                    return
            if pattern:
                # scan newest lines first, then block briefly
                if any(pattern.search(line) for line in list(self._stdout_ring)[-50:] + list(self._stderr_ring)[-50:]):
                    self._ready_event.set()
                    return
            # check if process died
            if self.proc and (self.proc.poll() is not None):
                # Process exited before becoming ready; don't loop forever
                break
            time.sleep(0.05)
        # timeout or early exit: leave event unset

    @staticmethod
    def _safe_predicate(pred: ReadyPredicate) -> bool:
        try:
            return bool(pred())
        except Exception:
            return False

    def wait_ready(self, timeout: Optional[float] = None) -> bool:
        """Block until ready criteria met or timeout expires."""
        if timeout is None:
            timeout = self.ready_timeout
        return self._ready_event.wait(timeout=timeout)

    # ---- Convenience properties -------------------------------------------

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ---- Teardown ----------------------------------------------------------

    def terminate(self) -> None:
        """Try graceful stop (TERM/CTRL_BREAK), then hard kill if needed."""
        if not self.proc:
            return
        if self.proc.poll() is not None:
            self._stop_teers()
            return

        try:
            if os.name == "nt":
                # Send CTRL_BREAK to the process group if possible
                try:
                    self._send_ctrl_break()
                except Exception:
                    self.proc.terminate()
            else:
                # Send to the whole process group if we used setsid
                pgid = os.getpgid(self.proc.pid) if self.start_new_session else None
                if pgid and pgid > 0:
                    os.killpg(pgid, signal.SIGTERM)
                else:
                    self.proc.terminate()
        except Exception:
            # If anything goes wrong, fall back to kill
            self.kill()
            return

        # Wait grace period
        self._wait_for_exit(self.kill_grace_period)

        # If still alive, escalate
        if self.running:
            self.kill()

        self._stop_teers()

    def kill(self) -> None:
        """Hard kill (KILL) of the process (and group if available)."""
        if not self.proc:
            return
        if self.proc.poll() is not None:
            self._stop_teers()
            return
        try:
            if os.name != "nt":
                pgid = os.getpgid(self.proc.pid) if self.start_new_session else None
                if pgid and pgid > 0:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    self.proc.kill()
            else:
                self.proc.kill()
        finally:
            self._wait_for_exit(self.kill_hard_timeout)
            self._stop_teers()

    def _wait_for_exit(self, timeout: float) -> None:
        if not self.proc:
            return
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def _stop_teers(self) -> None:
        for t in (self._tee_out, self._tee_err):
            if t:
                t.stop()
        # Give threads a moment to flush
        time.sleep(0.02)

    def _send_ctrl_break(self) -> None:
        """Windows-friendly soft stop by CTRL_BREAK_EVENT."""
        if os.name != "nt":
            return
        # Requires the process to be in a new process group (creationflag set)
        try:
            signal.CTRL_BREAK_EVENT  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback to terminate
            self.proc.terminate()
            return
        # Send to the process *group*
        subprocess.Popen.send_signal(self.proc, signal.CTRL_BREAK_EVENT)  # type: ignore

    # ---- Context manager ---------------------------------------------------

    def __enter__(self) -> "ManagedProcess":
        return self if self.proc is not None else self.start()

    def __exit__(self, exc_type, exc, tb):
        self.terminate()

    # ---- Debug helpers -----------------------------------------------------

    def tail(self, n: int = 50) -> str:
        """Return the last N lines of combined stdout+stderr."""
        lines = list(self._stdout_ring)[-n:] + list(self._stderr_ring)[-n:]
        return "".join(lines)

    def log_paths(self) -> Tuple[Optional[Path], Optional[Path]]:
        return self.stdout_path, self.stderr_path


# --------------------------------------------------------------------------------------
# Process supervisor
# --------------------------------------------------------------------------------------

class ProcessSupervisor:
    """Tracks multiple ManagedProcess instances for coordinated teardown."""
    def __init__(self) -> None:
        self._procs: list[ManagedProcess] = []
        self._closed = False
        atexit.register(self._atexit_teardown)

    def add(self, p: ManagedProcess) -> None:
        if self._closed:
            raise RuntimeError("ProcessSupervisor already torn down")
        self._procs.append(p)

    def teardown(self) -> None:
        if self._closed:
            return
        # Terminate in reverse start order
        for p in reversed(self._procs):
            with _suppress():
                p.terminate()
        self._procs.clear()
        self._closed = True

    def _atexit_teardown(self) -> None:
        with _suppress():
            self.teardown()


# --------------------------------------------------------------------------------------
# Misc helpers
# --------------------------------------------------------------------------------------

class _suppress:
    def __enter__(self):  # pragma: no cover
        return self
    def __exit__(self, exc_type, exc, tb):  # pragma: no cover
        return True


def start_and_wait(
    args: Sequence[Union[str, os.PathLike]],
    name: str,
    cwd: Optional[Union[str, os.PathLike]] = None,
    env: Optional[dict] = None,
    ready_regex: Optional[Union[str, re.Pattern[str]]] = None,
    ready_port: Optional[Tuple[str, int]] = None,
    ready_predicate: Optional[ReadyPredicate] = None,
    ready_timeout: float = 20.0,
    log_dir: Optional[Union[str, os.PathLike]] = None,
) -> ManagedProcess:
    """
    Fire-and-wait convenience: start a ManagedProcess and wait until it's ready.
    Raises RuntimeError on timeout or early exit.
    """
    p = ManagedProcess(
        args=args, name=name, cwd=cwd, env=env,
        ready_regex=ready_regex, ready_port=ready_port,
        ready_predicate=ready_predicate, ready_timeout=ready_timeout,
        log_dir=log_dir,
    ).start()

    if not p.wait_ready(timeout=ready_timeout):
        # If process died, include return code
        rc = p.proc.poll() if p.proc else None
        tail = p.tail(120)
        p.terminate()
        raise RuntimeError(
            f"Process '{name}' not ready in {ready_timeout:.1f}s (rc={rc}).\n--- tail ---\n{tail}"
        )
    return p
