from __future__ import annotations

"""
Token-bucket rate limiting (per-IP / per-API-key / per-route) for FastAPI.

Features
--------
- In-memory token buckets with monotonic clock (async-safe).
- Global defaults (global, ip, key) + per-route overrides.
- Configurable via env var RATE_LIMITS (human-friendly syntax).
- Optional per-endpoint dependency for stricter/custom buckets.
- Adds 429 with Retry-After and X-RateLimit-* hints when blocked.

⚠️ Note: In-memory buckets are per-process. For multi-worker or multi-host
deployments, use a distributed store (e.g., Redis). This module is structured
so a swap to a shared backend is straightforward (replace TokenStore).

Quickstart
----------
    from fastapi import FastAPI, Depends
    from studio_services.security.rate_limit import setup_rate_limiter, rate_limit

    app = FastAPI()
    setup_rate_limiter(app)  # installs middleware using env RATE_LIMITS

    @app.post("/faucet/drip")
    async def drip(_rl = Depends(rate_limit("faucet", rate="3r/m", burst=5))):
        return {"ok": True}

Environment config
------------------
RATE_LIMITS supports semicolon-separated rules:

    RATE_LIMITS="
      global=100r/s,burst=200;
      ip=30r/s,burst=60;
      key=60r/m,burst=120;
      route:POST /deploy=5r/s,burst=10;
      route:/verify=10r/s,burst=20
    "

Accepted rate units: r/s, r/m, r/h.

If not set, safe defaults are applied:
- global: 50r/s burst 100
- ip:     20r/s burst 40
- key:    40r/m burst 80
"""

import asyncio
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Callable, Awaitable, Any, List

from fastapi import Request, HTTPException, status
from fastapi import Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ------------------------------ parsing & config ------------------------------


_RATE_RE = re.compile(r"^\s*(\d+)\s*r\s*/\s*([smh])\s*$", re.IGNORECASE)


def _parse_rate(rate: str) -> float:
    """
    Parse e.g. "10r/s", "60r/m", "3600r/h" → tokens per second (float).
    """
    m = _RATE_RE.match(rate)
    if not m:
        raise ValueError(f"Invalid rate spec: {rate!r}")
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return n
    if unit == "m":
        return n / 60.0
    if unit == "h":
        return n / 3600.0
    raise ValueError(f"Unknown rate unit in {rate!r}")


@dataclass(frozen=True)
class RateRule:
    name: str
    refill_per_sec: float
    capacity: float  # burst
    cost: float = 1.0


def _rule_from(rate: str, burst: Optional[int], *, name: str) -> RateRule:
    rps = _parse_rate(rate)
    cap = float(burst if burst is not None else max(1, int(rps * 2)))
    return RateRule(name=name, refill_per_sec=rps, capacity=cap)


@dataclass
class RateConfig:
    global_rule: RateRule
    ip_rule: RateRule
    key_rule: RateRule
    route_rules: Dict[str, RateRule]  # key: "METHOD /path" or "/path"


def _default_config() -> RateConfig:
    return RateConfig(
        global_rule=RateRule("global", refill_per_sec=50.0, capacity=100.0),
        ip_rule=RateRule("ip", refill_per_sec=20.0, capacity=40.0),
        key_rule=RateRule("key", refill_per_sec=40.0 / 60.0, capacity=80.0),
        route_rules={},
    )


def _split_env_list(s: str) -> List[str]:
    s = s.replace("\r", "\n")
    parts: List[str] = []
    for line in s.split("\n"):
        for piece in line.split(";"):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts


def _parse_kv(spec: str) -> Tuple[str, str]:
    if "=" not in spec:
        raise ValueError(f"Expected key=value: {spec!r}")
    k, v = spec.split("=", 1)
    return k.strip(), v.strip()


def _parse_attrs(v: str) -> Tuple[str, Optional[int]]:
    """
    Parse "10r/s,burst=20" → ("10r/s", 20).
    """
    rate = v
    burst = None
    if "," in v:
        parts = [p.strip() for p in v.split(",") if p.strip()]
        rate = parts[0]
        for p in parts[1:]:
            if p.lower().startswith("burst="):
                try:
                    burst = int(p.split("=", 1)[1])
                except Exception as e:
                    raise ValueError(f"Invalid burst value in {v!r}: {e}")
    return rate, burst


