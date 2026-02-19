"""Data models for CLI command execution results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamEvent:
    """A single line of output from a running process.

    Attributes
    ----------
    stream:
        One of ``"stdout"``, ``"stderr"``, or ``"system"`` (for synthetic messages).
    ts:
        Unix timestamp when the line was received.
    line:
        The text content (newline stripped).
    """

    stream: str
    ts: float
    line: str


@dataclass
class ExecResult:
    """Result of executing a CLI command via :class:`~animica_studio.services.cli_runner.CliRunner`.

    Attributes
    ----------
    cmd:
        The command that was run.
    returncode:
        Process exit code, or ``None`` if the process was killed/timed out.
    timed_out:
        ``True`` if the process was killed due to timeout.
    cancelled:
        ``True`` if the process was killed due to a cancel token.
    start_ts:
        Unix timestamp when execution started.
    end_ts:
        Unix timestamp when execution ended.
    duration_ms:
        Wall-clock duration in milliseconds.
    stdout:
        Full stdout as a single string (newlines joined).
    stderr:
        Full stderr as a single string (newlines joined).
    stdout_lines:
        Individual stdout lines.
    stderr_lines:
        Individual stderr lines.
    error:
        Human-readable error message if the command could not be started, otherwise ``None``.
    meta:
        Optional dict with extra metadata (e.g. ``{"pid": 1234}``).
    """

    cmd: list[str]
    returncode: int | None
    timed_out: bool
    cancelled: bool
    start_ts: float
    end_ts: float
    duration_ms: int
    stdout: str
    stderr: str
    stdout_lines: list[str]
    stderr_lines: list[str]
    error: str | None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Return ``True`` if the command exited with code 0 and did not time out or cancel."""
        return (
            self.returncode == 0
            and not self.timed_out
            and not self.cancelled
            and self.error is None
        )
