from __future__ import annotations

"""
Access logging middleware with durations & sizes.

- One concise structured log line per request.
- Captures: method, path, route, status, latency_ms, rx_bytes, tx_bytes,
  client_ip, user_agent, referer, request_id, trace_id.
- Uses structlog if available; falls back to stdlib logging.
- Counts streamed responses when Content-Length is absent.

Install:
    from fastapi import FastAPI
    from studio_services.middleware.logging import install_access_log_middleware

    app = FastAPI()
    install_access_log_middleware(app)
"""

import logging
import time
from typing import AsyncIterator, Callable, Optional, Tuple

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# --------------------------- logger helpers ---------------------------


def _get_logger():
    try:
        import structlog  # type: ignore

        return structlog.get_logger("access")
    except Exception:
        return logging.getLogger("studio_services.access")


log = _get_logger()


def _state_id(request: Request, name: str) -> str:
    return getattr(request.state, name, "") or ""


def _client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For (first hop), then X-Real-IP, then ASGI client addr
    fwd = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    real = request.headers.get("x-real-ip") or request.headers.get("X-Real-Ip")
    if real:
        return real.strip()
    if request.client:
        return request.client.host
    return ""


def _route_template(request: Request) -> str:
    # Try to fetch FastAPI route path template for stable cardinality
    route = request.scope.get("route")
    if route is None:
        return ""
    # fastapi routing exposes .path or .path_format depending on version
    return getattr(route, "path_format", None) or getattr(route, "path", "") or ""


# --------------------------- counting wrapper ---------------------------


class _CountingIterator:
    """
    Wrap a response body iterator to count bytes yielded without materializing it.
    """

    def __init__(self, inner):
        self.inner = inner
        self.count = 0

    async def __aiter__(self):
        async for chunk in self.inner:
            if isinstance(chunk, (bytes, bytearray)):
                self.count += len(chunk)
            elif isinstance(chunk, str):
                self.count += len(chunk.encode("utf-8"))
            yield chunk


# --------------------------- middleware ---------------------------


class AccessLogMiddleware(BaseHTTPMiddleware):
    """
    Emit a structured access log for every request.
    """

    def __init__(self, app, *, redact_headers: Optional[Tuple[str, ...]] = None):
        super().__init__(app)
        self.redact_headers = tuple((h.lower() for h in (redact_headers or ())))  # reserved for future

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        start_ns = time.perf_counter_ns()

        method = request.method
        path = request.url.path
        query = request.url.query
        # Avoid large log lines for long queries; keep a tiny hint
        qs = f"?{query}" if query and len(query) <= 256 else ("?…" if query else "")
        ua = request.headers.get("user-agent", "")
        ref = request.headers.get("referer", "")

        rx_bytes = 0
        try:
            rx_bytes = int(request.headers.get("content-length") or 0)
        except Exception:
            rx_bytes = 0

        client_ip = _client_ip(request)
        route_tmpl = _route_template(request)

        # IDs from request_id middleware, if installed
        request_id = _state_id(request, "request_id")
        trace_id = _state_id(request, "trace_id")

        status = 500
        tx_bytes = 0
        counted_iter: Optional[_CountingIterator] = None
        response: Response

        try:
            response = await call_next(request)

            # Determine tx bytes
            try:
                tx_bytes = int(response.headers.get("content-length") or 0)
            except Exception:
                tx_bytes = 0

            if tx_bytes == 0 and getattr(response, "body_iterator", None) is not None:
                # Wrap streaming iterator to count on the fly
                counted_iter = _CountingIterator(response.body_iterator)
                response.body_iterator = counted_iter  # type: ignore[attr-defined]

            status = response.status_code
        except Exception as exc:
            # Ensure duration is recorded even if unhandled; re-raise to be mapped by error middleware
            status = 500
            end_ns = time.perf_counter_ns()
            latency_ms = (end_ns - start_ns) / 1e6
            _log_line(
                level="error",
                method=method,
                path=path,
                qs=qs,
                route_tmpl=route_tmpl,
                status=status,
                latency_ms=latency_ms,
                rx_bytes=rx_bytes,
                tx_bytes=tx_bytes,
                client_ip=client_ip,
                ua=ua,
                ref=ref,
                request_id=request_id,
                trace_id=trace_id,
                exc=exc,
            )
            raise

        # If we wrapped the iterator, we need to finalize logging after the response has been sent.
        async def _send_with_finalize(send):
            nonlocal tx_bytes
            await response(scope=request.scope, receive=request.receive, send=send)  # type: ignore[misc]
            if counted_iter is not None:
                tx_bytes = counted_iter.count
            end_ns = time.perf_counter_ns()
            latency_ms = (end_ns - start_ns) / 1e6
            _log_line(
                level=_level_for_status(status),
                method=method,
                path=path,
                qs=qs,
                route_tmpl=route_tmpl,
                status=status,
                latency_ms=latency_ms,
                rx_bytes=rx_bytes,
                tx_bytes=tx_bytes,
                client_ip=client_ip,
                ua=ua,
                ref=ref,
                request_id=request_id,
                trace_id=trace_id,
                exc=None,
            )

        # Starlette Response.__call__ consumes the body iterator; to hook after send we wrap in a lightweight sender.
        # We return a small Response that delegates to our custom send function.
        class _ProxyResponse(Response):
            async def __call__(self, scope, receive, send) -> None:  # type: ignore[override]
                await _send_with_finalize(send)

        # Preserve headers/status/body already set on `response`
        prox = _ProxyResponse(
            content=b"",  # unused; we'll delegate
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )
        # Copy cookies
        for cookie in response.raw_headers:
            # raw_headers contain tuples like (b'set-cookie', b'...'); the constructor above already copied headers.
            # Nothing else required here.
            pass
        return prox


