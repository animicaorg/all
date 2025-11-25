"""
tests.harness.logging — structured logs for tests
=================================================

Lightweight, zero-dependency structured logging helpers tailored for the test
suite. Produces newline-delimited JSON by default, with contextual fields that
can be *bound* per test or per section.

Quick start (in tests/conftest.py)
----------------------------------
    from tests.harness.logging import setup_logging, bind, context, get_logger

    # Configure once for the test session
    setup_logging()

    # Bind common per-test context
    def pytest_runtest_setup(item):
        bind(test=item.name)

    # Or use a scoped context:
    with context(test="rpc_roundtrip", chain_id="omni-dev"):
        log = get_logger(__name__)
        log.info("starting test")

Environment variables
---------------------
TEST_LOG_LEVEL   : DEBUG|INFO|WARNING|ERROR (default: INFO)
TEST_LOG_FORMAT  : json|plain (default: json)
TEST_LOG_NOISY   : comma list of logger names to keep at chosen level.
                   (By default we quiet: asyncio, urllib3, httpx, websockets)
TEST_LOG_FILE    : path to log file (in addition to stderr)

APIs
----
- setup_logging(level: Optional[str] = None, fmt: str = "json", file: Optional[str] = None)
- get_logger(name: str) -> logging.Logger
- bind(**fields): merge fields into context (thread-task local)
- unbind(*keys): remove fields from context
- context(**fields): contextmanager to temporarily bind fields
- log_duration(event: str = "call", level: int = logging.INFO): decorator to log runtime

Notes
-----
- We avoid extra dependencies (no structlog) to keep test bootstrap snappy.
- All extras passed to logger calls (via 'extra={...}') are merged into the JSON.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional, Sequence

__all__ = [
    "setup_logging",
    "get_logger",
    "bind",
    "unbind",
    "context",
    "log_duration",
]

# ------------------------------------------------------------------------------
# Context handling (thread/task local via contextvars)
# ------------------------------------------------------------------------------

_CTX: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("_CTX", default={})


def _ctx_copy() -> Dict[str, Any]:
    d = _CTX.get()
    return dict(d) if d else {}


def bind(**fields: Any) -> None:
    """Bind additional fields into the contextual log dictionary."""
    d = _ctx_copy()
    d.update({k: v for k, v in fields.items() if v is not None})
    _CTX.set(d)


def unbind(*keys: str) -> None:
    """Remove fields from the contextual log dictionary."""
    if not keys:
        return
    d = _ctx_copy()
    for k in keys:
        d.pop(k, None)
    _CTX.set(d)


@contextlib.contextmanager
def context(**fields: Any) -> Iterator[None]:
    """Context manager to temporarily bind fields."""
    prev = _ctx_copy()
    try:
        bind(**fields)
        yield
    finally:
        _CTX.set(prev)


# ------------------------------------------------------------------------------
# JSON formatter & plumbing
# ------------------------------------------------------------------------------

def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _default_record_keys() -> set:
    dummy = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1, msg="m", args=(), exc_info=None
    )
    keys = set(dummy.__dict__.keys())
    # In some handlers, "message" is added later; account for it.
    keys.update({"message"})
    return keys


_DEFAULT_KEYS = _default_record_keys()


class _ContextFilter(logging.Filter):
    """Inject contextvars payload into the record as 'ctx'."""
    def filter(self, record: logging.LogRecord) -> bool:
        # Attach a copy so downstream modifications are harmless
        record.ctx = _ctx_copy()
        return True


class JsonFormatter(logging.Formatter):
    """Emit newline-delimited JSON."""

    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": os.getpid(),
            "tid": record.thread,
            "thread": record.threadName,
            "file": record.pathname,
            "func": record.funcName,
            "line": record.lineno,
        }

        # Attach bound context if present
        ctx = getattr(record, "ctx", None)
        if ctx:
            base["ctx"] = ctx

        # Merge extras (anything not in default keys)
        for k, v in record.__dict__.items():
            if k in _DEFAULT_KEYS or k == "ctx" or k.startswith("_"):
                continue
            # Skip internal/duplicate fields
            if k in ("args", "msg", "levelno", "created", "msecs", "relativeCreated"):
                continue
            base[k] = v

        # Exceptions / stack
        if record.exc_info:
            base["exc"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "stack": self.formatException(record.exc_info),
            }
        elif record.stack_info:
            base["stack"] = self.formatStack(record.stack_info)

        return json.dumps(base, default=str, separators=(",", ":"))


class PlainFormatter(logging.Formatter):
    """Human-friendly single-line formatter."""

    def format(self, record: logging.LogRecord) -> str:
        ts = _iso_utc(record.created)
        ctx = getattr(record, "ctx", None)
        ctx_str = f" ctx={json.dumps(ctx, default=str)}" if ctx else ""
        where = f"{record.module}:{record.funcName}:{record.lineno}"
        msg = record.getMessage()
        line = f"{ts} | {record.levelname:<8} | {record.name:<20} | {where:<30} | {msg}{ctx_str}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


_configured = False


def setup_logging(
    level: Optional[str] = None,
    fmt: str = "json",
    file: Optional[str] = None,
    quiet_default_noisy: bool = True,
) -> None:
    """
    Configure root logger if not already configured.

    Parameters
    ----------
    level : str | None
        Log level name; defaults to $TEST_LOG_LEVEL or "INFO".
    fmt : "json" | "plain"
        Output format; defaults to $TEST_LOG_FORMAT or "json".
    file : str | None
        If provided or $TEST_LOG_FILE set, also tee logs to this path.
    quiet_default_noisy : bool
        Reduce verbosity of chatty libs unless TEST_LOG_NOISY set.
    """
    global _configured
    if _configured:
        return

    env_level = (level or os.getenv("TEST_LOG_LEVEL") or "INFO").upper()
    env_fmt = (fmt or os.getenv("TEST_LOG_FORMAT") or "json").lower()
    log_file = file or os.getenv("TEST_LOG_FILE")

    root = logging.getLogger()
    root.setLevel(getattr(logging, env_level, logging.INFO))

    # Clear pre-existing handlers configured by pytest if any
    for h in list(root.handlers):
        root.removeHandler(h)

    handler_stderr = logging.StreamHandler(stream=sys.stderr)
    handler_stderr.addFilter(_ContextFilter())
    if env_fmt == "plain":
        handler_stderr.setFormatter(PlainFormatter())
    else:
        handler_stderr.setFormatter(JsonFormatter())
    root.addHandler(handler_stderr)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.addFilter(_ContextFilter())
        fh.setFormatter(JsonFormatter() if env_fmt == "json" else PlainFormatter())
        root.addHandler(fh)

    # Tame noisy dependencies unless explicitly overridden
    if quiet_default_noisy and not os.getenv("TEST_LOG_NOISY"):
        for n in ("asyncio", "urllib3", "httpx", "websockets"):
            logging.getLogger(n).setLevel(max(root.level, logging.WARNING))
    else:
        noisy = [s.strip() for s in os.getenv("TEST_LOG_NOISY", "").split(",") if s.strip()]
        for n in noisy:
            logging.getLogger(n).setLevel(root.level)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Fetch a named logger (root must be configured via setup_logging())."""
    return logging.getLogger(name)


