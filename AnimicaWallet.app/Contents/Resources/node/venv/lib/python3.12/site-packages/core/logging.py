"""
Animica — core.logging
----------------------

Structured logging with:
- JSON or concise colored text formats
- Context-local fields via `contextvars` (trace_id, chain_id, component, peer, etc.)
- Safe JSON serialization (datetimes, bytes → hex, Paths → str)
- Simple, dependency-free setup (stdlib only)
- Helpers to bind/unbind context fields and generate trace IDs
- Optional file logging

Usage
-----
    from core import logging as clog

    clog.configure(json=False, level="INFO")  # once at process start
    log = clog.get_logger(__name__)

    with clog.trace_scope():  # ensures a trace_id for this scope
        clog.bind(component="boot")
        log.info("node starting", cfg_loaded=True)

        try:
            ...
        except Exception:
            log.exception("fatal during boot")

Integrations
------------
- RPC / FastAPI middleware can call `trace_scope(trace_id)` per-request.
- P2P handlers can `bind(peer=peer_id)` per-connection.
- Miner can `bind(component="miner", device="cpu")` for its thread.

This module purposefully uses only the stdlib to be available very early.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import os
import sys
import threading
import traceback
import types
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

# ----------------------------
# Context
# ----------------------------

# Context fields carried across async tasks/threads if copied properly by caller.
_LOG_CONTEXT: ContextVar[Dict[str, Any]] = ContextVar("_LOG_CONTEXT", default={})

DEFAULT_CONTEXT_KEYS = (
    "trace_id",
    "chain_id",
    "component",
    "peer",
    "node_id",
    "height",
    "topic",
)


def context() -> Dict[str, Any]:
    """Return a *copy* of the active logging context."""
    return dict(_LOG_CONTEXT.get())


def bind(**fields: Any) -> None:
    """Merge fields into the active context."""
    cur = dict(_LOG_CONTEXT.get())
    cur.update({k: _coerce_value(v) for k, v in fields.items()})
    _LOG_CONTEXT.set(cur)


def unbind(*keys: str) -> None:
    cur = dict(_LOG_CONTEXT.get())
    for k in keys:
        cur.pop(k, None)
    _LOG_CONTEXT.set(cur)


def clear_context() -> None:
    _LOG_CONTEXT.set({})


def ensure_trace_id() -> str:
    cur = _LOG_CONTEXT.get()
    tid = cur.get("trace_id")
    if not tid:
        tid = short_uuid()
        bind(trace_id=tid)
    return tid


@contextmanager
def trace_scope(trace_id: Optional[str] = None):
    """
    Context manager that ensures a trace_id is present for the duration
    of the scope. Restores prior context on exit.
    """
    prev = dict(_LOG_CONTEXT.get())
    try:
        bind(trace_id=trace_id or short_uuid())
        yield
    finally:
        _LOG_CONTEXT.set(prev)


def short_uuid() -> str:
    # 12 hex chars (48 bits of randomness) is human-friendly and unique enough for tracing.
    return uuid.uuid4().hex[:12]


# ----------------------------
# JSON & Text formatters
# ----------------------------


class _SafeJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:  # type: ignore[override]
        try:
            if isinstance(o, (bytes, bytearray)):
                return o.hex()
            if isinstance(o, (Path,)):
                return str(o)
            if isinstance(o, (_dt.datetime,)):
                if o.tzinfo is None:
                    o = o.replace(tzinfo=_dt.timezone.utc)
                return o.isoformat()
            if isinstance(o, _dt.date):
                return o.isoformat()
            if is_dataclass(o):
                return asdict(o)
            if hasattr(o, "__dict__"):
                return vars(o)
            return str(o)
        except Exception:
            return f"<nonserializable:{type(o).__name__}>"


def _utcnow_iso() -> str:
    return (
        _dt.datetime.utcnow()
        .replace(tzinfo=_dt.timezone.utc)
        .isoformat(timespec="milliseconds")
    )


_LEVEL_TO_INT = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}

ANSI = types.SimpleNamespace(
    RESET="\x1b[0m",
    DIM="\x1b[2m",
    BOLD="\x1b[1m",
    FG=types.SimpleNamespace(
        RED="\x1b[31m",
        GREEN="\x1b[32m",
        YELLOW="\x1b[33m",
        BLUE="\x1b[34m",
        MAGENTA="\x1b[35m",
        CYAN="\x1b[36m",
        GREY="\x1b[90m",
        WHITE="\x1b[37m",
    ),
)

_LEVEL_COLOR = {
    logging.DEBUG: ANSI.FG.GREY,
    logging.INFO: ANSI.FG.GREEN,
    logging.WARNING: ANSI.FG.YELLOW,
    logging.ERROR: ANSI.FG.RED,
    logging.CRITICAL: ANSI.BOLD + ANSI.FG.MAGENTA,
}


def _supports_color(stream: io.TextIOBase) -> bool:
    try:
        return stream.isatty() and os.environ.get("NO_COLOR") is None
    except Exception:
        return False


def _coerce_value(v: Any) -> Any:
    # Keep basic JSON types as-is; coerce objects to readable forms.
    if v is None or isinstance(v, (bool, int, float, str, list, dict)):
        return v
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, (_dt.datetime,)):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.isoformat()
    if isinstance(v, _dt.date):
        return v.isoformat()
    if is_dataclass(v):
        return asdict(v)
    return str(v)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": _utcnow_iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": os.getpid(),
            "tid": threading.get_ident(),
        }
        # Merge context
        payload.update({k: _coerce_value(v) for k, v in context().items()})

        # Collect structured extras (fields passed via LoggerAdapter or log(..., extra={...}))
        for k, v in record.__dict__.items():
            if k.startswith("_"):  # internals
                continue
            if k in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            ):
                continue
            if k in payload:
                continue
            payload[k] = _coerce_value(v)

        if record.exc_info:
            payload["err"] = "".join(
                traceback.format_exception(*record.exc_info)
            ).rstrip()

        return json.dumps(payload, cls=_SafeJSONEncoder, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    """
    Human-friendly one-liner:
      2025-01-05T12:34:56.789Z | INFO  | core.rpc.server   | trace=abc123 node=Qm.. | serving on :8547
    With colors when supported.
    """

    def __init__(self, stream: io.TextIOBase):
        super().__init__()
        self._color = _supports_color(stream)

    def format(self, record: logging.LogRecord) -> str:
        ts = _utcnow_iso()
        lvl = record.levelname
        name = record.name

        ctx = context()
        ctx_str_parts: list[str] = []
        for k in DEFAULT_CONTEXT_KEYS:
            v = ctx.get(k)
            if v is not None:
                ctx_str_parts.append(f"{k}={v}")
        ctx_str = " ".join(ctx_str_parts)

        msg = record.getMessage()
        # Inline selected extras (beyond context)
        extras_parts: list[str] = []
        for k, v in record.__dict__.items():
            if k.startswith("_") or k in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            ):
                continue
            if k in DEFAULT_CONTEXT_KEYS or k in ctx:
                continue
            extras_parts.append(f"{k}={_coerce_value(v)}")
        extras = (" " + " ".join(extras_parts)) if extras_parts else ""

        # Colors
        if self._color:
            c = _LEVEL_COLOR.get(record.levelno, ANSI.FG.WHITE)
            lvl_s = f"{c}{lvl:<5}{ANSI.RESET}"
            name_s = f"{ANSI.FG.CYAN}{name}{ANSI.RESET}"
            ts_s = f"{ANSI.FG.GREY}{ts}{ANSI.RESET}"
            ctx_s = f"{ANSI.FG.GREY}{ctx_str}{ANSI.RESET}" if ctx_str else ""
        else:
            lvl_s = f"{lvl:<5}"
            name_s = name
            ts_s = ts
            ctx_s = ctx_str

        line = f"{ts_s} | {lvl_s} | {name_s}"
        if ctx_s:
            line += f" | {ctx_s}"
        if extras:
            line += f"{extras}"
        line += f" | {msg}"

        if record.exc_info:
            tb = "".join(traceback.format_exception(*record.exc_info)).rstrip()
            line += "\n" + tb
        return line


# ----------------------------
# Public setup API
# ----------------------------


def configure(
    *,
    json: Optional[bool] = None,
    level: str | int = "INFO",
    stream: io.TextIOBase = sys.stderr,
    file_path: Optional[Path | str] = None,
    propagate_existing: bool = False,
) -> None:
    """
    Configure the root logger.

    Parameters
    ----------
    json : bool | None
        If None, determined by env ANIMICA_LOG_FORMAT=(json|text) and TTY detection.
    level : str | int
        Minimum log level.
    stream : TextIO
        Stream for console handler (default: stderr).
    file_path : Path | str | None
        Optional path to a file to additionally write JSON logs (always JSON for machine-readability).
    propagate_existing : bool
        If True, leave existing handlers and only adjust formatters/levels. Defaults to false (fresh config).
    """
    chosen_json = _decide_json(json, stream)

    # Root logger
    root = logging.getLogger()
    root.setLevel(_coerce_level(level))

    if not propagate_existing:
        for h in list(root.handlers):
            root.removeHandler(h)

    # Console handler
    console = logging.StreamHandler(stream)
    console.setLevel(_coerce_level(level))
    if chosen_json:
        console.setFormatter(JSONFormatter())
    else:
        console.setFormatter(TextFormatter(stream))
    root.addHandler(console)

    # Optional file handler (always JSON for easy ingestion)
    if file_path:
        p = Path(file_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(p, encoding="utf-8")
        fh.setLevel(_coerce_level(level))
        fh.setFormatter(JSONFormatter())
        root.addHandler(fh)

    # Quiet some noisy libs if present
    for noisy in ("urllib3", "asyncio", "websockets"):
        logging.getLogger(noisy).setLevel(max(_coerce_level(level), logging.WARNING))


def setup_logging(
    *,
    level: str | int = "INFO",
    fmt: str = "json",
    file: Optional[Path | str] = None,
    stream: io.TextIOBase = sys.stderr,
    propagate_existing: bool = False,
) -> None:
    """
    Backwards-compatible wrapper for configuring logging.

    Parameters
    ----------
    level : str | int
        Minimum log level (default: "INFO").
    fmt : "json" | "text"
        Output format. Environment variable ANIMICA_LOG_FORMAT takes precedence.
    file : Path | str | None
        Optional path to tee machine-readable JSON logs to a file.
    stream : TextIO
        Stream for console handler (default: stderr).
    propagate_existing : bool
        If True, leave existing handlers in place.
    """
    override = _env_json_override()
    fmt_lower = fmt.strip().lower()
    chosen_json = override if override is not None else fmt_lower == "json"

    configure(
        json=chosen_json,
        level=level,
        stream=stream,
        file_path=file,
        propagate_existing=propagate_existing,
    )


def configure_from_core_config(cfg: Any) -> None:
    """
    Convenience wrapper to configure logging based on `core.config.Config`.
    Uses cfg.paths.logs_dir / 'node.log' for file output.
    """
    logs_dir = Path(getattr(cfg.paths, "logs_dir", Path.cwd()))
    log_file = logs_dir / "node.log"
    bind(chain_id=getattr(cfg.chain, "chain_id", None))
    configure(
        json=_env_json_override(),
        level=os.environ.get("ANIMICA_LOG_LEVEL", "INFO"),
        file_path=log_file,
    )


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a standard logger (root child). To add constant per-logger fields, use `with_fields`.
    """
    return logging.getLogger(name or "animica")