def load_config_from_env() -> RateConfig:
    raw = os.getenv("RATE_LIMITS") or ""
    if not raw.strip():
        return _default_config()

    cfg = _default_config()
    for entry in _split_env_list(raw):
        key, val = _parse_kv(entry)
        rate, burst = _parse_attrs(val)

        if key == "global":
            cfg.global_rule = _rule_from(rate, burst, name="global")
        elif key == "ip":
            cfg.ip_rule = _rule_from(rate, burst, name="ip")
        elif key == "key":
            cfg.key_rule = _rule_from(rate, burst, name="key")
        elif key.startswith("route:"):
            rid = key[len("route:") :].strip()
            cfg.route_rules[rid] = _rule_from(rate, burst, name=f"route:{rid}")
        else:
            raise ValueError(f"Unknown RATE_LIMITS key: {key!r}")
    return cfg


# ------------------------------ token buckets ---------------------------------


class TokenBucket:
    __slots__ = ("capacity", "refill_per_sec", "tokens", "ts", "lock")

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self.ts = time.monotonic()
        self.lock = asyncio.Lock()

    async def try_consume(self, cost: float = 1.0) -> Tuple[bool, float, float, float]:
        """
        Attempt to consume `cost` tokens.

        Returns (allowed, retry_after_seconds, remaining, capacity)
        """
        now = time.monotonic()
        async with self.lock:
            elapsed = max(0.0, now - self.ts)
            if self.refill_per_sec > 0.0 and elapsed > 0.0:
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.ts = now

            if self.tokens >= cost:
                self.tokens -= cost
                return True, 0.0, max(0.0, self.tokens), self.capacity

            deficit = cost - self.tokens
            retry_after = deficit / max(self.refill_per_sec, 1e-9)
            return False, retry_after, max(0.0, self.tokens), self.capacity


class TokenStore:
    """
    Simple in-memory store for buckets keyed by (kind, id, rule.name).

    kind: "global" | "ip" | "key" | "route"
    id:   "" for global, IP string, API key, or route id (e.g., "POST /deploy")
    """

    def __init__(self) -> None:
        self._buckets: Dict[Tuple[str, str, str], TokenBucket] = {}
        self._lock = asyncio.Lock()

    async def get(self, kind: str, ident: str, rule: RateRule) -> TokenBucket:
        key = (kind, ident, rule.name)
        bucket = self._buckets.get(key)
        if bucket is not None:
            return bucket
        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(capacity=rule.capacity, refill_per_sec=rule.refill_per_sec)
                self._buckets[key] = bucket
            return bucket


# ------------------------------ identifier helpers ----------------------------


def _client_ip(request: Request) -> str:
    """
    Best-effort extraction of client IP. Honors common proxy headers.
    """
    # X-Forwarded-For may contain multiple, take first non-empty
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip:
            return ip
    xri = request.headers.get("x-real-ip", "").strip()
    if xri:
        return xri
    cfi = request.headers.get("cf-connecting-ip", "").strip()
    if cfi:
        return cfi
    # Fallback to connection peer
    client = request.client
    return client.host if client else "unknown"


def _api_key_from_request(request: Request) -> Optional[str]:
    """
    Mirror of auth: Authorization: Bearer <token> | X-API-Key | ?api_key=
    """
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split()
        if len(parts) == 2 and parts[0].lower() in ("bearer", "token"):
            return parts[1].strip()
    hdr = request.headers.get("x-api-key")
    if hdr:
        return hdr.strip()
    qp = request.query_params.get("api_key")
    if qp:
        return qp.strip()
    return None


def _route_id(request: Request) -> str:
    method = request.method.upper()
    path = request.url.path
    return f"{method} {path}"


# ------------------------------ limiter core ----------------------------------


