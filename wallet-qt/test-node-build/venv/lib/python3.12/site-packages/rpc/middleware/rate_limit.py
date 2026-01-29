from __future__ import annotations

import json
import math
import time
import typing as t
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# --------- tiny JSON helper (tries orjson if available) ----------
try:  # pragma: no cover
    import orjson as _json  # type: ignore

    def _dumps(obj: t.Any) -> str:
        return _json.dumps(obj).decode("utf-8")

except Exception:  # pragma: no cover

    def _dumps(obj: t.Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ------------------------ token bucket ---------------------------


@dataclass
class TokenBucket:
    capacity: float
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last = now

    def take(self, n: float = 1.0) -> tuple[bool, float]:
        """
        Attempt to consume `n` tokens.
        Returns (ok, retry_after_seconds_if_denied).
        """
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        deficit = n - self.tokens
        retry = float("inf") if self.refill_rate <= 0 else deficit / self.refill_rate
        # Do not mutate tokens on failure; just report retry time.
        return False, retry

    def remaining(self) -> float:
        self._refill()
        return max(0.0, self.tokens)


# ---------------------- middleware core --------------------------


def _get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "-"


def _detect_jsonrpc_method(body: bytes) -> t.Optional[str]:
    if not body:
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if isinstance(obj, dict):
        m = obj.get("method")
        return str(m) if isinstance(m, str) else None
    if isinstance(obj, list) and obj:
        first = obj[0]
        if isinstance(first, dict):
            m = first.get("method")
            return str(m) if isinstance(m, str) else None
    return None


def _should_parse_body(request: Request) -> bool:
    # Only parse body for potential JSON-RPC requests (path hints + content-type)
    if request.url.path.rstrip("/") not in ("/rpc", "/jsonrpc", "/api/rpc"):
        return False
    ctype = request.headers.get("content-type", "")
    return "json" in ctype or ctype == ""  # tolerate missing content-type


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Token-bucket rate limiter with:
      - per-IP global bucket (all routes)
      - optional per-JSON-RPC-method buckets (keyed by IP+method)

    Configuration:
      default_capacity: float      # max burst size for the global per-IP bucket
      default_refill_rate: float   # tokens/second for the global per-IP bucket
      per_method_limits: dict[str, tuple[capacity, refill_rate]]  # overrides
      exempt_paths: set[str]       # paths to skip (e.g., /healthz, /metrics)
      ip_whitelist: set[str]       # IPs to skip limits
      cost_fn: Optional[callable(request, jsonrpc_method) -> float]  # tokens per request
      sample_body_bytes: int       # if > 0, include first N bytes in error details

    Behavior:
      - On reject, returns HTTP 429 with JSON body (and JSON-RPC flavored error fields).
      - Adds `Retry-After`, `X-RateLimit-*` headers when possible.
      - Body is read/restored if needed to detect JSON-RPC method; downstream handlers can still read it.
    """

    def __init__(
        self,
        app,
        *,
        default_capacity: float = 20.0,
        default_refill_rate: float = 20.0,
        per_method_limits: t.Optional[dict[str, tuple[float, float]]] = None,
        exempt_paths: t.Optional[t.Iterable[str]] = None,
        ip_whitelist: t.Optional[t.Iterable[str]] = None,
        cost_fn: t.Optional[t.Callable[[Request, t.Optional[str]], float]] = None,
        sample_body_bytes: int = 0,
    ) -> None:
        super().__init__(app)
        self.default_capacity = float(default_capacity)
        self.default_refill_rate = float(default_refill_rate)
        self.per_method_limits = dict(per_method_limits or {})
        self.exempt_paths = set(exempt_paths or set())
        self.ip_whitelist = set(ip_whitelist or set())
        self.cost_fn = cost_fn
        self.sample_body_bytes = max(0, int(sample_body_bytes))

        # Buckets
        self._global: dict[str, TokenBucket] = {}
        self._per_method: dict[tuple[str, str], TokenBucket] = {}

    # ------------------------ bucket helpers ------------------------

    def _get_global_bucket(self, ip: str) -> TokenBucket:
        b = self._global.get(ip)
        if b is None:
            b = TokenBucket(self.default_capacity, self.default_refill_rate)
            self._global[ip] = b
        return b

    def _get_method_bucket(self, ip: str, method: str) -> TokenBucket:
        key = (ip, method)
        b = self._per_method.get(key)
        if b is None:
            cap, rate = self.per_method_limits[method]
            b = TokenBucket(float(cap), float(rate))
            self._per_method[key] = b
        return b

    # ------------------------ HTTP pipeline ------------------------

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        # Skip non-HTTP (e.g., websocket upgrade frames)
        if request.scope.get("type") != "http":
            return await call_next(request)

        path = request.url.path
        if path in self.exempt_paths:
            return await call_next(request)

        ip = _get_client_ip(request)
        if ip in self.ip_whitelist:
            return await call_next(request)

        # Read body if we might need the JSON-RPC method; then restore it.
        body: bytes = b""
        method_name: t.Optional[str] = None
        if _should_parse_body(request):
            try:
                body = await request.body()
            except Exception:
                body = b""
            # restore for downstream
            request._body = body  # type: ignore[attr-defined]

            async def _receive_once():
                nonlocal body
                b, body = body, b""
                return {"type": "http.request", "body": b, "more_body": False}

            request._receive = _receive_once  # type: ignore[attr-defined]
            method_name = _detect_jsonrpc_method(request._body or b"")  # type: ignore[attr-defined]

        # Determine cost (default 1 token)
        cost = 1.0
        if self.cost_fn is not None:
            try:
                cost = float(self.cost_fn(request, method_name))
                if not math.isfinite(cost) or cost <= 0:
                    cost = 1.0
            except Exception:
                cost = 1.0

        # Global per-IP check
        g = self._get_global_bucket(ip)
        ok, retry_after = g.take(cost)
        if not ok:
            return self._reject(ip, path, method_name, retry_after, g, None, body)

        # Per-method check (if configured for this method)
        mbucket: t.Optional[TokenBucket] = None
        if method_name and method_name in self.per_method_limits:
            mbucket = self._get_method_bucket(ip, method_name)
            ok, retry_after = mbucket.take(cost)
            if not ok:
                return self._reject(
                    ip, path, method_name, retry_after, g, mbucket, body
                )

        return await call_next(request)

    # ------------------------ rejection response ------------------------

    def _reject(
        self,
        ip: str,
        path: str,
        method_name: t.Optional[str],
        retry_after: float,
        global_bucket: TokenBucket,
        method_bucket: t.Optional[TokenBucket],
        body: bytes,
    ) -> Response:
        secs = int(math.ceil(retry_after if math.isfinite(retry_after) else 1))
        headers = {
            "Retry-After": str(secs),
            "X-RateLimit-Limit": str(int(self.default_capacity)),
            "X-RateLimit-Remaining": str(int(global_bucket.remaining())),
        }
        if method_name and method_bucket is not None:
            headers["X-RateLimit-Method"] = method_name
            # We don't expose the exact capacity per-method by default (could, if desired)
            headers["X-RateLimit-Method-Remaining"] = str(
                int(method_bucket.remaining())
            )

        detail: dict[str, t.Any] = {
            "error": {
                "code": -32005,  # application-defined JSON-RPC error code for rate limiting
                "message": "Rate limit exceeded",
                "data": {
                    "retry_after_seconds": secs,
                    "ip": ip,
                    "path": path,
                },
            }
        }
        if method_name:
            detail["error"]["data"]["jsonrpc_method"] = method_name  # type: ignore[index]
        if self.sample_body_bytes > 0 and body:
            sample = body[: self.sample_body_bytes]
            try:
                sample_str = sample.decode("utf-8")
            except UnicodeDecodeError:
                sample_str = "0x" + sample.hex()
            if len(body) > self.sample_body_bytes:
                sample_str += "…"
            detail["error"]["data"]["body_sample"] = sample_str  # type: ignore[index]

        # JSON (not a full JSON-RPC envelope because we don't know request id safely here)
        return JSONResponse(detail, status_code=429, headers=headers)


# ------------------------ convenience factory ------------------------


def build_default_ratelimiter(app) -> RateLimitMiddleware:
    """
    Sensible defaults:
      - Global per-IP: 30 rps burst 60
      - Per-method stricter caps for spammy calls
    """
    per_method = {
        # Read-only, high-frequency:
        "chain.getHead": (60.0, 60.0),
        # Heavier:
        "tx.sendRawTransaction": (6.0, 3.0),
        "da.putBlob": (3.0, 1.5),
        # Misc:
        "state.getBalance": (40.0, 40.0),
    }
    return RateLimitMiddleware(
        app,
        default_capacity=60.0,
        default_refill_rate=30.0,
        per_method_limits=per_method,
        exempt_paths={"/healthz", "/readyz", "/metrics", "/openrpc.json"},
        ip_whitelist=set(),
        cost_fn=None,
        sample_body_bytes=0,
    )


__all__ = ["RateLimitMiddleware", "TokenBucket", "build_default_ratelimiter"]
