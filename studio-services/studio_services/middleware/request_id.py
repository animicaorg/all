from __future__ import annotations

"""
Request ID & tracing middleware.

Features
--------
- Generates or propagates a stable **X-Request-Id** for every request.
- Supports the W3C **traceparent** header:
  - If present, preserves the incoming trace-id and assigns a new span-id.
  - If absent, creates a fresh trace-id/span-id pair.
- Exposes IDs on `request.state` for handlers:
    request.state.request_id
    request.state.trace_id
    request.state.span_id
    request.state.parent_span_id
    request.state.correlation_id
- Adds response headers:
    X-Request-Id, traceparent, (optionally) X-Correlation-Id (if inbound)
- Integrates with `structlog.contextvars` if available (no hard dependency).

Usage
-----
    from fastapi import FastAPI
    from studio_services.middleware.request_id import install_request_id_middleware

    app = FastAPI()
    install_request_id_middleware(app)

Configuration
-------------
You can override header names by passing kwargs to `install_request_id_middleware`:
    install_request_id_middleware(app, request_id_header="X-Req-Id")

Security Notes
--------------
Request/trace IDs are *not secrets*. They are safe to echo in logs and headers.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# --------------------------- helpers ---------------------------

_TRACEPARENT_RE = re.compile(
    r"^(?P<ver>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def _parse_traceparent(value: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse W3C traceparent. Returns (trace_id, parent_span_id, flags) if valid, else None.
    """
    m = _TRACEPARENT_RE.match(value.strip())
    if not m:
        return None
    ver = m.group("ver")
    # Only version 00 is supported; still accept any hex to be lenient.
    trace_id = m.group("trace_id")
    span_id = m.group("span_id")
    flags = m.group("flags")
    # Disallow all-zero IDs per spec
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return (trace_id, span_id, flags)


def _new_trace_ids(sampled: bool = True) -> Tuple[str, str, str]:
    """
    Return (trace_id, span_id, flags) hex strings per W3C.
    """
    trace_id = secrets.token_hex(16)  # 16 bytes = 32 hex
    span_id = secrets.token_hex(8)    # 8 bytes = 16 hex
    flags = "01" if sampled else "00"
    return trace_id, span_id, flags


def _format_traceparent(trace_id: str, span_id: str, flags: str = "01") -> str:
    return f"00-{trace_id}-{span_id}-{flags}"


def _maybe_bind_structlog(**kv: str) -> None:
    """
    Bind IDs to structlog's contextvars if available. No-op if structlog missing.
    """
    try:
        from structlog.contextvars import bind_contextvars  # type: ignore
    except Exception:
        return
    try:
        # Filter out none/empty to avoid noise.
        payload = {k: v for k, v in kv.items() if v}
        if payload:
            bind_contextvars(**payload)
    except Exception:
        pass


def _maybe_unbind_structlog(*keys: str) -> None:
    try:
        from structlog.contextvars import unbind_contextvars  # type: ignore
    except Exception:
        return
    try:
        unbind_contextvars(*keys)
    except Exception:
        pass


# --------------------------- middleware ---------------------------

@dataclass(frozen=True)
class RequestIdConfig:
    request_id_header: str = "X-Request-Id"
    correlation_id_header: str = "X-Correlation-Id"
    traceparent_header: str = "traceparent"
    # Whether to echo X-Correlation-Id back when present
    echo_correlation_id: bool = True


class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that manages request/trace IDs and propagates headers.
    """

    def __init__(self, app, config: Optional[RequestIdConfig] = None):
        super().__init__(app)
        self.cfg = config or RequestIdConfig()

        # Normalize header names to canonical outbound case for responses
        self._resp_request_id = self.cfg.request_id_header
        self._resp_correlation_id = self.cfg.correlation_id_header
        self._resp_traceparent = self.cfg.traceparent_header

        # Inbound lookups are case-insensitive
        self._in_request_id = self.cfg.request_id_header.lower()
        self._in_correlation_id = self.cfg.correlation_id_header.lower()
        self._in_traceparent = self.cfg.traceparent_header.lower()

    async def dispatch(self, request: Request, call_next):
        # ----- Inbound: extract/generate IDs -----
        headers = request.headers
        req_id = headers.get(self._in_request_id)
        corr_id = headers.get(self._in_correlation_id)

        # Prefer inbound request-id, else generate a new one
        if not req_id:
            # Use uuid4 hex (32 chars) for simplicity & readability
            req_id = uuid.uuid4().hex

        # W3C traceparent: propagate or start a new trace
        parent = headers.get(self._in_traceparent)
        parsed = _parse_traceparent(parent) if parent else None
        if parsed:
            trace_id, parent_span_id, flags = parsed
            span_id = secrets.token_hex(8)
        else:
            trace_id, span_id, flags = _new_trace_ids(sampled=True)
            parent_span_id = ""

        # Stash in request.state for handlers
        request.state.request_id = req_id
        request.state.correlation_id = corr_id
        request.state.trace_id = trace_id
        request.state.span_id = span_id
        request.state.parent_span_id = parent_span_id

        # Bind to structlog context (best-effort)
        _maybe_bind_structlog(
            request_id=req_id,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id or "",
            correlation_id=corr_id or "",
        )

        # ----- Call downstream -----
        try:
            response: Response = await call_next(request)
        finally:
            # Always unbind to avoid leaking context across tasks
            _maybe_unbind_structlog("request_id", "trace_id", "span_id", "parent_span_id", "correlation_id")

        # ----- Outbound: set headers -----
        response.headers[self._resp_request_id] = req_id
        response.headers[self._resp_traceparent] = _format_traceparent(trace_id, span_id, flags)
        if self.cfg.echo_correlation_id and corr_id:
            response.headers[self._resp_correlation_id] = corr_id

        return response


# --------------------------- install helper ---------------------------

def install_request_id_middleware(
    app: FastAPI,
    *,
    request_id_header: str = "X-Request-Id",
    correlation_id_header: str = "X-Correlation-Id",
    traceparent_header: str = "traceparent",
    echo_correlation_id: bool = True,
) -> RequestIdConfig:
    """
    Convenience helper to install the middleware with optional header overrides.
    Returns the effective config.
    """
    cfg = RequestIdConfig(
        request_id_header=request_id_header,
        correlation_id_header=correlation_id_header,
        traceparent_header=traceparent_header,
        echo_correlation_id=echo_correlation_id,
    )
    app.add_middleware(RequestIdMiddleware, config=cfg)
    return cfg


__all__ = [
    "RequestIdConfig",
    "RequestIdMiddleware",
    "install_request_id_middleware",
]