def _level_for_status(status: int) -> str:
    if status >= 500:
        return "error"
    if status >= 400:
        return "warning"
    return "info"


def _log_line(
    *,
    level: str,
    method: str,
    path: str,
    qs: str,
    route_tmpl: str,
    status: int,
    latency_ms: float,
    rx_bytes: int,
    tx_bytes: int,
    client_ip: str,
    ua: str,
    ref: str,
    request_id: str,
    trace_id: str,
    exc: Optional[BaseException],
) -> None:
    payload = {
        "method": method,
        "path": path,
        "route": route_tmpl or "",
        "status": status,
        "latency_ms": round(latency_ms, 3),
        "rx_bytes": rx_bytes,
        "tx_bytes": tx_bytes,
        "client_ip": client_ip,
        "user_agent": ua,
        "referer": ref,
        "request_id": request_id,
        "trace_id": trace_id,
    }

    if hasattr(log, "bind"):
        # structlog: enrich per-event context
        logger = log.bind(**{k: v for k, v in payload.items() if v != ""})
        if level == "error":
            if exc is not None:
                logger.exception("access", exc_info=exc)
            else:
                logger.error("access")
        elif level == "warning":
            logger.warning("access")
        else:
            logger.info("access")
    else:
        msg = (
            f'{method} {path}{qs} -> {status} '
            f'latency_ms={payload["latency_ms"]} rx={rx_bytes} tx={tx_bytes} '
            f'ip={client_ip} req_id={request_id} trace_id={trace_id}'
        )
        if level == "error":
            logging.getLogger("studio_services.access").error(msg, exc_info=exc)
        elif level == "warning":
            logging.getLogger("studio_services.access").warning(msg)
        else:
            logging.getLogger("studio_services.access").info(msg)


# --------------------------- installer ---------------------------


def install_access_log_middleware(app: FastAPI, *, redact_headers: Optional[Tuple[str, ...]] = None) -> None:
    """
    Add the AccessLogMiddleware to the FastAPI app.

    Parameters
    ----------
    redact_headers:
        Reserved for future use to prevent logging specific inbound headers.
        (No headers are logged today; included for API parity.)
    """
    app.add_middleware(AccessLogMiddleware, redact_headers=redact_headers)


__all__ = ["AccessLogMiddleware", "install_access_log_middleware"]