def with_fields(logger: logging.Logger, **fields: Any) -> "ContextAdapter":
    """Return a logger adapter that injects constant fields on each call."""
    return ContextAdapter(
        logger, extra={k: _coerce_value(v) for k, v in fields.items()}
    )


class ContextAdapter(logging.LoggerAdapter):
    """
    LoggerAdapter that injects:
      - Active contextvars (trace_id, chain_id, ...)
      - Adapter's .extra (constant fields)
      - kwargs passed via log(..., extra={'k': 'v'})
    """

    def process(self, msg: Any, kwargs: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        # Ensure extra dict exists
        extra = kwargs.get("extra") or {}
        # Merge adapter extras without clobbering call-site extras
        merged = (
            {**self.extra, **extra} if isinstance(extra, dict) else dict(self.extra)
        )
        kwargs["extra"] = merged
        return msg, kwargs


# ----------------------------
# Internals
# ----------------------------


def _coerce_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return _LEVEL_TO_INT.get(level.upper(), logging.INFO)


def _decide_json(json_flag: Optional[bool], stream: io.TextIOBase) -> bool:
    if json_flag is not None:
        return json_flag
    env = os.environ.get("ANIMICA_LOG_FORMAT", "").strip().lower()
    if env in ("json", "text"):
        return env == "json"
    # Default: JSON in non-tty (services), text when interactive TTY
    return not _supports_color(stream)


def _env_json_override() -> Optional[bool]:
    env = os.environ.get("ANIMICA_LOG_FORMAT", "").strip().lower()
    if env == "json":
        return True
    if env == "text":
        return False
    return None


# If this module is executed directly: quick smoke demo
if __name__ == "__main__":
    configure(json=False, level="DEBUG")
    log = get_logger("core.logging.demo")
    with trace_scope():
        bind(component="demo", node_id="N1")
        log.debug("debug line", counter=1)
        log.info("hello", user="alice")
        try:
            1 / 0
        except Exception:
            log.exception("boom")
        unbind("user")
        log.warning("after unbind")