class RateLimiter:
    """
    Composes buckets: global + per-IP + per-key + per-route (if configured).
    """

    def __init__(self, config: Optional[RateConfig] = None) -> None:
        self.cfg = config or load_config_from_env()
        self.store = TokenStore()

    async def check(self, request: Request, *, extra_route_rule: Optional[RateRule] = None) -> None:
        """
        Evaluate all applicable buckets; raise HTTP 429 if any is exceeded.
        """
        # Evaluate buckets in a deterministic order (most specific first)
        # so error messages are consistent.
        route = _route_id(request)
        ip = _client_ip(request)
        key = _api_key_from_request(request)
        route_rule = self._match_route_rule(route)

        # Build the list of (kind, id, rule)
        scopes: list[Tuple[str, str, RateRule]] = []

        if route_rule is not None:
            scopes.append(("route", route, route_rule))
        if extra_route_rule is not None:
            scopes.append(("route", route, extra_route_rule))
        if key:
            scopes.append(("key", key, self.cfg.key_rule))
        if ip:
            scopes.append(("ip", ip, self.cfg.ip_rule))
        scopes.append(("global", "", self.cfg.global_rule))

        # Consume cost=1 from each bucket; fail-fast on first denial.
        for kind, ident, rule in scopes:
            bucket = await self.store.get(kind, ident, rule)
            allowed, retry_after, remaining, capacity = await bucket.try_consume(rule.cost)
            if not allowed:
                # Compose helpful headers; for multiple buckets we report the one that blocked.
                headers = {
                    "Retry-After": f"{max(1, int(retry_after))}",
                    "X-RateLimit-Bucket": f"{kind}:{ident or 'global'}",
                    "X-RateLimit-Limit": f"{int(capacity)}/{self._unit_from_rps(rule.refill_per_sec)}",
                    "X-RateLimit-Remaining": f"{int(remaining)}",
                    "X-RateLimit-Reason": rule.name,
                }
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded for {kind}",
                    headers=headers,
                )

    def _match_route_rule(self, route_id: str) -> Optional[RateRule]:
        # Exact match on "METHOD /path" first, then on plain "/path".
        rule = self.cfg.route_rules.get(route_id)
        if rule:
            return rule
        # Strip method
        try:
            _method, path = route_id.split(" ", 1)
        except ValueError:
            path = route_id
        return self.cfg.route_rules.get(path)

    @staticmethod
    def _unit_from_rps(rps: float) -> str:
        # Pick a human unit to display (approx).
        if abs(rps - round(rps)) < 1e-9 and rps >= 1.0:
            return "s"
        if rps * 60 >= 1.0:
            return "m"
        return "h"


# Singleton installed by setup_rate_limiter(); available to dependencies.
_LIMITER: Optional[RateLimiter] = None


def get_limiter() -> RateLimiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = RateLimiter()
    return _LIMITER


# ------------------------------ middleware ------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Applies global/ip/key/route limits on every request based on env config.
    """

    def __init__(self, app, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or get_limiter()

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        try:
            await self.limiter.check(request)
        except HTTPException as e:
            return Response(
                content='{"detail": "rate_limited"}',
                status_code=e.status_code,
                headers=e.headers,
                media_type="application/json",
            )
        return await call_next(request)


def setup_rate_limiter(app, config: Optional[RateConfig] = None) -> RateLimiter:
    """
    Initialize a RateLimiter and install middleware. Call once at app startup.

        app = FastAPI()
        setup_rate_limiter(app)  # uses RATE_LIMITS env

    You may pass an explicit RateConfig for tests.
    """
    global _LIMITER
    _LIMITER = RateLimiter(config=config)
    app.add_middleware(RateLimitMiddleware, limiter=_LIMITER)
    return _LIMITER


# ------------------------------ endpoint helper --------------------------------


def rate_limit(
    route_name: str,
    *,
    rate: str = "10r/m",
    burst: Optional[int] = None,
    cost: float = 1.0,
):
    """
    FastAPI dependency to add/override a route-specific bucket for this endpoint.

    Example:
        @router.post("/deploy")
        async def deploy(_rl = Depends(rate_limit("deploy", rate="5r/s", burst=10))):
            ...

    This works *in addition* to global/ip/key rules applied by middleware.
    """
    rule = _rule_from(rate, burst, name=f"route:{route_name}")
    # Apply custom cost if provided
    rule = RateRule(name=rule.name, refill_per_sec=rule.refill_per_sec, capacity=rule.capacity, cost=cost)

    async def _dep(request: Request) -> None:
        await get_limiter().check(request, extra_route_rule=rule)

    return Depends(_dep)


__all__ = [
    "RateRule",
    "RateConfig",
    "RateLimiter",
    "RateLimitMiddleware",
    "setup_rate_limiter",
    "rate_limit",
    "load_config_from_env",
]