# ------------------------------------------------------------------------------
# Convenience decorator: duration logging
# ------------------------------------------------------------------------------

def log_duration(event: str = "call", level: int = logging.INFO):
    """
    Decorator to log start/finish with wall time duration (ms).
    Usage:
        @log_duration("deploy_tx")
        def deploy(...): ...
    Emits:
        {"event":"deploy_tx","phase":"start",...}
        {"event":"deploy_tx","phase":"finish","ms":12.34,...}
    """
    def _wrap(fn):
        log = get_logger(f"{fn.__module__}.{fn.__name__}")

        def _now_ms() -> float:
            return time.perf_counter() * 1000.0

        def wrapper(*args, **kwargs):
            log.log(level, f"{event}: start", extra={"event": event, "phase": "start"})
            t0 = _now_ms()
            try:
                result = fn(*args, **kwargs)
                ms = _now_ms() - t0
                log.log(level, f"{event}: finish in {ms:.2f}ms",
                        extra={"event": event, "phase": "finish", "ms": round(ms, 3)})
                return result
            except Exception as e:
                ms = _now_ms() - t0
                log.error(f"{event}: error after {ms:.2f}ms",
                          extra={"event": event, "phase": "error", "ms": round(ms, 3), "error": str(e)},
                          exc_info=True)
                raise
        # Preserve metadata for pytest introspection
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__qualname__ = fn.__qualname__
        return wrapper
    return _wrap
