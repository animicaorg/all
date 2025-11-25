from __future__ import annotations

"""
Structured logging setup for Studio Services.

This module configures **structlog** + the stdlib ``logging`` package so that:
- All logs (including Uvicorn / FastAPI / libraries) are emitted as structured JSON
  by default (or as a pretty console renderer in dev).
- Context variables (e.g., request id) are automatically merged into each event.
- Exceptions include a structured stack trace.
- Log level & format are configurable via environment variables.

Quick start
-----------
    from studio_services.logging import setup_logging, get_logger

    setup_logging(service_name="studio-services")  # call once on process start
    log = get_logger(__name__)
    log.info("server_started", port=8080)

Environment
-----------
- LOG_LEVEL: one of DEBUG, INFO, WARNING, ERROR (default: INFO)
- LOG_FORMAT: "json" (default) or "console"
- LOG_INCLUDE_STACKTRACE: "1" to include stack traces in logs (default: 1 for json, 0 for console)
"""

import logging
import logging.config
import os
from typing import Any, Dict, Iterable, Optional

import structlog
from structlog.contextvars import merge_contextvars
from structlog.processors import JSONRenderer


# ------------------------------ Redaction ------------------------------------


REDACT_KEYS = {"authorization", "token", "access_token", "refresh_token", "password", "secret", "api_key"}


def _redact_secrets(_: logging.Logger, __: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Processor that redacts sensitive values for well-known keys.
    """
    for k in list(event_dict.keys()):
        if k.lower() in REDACT_KEYS and event_dict[k] is not None:
            event_dict[k] = "***"
    return event_dict


# ------------------------------ Setup ----------------------------------------


def _base_processors(service_name: str, include_stacktrace: bool) -> Iterable:
    yield structlog.stdlib.add_log_level
    yield structlog.processors.TimeStamper(fmt="iso", utc=True)
    yield merge_contextvars  # pull in contextvars bound elsewhere (e.g., request_id)
    yield structlog.processors.StackInfoRenderer()
    if include_stacktrace:
        yield structlog.processors.format_exc_info
    yield _redact_secrets
    yield structlog.processors.UnicodeDecoder()
    # Add service name if not present
    def _ensure_service(_: logging.Logger, __: str, ev: Dict[str, Any]) -> Dict[str, Any]:
        ev.setdefault("service", service_name)
        return ev

    yield _ensure_service


def setup_logging(
    *,
    service_name: str = "studio-services",
    level: Optional[str | int] = None,
    log_format: Optional[str] = None,
    include_stacktrace: Optional[bool] = None,
) -> None:
    """
    Configure structlog + stdlib logging. Safe to call once at process start.

    Parameters
    ----------
    service_name: str
        Value injected as "service" into every event.
    level: str|int
        Log level (e.g., "INFO"). Defaults to $LOG_LEVEL or INFO.
    log_format: str
        "json" (default) or "console". Defaults to $LOG_FORMAT or "json".
    include_stacktrace: bool
        Include stack traces for log records with exc_info. Defaults to:
        - True for JSON
        - False for console
        Or $LOG_INCLUDE_STACKTRACE if defined ("1"/"0").
    """
    env_level = os.getenv("LOG_LEVEL", "").upper() or None
    env_format = os.getenv("LOG_FORMAT", "").lower() or None
    env_stack = os.getenv("LOG_INCLUDE_STACKTRACE")

    level = level or env_level or "INFO"
    log_format = (log_format or env_format or "json").lower()
    if include_stacktrace is None:
        if env_stack is not None:
            include_stacktrace = env_stack.strip() in ("1", "true", "yes", "on")
        else:
            include_stacktrace = log_format == "json"

    # Build processor chains
    processors = list(_base_processors(service_name, include_stacktrace))

    if log_format == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=True, sort_keys=False)
    else:
        renderer = JSONRenderer(sort_keys=True)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            *processors,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging to route through ProcessorFormatter-compatible handler.
    shared_handler = logging.StreamHandler()
    shared_handler.setLevel(logging.DEBUG)

    # Use ProcessorFormatter to let stdlib logs go through the same processors/renderer.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            *processors,
        ],
    )
    shared_handler.setFormatter(formatter)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)
    # Clear default handlers that frameworks add (if any) to avoid duplicates
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(shared_handler)

    # Uvicorn / FastAPI loggers harmonization
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn", "gunicorn.error", "gunicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [shared_handler]
        lg.propagate = False
        lg.setLevel(level)

    # Silence noisy loggers slightly (keep overridable via env if needed)
    logging.getLogger("asyncio").setLevel(os.getenv("LOG_LEVEL_ASYNCIO", "WARNING"))
    logging.getLogger("httpcore").setLevel(os.getenv("LOG_LEVEL_HTTPCORE", "WARNING"))
    logging.getLogger("httpx").setLevel(os.getenv("LOG_LEVEL_HTTPX", "WARNING"))


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger; bind module name if provided.
    """
    log = structlog.get_logger()
    if name:
        return log.bind(logger=name)
    return log


# ------------------------------ Context helpers -------------------------------

def bind_request_context(**kv: Any) -> None:
    """
    Bind request-scoped key/value pairs into the structlog contextvars store.
    Typical keys: request_id, method, path, client_ip, user_agent
    """
    structlog.contextvars.bind_contextvars(**kv)


def clear_request_context(*keys: str) -> None:
    """
    Clear specific keys from contextvars, or clear all if no keys provided.
    """
    if keys:
        structlog.contextvars.unbind_contextvars(*keys)
    else:
        structlog.contextvars.clear_contextvars()


__all__ = [
    "setup_logging",
    "get_logger",
    "bind_request_context",
    "clear_request_context",
]
