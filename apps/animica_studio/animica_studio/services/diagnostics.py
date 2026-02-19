"""Diagnostics service: centralised ring buffer for errors and recent logs.

Architecture
------------
* ``Diagnostics`` holds two :class:`RingBuffer` instances:
  - ``_events``: :class:`DiagnosticEvent` objects (max 200 by default).
  - ``_log_lines``: raw log strings (max 500 by default).
* A ``DiagnosticsHandler`` subclasses ``logging.Handler`` and feeds ERROR/WARN
  log records into the singleton :data:`diagnostics`.
* A module-level singleton is provided for convenience, but the class is fully
  injectable for unit tests.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from animica_studio.models.diagnostics_models import DiagnosticEvent, RingBuffer

log = logging.getLogger(__name__)

_DEFAULT_EVENT_CAPACITY = 200
_DEFAULT_LOG_CAPACITY = 500


class Diagnostics:
    """Centralised diagnostics service (ring-buffer backed)."""

    def __init__(
        self,
        event_capacity: int = _DEFAULT_EVENT_CAPACITY,
        log_capacity: int = _DEFAULT_LOG_CAPACITY,
    ) -> None:
        self._events: RingBuffer[DiagnosticEvent] = RingBuffer(event_capacity)
        self._log_lines: RingBuffer[str] = RingBuffer(log_capacity)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_error(
        self,
        source: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        event = DiagnosticEvent.make("ERROR", source, message, context)
        self._events.append(event)

    def record_warn(
        self,
        source: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        event = DiagnosticEvent.make("WARN", source, message, context)
        self._events.append(event)

    def record_info(
        self,
        source: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        event = DiagnosticEvent.make("INFO", source, message, context)
        self._events.append(event)

    def record_log_line(self, line: str) -> None:
        """Append a raw log line to the recent-logs ring buffer."""
        self._log_lines.append(line)

    def record_from_log_record(self, record: logging.LogRecord) -> None:
        """Feed a :class:`logging.LogRecord` into the appropriate ring buffer."""
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)

        formatted = f"{record.levelname} {record.name}: {msg}"
        self._log_lines.append(formatted)

        level = record.levelname
        if level in ("ERROR", "CRITICAL"):
            self._events.append(
                DiagnosticEvent(
                    ts=record.created,
                    level="ERROR",
                    source=record.name,
                    message=msg,
                    context={"levelno": record.levelno},
                )
            )
        elif level == "WARNING":
            self._events.append(
                DiagnosticEvent(
                    ts=record.created,
                    level="WARN",
                    source=record.name,
                    message=msg,
                    context={"levelno": record.levelno},
                )
            )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_events(self, last_n: int | None = None) -> list[DiagnosticEvent]:
        """Return diagnostic events, newest last."""
        items = self._events.items()
        if last_n is not None:
            items = items[-last_n:]
        return items

    def get_recent_logs(self, last_n: int | None = None) -> list[str]:
        """Return recent log lines, newest last."""
        lines = self._log_lines.items()
        if last_n is not None:
            lines = lines[-last_n:]
        return lines

    def ingest_node_log_tail(self, log_path: str, last_n: int = 100) -> None:
        """Read the last *last_n* lines from a node log file into the log buffer."""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            for line in lines[-last_n:]:
                self._log_lines.append(line.rstrip("\n"))
        except OSError:
            pass

    def clear(self) -> None:
        """Clear all buffers (useful for testing)."""
        self._events.clear()
        self._log_lines.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Module-level :class:`Diagnostics` singleton.  Import and use directly, or
#: override in tests by reassigning this name.
diagnostics: Diagnostics = Diagnostics()


# ---------------------------------------------------------------------------
# Logging handler
# ---------------------------------------------------------------------------


class DiagnosticsHandler(logging.Handler):
    """A :class:`logging.Handler` that feeds records into a :class:`Diagnostics` instance.

    Attach this to the root logger to capture all log output.
    """

    def __init__(self, diag: Diagnostics | None = None, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self._diag = diag or diagnostics

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._diag.record_from_log_record(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)
