"""CLI command runner with streaming, cancellation, and timeouts.

Design notes
------------
* Uses ``subprocess.Popen`` with pipes for stdout/stderr.
* Two reader threads drain stdout and stderr concurrently to avoid deadlocks.
* A cancel token allows external code to abort the process.
* A timeout can terminate the process after a configured number of seconds.
* Works on Linux, macOS, and Windows.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Callable

from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_DEFAULT_KILL_GRACE_S = 3.0  # seconds between SIGTERM and SIGKILL


def _reader_thread(
    stream: object,
    stream_name: str,
    out_queue: "queue.Queue[StreamEvent | None]",
) -> None:
    """Read lines from *stream* and push :class:`StreamEvent` objects to *out_queue*.

    Pushes ``None`` as a sentinel when the stream is exhausted.
    """
    try:
        for raw_line in stream:  # type: ignore[union-attr]
            line = raw_line.rstrip("\n").rstrip("\r")
            out_queue.put(StreamEvent(stream=stream_name, ts=time.time(), line=line))  # type: ignore[arg-type]
    finally:
        out_queue.put(None)  # sentinel


class CliRunner:
    """Run CLI commands with streaming output, cancellation, and timeouts."""

    def run(
        self,
        cmd: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> ExecResult:
        """Execute *cmd* and return an :class:`ExecResult`.

        Parameters
        ----------
        cmd:
            Command and arguments.
        cwd:
            Working directory (default: current directory).
        env:
            Environment variables.  If ``None``, inherits the current environment.
        timeout_s:
            Maximum execution time in seconds.  ``None`` means no timeout.
        cancel_token:
            If provided, the process is terminated when the token is cancelled.
        stream_cb:
            Called synchronously for each line of stdout/stderr while the process
            runs.  Do not perform blocking operations inside this callback.
        """
        start_ts = time.time()
        timed_out = False
        cancelled = False
        error: str | None = None
        proc: subprocess.Popen[str] | None = None
        pid: int | None = None
        returncode: int | None = None

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        merged_env: dict[str, str] | None
        if env is not None:
            merged_env = {**os.environ, **env}
        else:
            merged_env = None

        event_q: queue.Queue[StreamEvent | None] = queue.Queue()

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # On Windows avoid creating a new console window
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0),
            )
            pid = proc.pid
            log.debug("CliRunner: started pid=%d cmd=%r", pid, cmd)

            # Start reader threads
            t_out = threading.Thread(
                target=_reader_thread,
                args=(proc.stdout, "stdout", event_q),
                daemon=True,
            )
            t_err = threading.Thread(
                target=_reader_thread,
                args=(proc.stderr, "stderr", event_q),
                daemon=True,
            )
            t_out.start()
            t_err.start()

            deadline: float | None = (start_ts + timeout_s) if timeout_s is not None else None
            sentinels_received = 0

            while sentinels_received < 2:
                # Compute wait time
                now = time.time()
                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0:
                        timed_out = True
                        break
                    wait = min(remaining, 0.1)
                else:
                    wait = 0.1

                # Check cancellation
                if cancel_token is not None and cancel_token.is_cancelled:
                    cancelled = True
                    break

                try:
                    event = event_q.get(timeout=wait)
                except queue.Empty:
                    # Check if process exited without more output
                    if proc.poll() is not None:
                        # Drain remaining events briefly
                        _drain_queue(event_q, stdout_lines, stderr_lines, stream_cb, sentinels_received)
                        break
                    continue

                if event is None:
                    sentinels_received += 1
                    continue

                # Deliver event
                if event.stream == "stdout":
                    stdout_lines.append(event.line)
                else:
                    stderr_lines.append(event.line)
                if stream_cb is not None:
                    try:
                        stream_cb(event)
                    except Exception:  # noqa: BLE001
                        pass

            # Terminate if needed
            if (timed_out or cancelled) and proc.poll() is None:
                _terminate_process(proc)
                # Drain any remaining events after termination
                t_out.join(timeout=2.0)
                t_err.join(timeout=2.0)
                _drain_queue(event_q, stdout_lines, stderr_lines, stream_cb, 0)
            else:
                t_out.join(timeout=5.0)
                t_err.join(timeout=5.0)

            returncode = proc.wait(timeout=5.0) if proc.poll() is None else proc.returncode

        except FileNotFoundError as exc:
            error = f"Command not found: {cmd[0]!r} — {exc}"
            log.error("CliRunner: %s", error)
        except PermissionError as exc:
            error = f"Permission denied running {cmd[0]!r} — {exc}"
            log.error("CliRunner: %s", error)
        except Exception as exc:  # noqa: BLE001
            error = f"Unexpected error running {cmd!r}: {exc}"
            log.exception("CliRunner: unexpected error")
        finally:
            if proc is not None:
                try:
                    if proc.stdout:
                        proc.stdout.close()
                    if proc.stderr:
                        proc.stderr.close()
                except OSError:
                    pass

        end_ts = time.time()
        duration_ms = int((end_ts - start_ts) * 1000)

        meta: dict[str, object] = {"pid": pid}

        result = ExecResult(
            cmd=cmd,
            returncode=returncode,
            timed_out=timed_out,
            cancelled=cancelled,
            start_ts=start_ts,
            end_ts=end_ts,
            duration_ms=duration_ms,
            stdout="\n".join(stdout_lines),
            stderr="\n".join(stderr_lines),
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines,
            error=error,
            meta=meta,
        )

        if timed_out:
            log.warning("CliRunner: command timed out after %.1fs: %r", timeout_s, cmd)
            if stream_cb is not None:
                try:
                    stream_cb(StreamEvent(stream="system", ts=time.time(), line=f"[timeout after {timeout_s}s]"))
                except Exception:  # noqa: BLE001
                    pass
        elif cancelled:
            log.info("CliRunner: command cancelled: %r", cmd)
            if stream_cb is not None:
                try:
                    stream_cb(StreamEvent(stream="system", ts=time.time(), line="[cancelled]"))
                except Exception:  # noqa: BLE001
                    pass

        return result


def _drain_queue(
    q: "queue.Queue[StreamEvent | None]",
    stdout_lines: list[str],
    stderr_lines: list[str],
    stream_cb: Callable[[StreamEvent], None] | None,
    already_received: int,
) -> None:
    """Drain remaining events from *q* without blocking."""
    while True:
        try:
            event = q.get_nowait()
        except queue.Empty:
            break
        if event is None:
            continue
        if event.stream == "stdout":
            stdout_lines.append(event.line)
        else:
            stderr_lines.append(event.line)
        if stream_cb is not None:
            try:
                stream_cb(event)
            except Exception:  # noqa: BLE001
                pass


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    """Attempt graceful termination, then force-kill after a grace period."""
    try:
        proc.terminate()
    except (OSError, ProcessLookupError):
        return

    deadline = time.time() + _DEFAULT_KILL_GRACE_S
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.05)

    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        pass
