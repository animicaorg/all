from __future__ import annotations

"""
Animica • DA • Retrieval • Rate Limiting

Lightweight IP- and token-tier-based token buckets for DoS resistance.

Design
------
- Two checks per request:
    1) IP bucket: shared by all traffic from a client IP (behind a proxy, we
       honor X-Forwarded-For / X-Real-IP; see client_ip()).
    2) Tier bucket: keyed by the caller's API token if present; otherwise by IP.
       Tiers come from da.retrieval.auth (public/test/provider/admin).

- Token bucket algorithm:
    tokens refill continuously at 'rps' (tokens/sec) up to 'burst' capacity.
    A request spends 'cost' tokens (default 1). If insufficient, we reject with 429.

- Defaults are conservative and testnet-friendly. Override via env:

    # IP-wide defaults
    DA_RATE_IP_RPS=5
    DA_RATE_IP_BURST=20

    # Per-tier (tokens/sec and burst)
    DA_RATE_PUBLIC_RPS=3
    DA_RATE_PUBLIC_BURST=10
    DA_RATE_TEST_RPS=5
    DA_RATE_TEST_BURST=20
    DA_RATE_PROVIDER_RPS=20
    DA_RATE_PROVIDER_BURST=80
    DA_RATE_ADMIN_RPS=50
    DA_RATE_ADMIN_BURST=200

- Usage in FastAPI route:

    from fastapi import APIRouter, Depends
    from da.retrieval.rate_limit import rate_limit_dependency

    router = APIRouter()

    @router.post("/da/blob")
    def post_blob(..., _rl=Depends(rate_limit_dependency(cost=5))):
        ...

    @router.get("/da/blob/{commit}")
    def get_blob(commit: str, _rl=Depends(rate_limit_dependency(cost=1))):
        ...

Implementation notes
--------------------
- In-memory buckets are process-local. For multi-replica deployments, prefer
  per-replica limits or put the service behind a rate-limiting proxy.
- This module is asyncio-safe via per-bucket threading locks guarding updates.
"""

import os
import time
import threading
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status

try:
    from da.retrieval.auth import AuthContext, auth_dependency
except Exception:  # pragma: no cover
    # Tiny fallback to allow import without the auth module (tests)
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class AuthContext:  # type: ignore
        token: Optional[str] = None
        tier: str = "public"
        subject: Optional[str] = None

    def auth_dependency(_: Request) -> AuthContext:  # type: ignore
        return AuthContext(token=None, tier="public", subject=None)


# --------- Helpers to read environment with defaults -------------------------

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return float(default)
    try:
        v = float(raw)
        return float(default) if v <= 0 else v
    except Exception:
        return float(default)

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return int(default)
    try:
        v = int(raw)
        return int(default) if v <= 0 else v
    except Exception:
        return int(default)


# --------- Client IP extraction ---------------------------------------------

def client_ip(request: Request) -> str:
    """
    Best-effort client IP detection with proxy headers.
    """
    # Try Forwarded/X-Forwarded-For (take the left-most)
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        left = fwd.split(",")[0].strip()
        if left:
            return left
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    # Fallback to client host
    host, _port = (request.client.host, request.client.port) if request.client else ("0.0.0.0", 0)
    return host or "0.0.0.0"


# --------- Token Bucket ------------------------------------------------------

@dataclass
class Rate:
    rps: float     # tokens per second
    burst: int     # maximum bucket capacity

class TokenBucket:
    __slots__ = ("rate", "capacity", "tokens", "updated", "lock")

    def __init__(self, rate: Rate):
        self.rate = rate
        self.capacity = float(rate.burst)
        self.tokens = float(rate.burst)
        self.updated = time.perf_counter()
        self.lock = threading.Lock()

    def _refill(self, now: float) -> None:
        dt = max(0.0, now - self.updated)
        if dt > 0:
            self.tokens = min(self.capacity, self.tokens + self.rate.rps * dt)
            self.updated = now

    def consume(self, cost: float = 1.0) -> Tuple[bool, float]:
        """
        Attempt to consume 'cost' tokens.
        Returns (ok, retry_after_seconds).
        """
        now = time.perf_counter()
        with self.lock:
            self._refill(now)
            if self.tokens >= cost:
                self.tokens -= cost
                return True, 0.0
            # not enough; compute wait time for deficit
            deficit = cost - self.tokens
            retry = deficit / self.rate.rps if self.rate.rps > 0 else 1.0
            return False, max(0.01, retry)


