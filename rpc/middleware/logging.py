from __future__ import annotations

import json
import logging
import time
import types
import uuid
from typing import Any, Dict, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Try to use orjson for faster structured logs if present
try:  # pragma: no cover
    import orjson as _json  # type: ignore

    def _dumps(obj: Any) -> str:
        return _json.dumps(obj).decode("utf-8")

except Exception:  # pragma: no cover
    def _dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


_LOG = logging.getLogger("animica.rpc")


def _get_client_ip(request: Request) -> str:
    # Prefer X-Forwarded-For (first hop) when behind a trusted proxy; this is *informational* only.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    # Starlette's client may be None in some envs
    client = request.client
    return client.host if client else "-"


def _maybe_utf8(b: bytes, limit: int) -> str:
    if limit <= 0 or not b:
        return ""
    sample = b[:limit]
    try:
        s = sample.decode("utf-8", errors="strict")
        if len(b) > limit:
            s += "…"
        return s
    except UnicodeDecodeError:
        # Hexify a tiny prefix if not valid UTF-8
        hx = sample.hex()
        if len(b) > limit:
            hx += "…"
        return f"0x{hx}"


def _detect_jsonrpc_method(b: bytes) -> Optional[str]:
    if not b:
        return None
    # Best-effort parse, tolerate non-JSON bodies
    try:
        obj = json.loads(b.decode("utf-8"))
    except Exception:
        return None

    if isinstance(obj, dict):
        # JSON-RPC 2.0 single call
        m = obj.get("method")
        return str(m) if isinstance(m, str) else None
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, dict):
            m = first.get("method")
            return str(m) if isinstance(m, str) else None
    return None


def _ensure_request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id") or request.headers.get("x-trace-id")
    if rid:
        return rid
    return uuid.uuid4().hex


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Structured access logging with tracing IDs.

    - Emits a single JSON line per HTTP request at INFO level:
      {
        "event":"http_request",
        "req_id":"…",
        "method":"POST",
        "path":"/rpc",
        "query":"?foo=bar",
        "status":200,
        "duration_ms":12.34,
        "bytes_sent":1234,
        "client_ip":"203.0.113.5",
        "user_agent":"…",
        "jsonrpc_method":"chain.getHead",
        "body_sample":"{…}" | "0x…"
      }

    - Adds `X-Request-ID` response header (and uses incoming header if provided).
    - Reads (and restores) the request body so downstream handlers can still consume it.
    - `request_body_sample` controls how many bytes of the request body are logged (default: 0).

    Notes:
    - Only runs on HTTP scopes; WebSocket frames bypass this middleware.
    """

    def __init__(self, app, request_body_sample: int = 0) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.request_body_sample = max(0, int(request_body_sample))

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.scope.get("type") != "http":  # pass-through for websockets/other
            return await call_next(request)

        req_id = _ensure_request_id(request)
        start = time.perf_counter()

        # Snapshot headers we care about early (they can be mutated later)
        method = request.method
        path = request.url.path
        query = request.url.query
        client_ip = _get_client_ip(request)
        ua = request.headers.get("user-agent", "-")

        # Read and restore body for downstream consumers (idempotent)
        try:
            body = await request.body()
        except Exception:
            body = b""

        # Put the body back so downstream handlers can read it normally.
        # Starlette's Request caches ._body; we also patch ._receive to a one-shot supplier.
        request._body = body  # type: ignore[attr-defined]

        async def _receive_once():
            nonlocal body
            b, body = body, b""
            return {"type": "http.request", "body": b, "more_body": False}

        request._receive = _receive_once  # type: ignore[attr-defined]

        # Optional introspection of JSON-RPC method name (best-effort)
        jsonrpc_method = _detect_jsonrpc_method(body)

        # Call downstream and capture response / errors
        status = 500
        bytes_sent: Optional[int] = None
        exc_info: Optional[BaseException] = None
        try:
            response: Response = await call_next(request)
            status = int(getattr(response, "status_code", 200))
            # attach request id header for correlation
            try:
                response.headers["X-Request-ID"] = req_id
            except Exception:
                pass
            # size (if known)
            cl = response.headers.get("content-length")
            if cl and cl.isdigit():
                bytes_sent = int(cl)
            elif hasattr(response, "body_iterator") and isinstance(response.body_iterator, (bytes, bytearray)):
                bytes_sent = len(response.body_iterator)  # type: ignore[arg-type]
            return response
        except Exception as e:
            exc_info = e
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0

            # Build log record
            record: Dict[str, Any] = {
                "event": "http_request",
                "req_id": req_id,
                "method": method,
                "path": path,
                "query": (f"?{query}" if query else ""),
                "status": status,
                "duration_ms": round(duration_ms, 3),
                "bytes_sent": bytes_sent,
                "client_ip": client_ip,
                "user_agent": ua,
            }
            if jsonrpc_method:
                record["jsonrpc_method"] = jsonrpc_method
            if self.request_body_sample > 0:
                record["body_sample"] = _maybe_utf8(body, self.request_body_sample)

            line = _dumps(record)
            if exc_info is None and 100 <= status < 400:
                _LOG.info(line)
            elif exc_info is None:
                _LOG.warning(line)
            else:
                # Include exception context for error cases
                _LOG.error(line, exc_info=exc_info)


__all__ = ["LoggingMiddleware"]