# --------- Limiter -----------------------------------------------------------

class RateLimiter:
    """
    Holds IP- and tier-scoped buckets. Thread-safe per bucket.
    """

    def __init__(self,
                 ip_rate: Rate,
                 tier_rates: Dict[str, Rate]):
        self.ip_rate = ip_rate
        self.tier_rates = tier_rates
        self._ip_buckets: Dict[str, TokenBucket] = {}
        self._tier_buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_bucket(self, table: Dict[str, TokenBucket], key: str, rate: Rate) -> TokenBucket:
        b = table.get(key)
        if b is not None:
            return b
        with self._lock:
            b = table.get(key)
            if b is None:
                b = TokenBucket(rate)
                table[key] = b
            return b

    def allow_ip(self, ip: str, cost: float = 1.0) -> Tuple[bool, float]:
        bucket = self._get_bucket(self._ip_buckets, f"ip:{ip}", self.ip_rate)
        return bucket.consume(cost)

    def allow_tier(self, tier: str, key: str, cost: float = 1.0) -> Tuple[bool, float]:
        rate = self.tier_rates.get(tier, self.tier_rates.get("public"))
        if rate is None:  # safety
            rate = Rate(rps=3.0, burst=10)
        bucket = self._get_bucket(self._tier_buckets, f"{tier}:{key}", rate)
        return bucket.consume(cost)


# --------- Defaults + Singleton ---------------------------------------------

def _build_default_limiter() -> RateLimiter:
    ip = Rate(
        rps=_env_float("DA_RATE_IP_RPS", 5.0),
        burst=_env_int("DA_RATE_IP_BURST", 20),
    )
    tiers = {
        "public": Rate(_env_float("DA_RATE_PUBLIC_RPS", 3.0), _env_int("DA_RATE_PUBLIC_BURST", 10)),
        "test": Rate(_env_float("DA_RATE_TEST_RPS", 5.0), _env_int("DA_RATE_TEST_BURST", 20)),
        "provider": Rate(_env_float("DA_RATE_PROVIDER_RPS", 20.0), _env_int("DA_RATE_PROVIDER_BURST", 80)),
        "admin": Rate(_env_float("DA_RATE_ADMIN_RPS", 50.0), _env_int("DA_RATE_ADMIN_BURST", 200)),
    }
    return RateLimiter(ip_rate=ip, tier_rates=tiers)

_LIMITER = _build_default_limiter()


# --------- FastAPI dependency -----------------------------------------------

def rate_limit_dependency(*, cost: float = 1.0):
    """
    Build a FastAPI dependency that enforces rate limits for the current request.
    Checks BOTH the client IP bucket and the caller's tier bucket.

    Example:
        @router.post("/da/blob")
        def post_blob(..., _rl = Depends(rate_limit_dependency(cost=5))):
            ...
    """
    async def _dep(request: Request, ctx: AuthContext = Depends(auth_dependency)):
        ip = client_ip(request)

        ok_ip, retry_ip = _LIMITER.allow_ip(ip, cost=cost)
        if not ok_ip:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"IP rate limit exceeded. Retry after ~{retry_ip:.2f}s",
                headers={"Retry-After": str(int(max(1.0, retry_ip)))},
            )

        tier_key = ctx.token if (ctx and ctx.token) else ip  # anonymous keyed by IP
        ok_tier, retry_tier = _LIMITER.allow_tier(ctx.tier if ctx else "public", tier_key, cost=cost)
        if not ok_tier:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Tier '{ctx.tier if ctx else 'public'}' rate limit exceeded. Retry after ~{retry_tier:.2f}s",
                headers={"Retry-After": str(int(max(1.0, retry_tier)))},
            )
        # On success, return a tiny token describing what was charged (optional)
        return {"ip": ip, "tier": ctx.tier if ctx else "public", "cost": cost}

    return _dep


# --------- Public API --------------------------------------------------------

__all__ = [
    "Rate",
    "TokenBucket",
    "RateLimiter",
    "client_ip",
    "rate_limit_dependency",
]
