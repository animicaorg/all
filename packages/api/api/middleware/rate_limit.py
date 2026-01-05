"""Rate Limiting Middleware - Redis-backed rate limiting"""
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as redis
import time
import os
from typing import Optional


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting using Redis"""
    
    def __init__(
        self, 
        app,
        redis_url: Optional[str] = None,
        requests_per_minute: int = 60,
        burst_size: int = 100
    ):
        super().__init__(app)
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.redis_client: Optional[redis.Redis] = None
        
        # Paths exempt from rate limiting
        self.exempt_paths = {"/health", "/"}
    
    async def get_redis(self):
        """Get or create Redis connection"""
        if self.redis_client is None:
            self.redis_client = await redis.from_url(self.redis_url, decode_responses=True)
        return self.redis_client
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for exempt paths
        if request.url.path in self.exempt_paths:
            return await call_next(request)
        
        # Determine rate limit key (user ID or IP address)
        rate_limit_key = self._get_rate_limit_key(request)
        
        if not rate_limit_key:
            # No rate limit key, skip rate limiting
            return await call_next(request)
        
        try:
            # Check rate limit
            r = await self.get_redis()
            
            # Use sliding window rate limiting
            now = time.time()
            window_key = f"rate_limit:{rate_limit_key}"
            
            # Remove old entries (older than 60 seconds)
            await r.zremrangebyscore(window_key, 0, now - 60)
            
            # Count requests in current window
            request_count = await r.zcard(window_key)
            
            if request_count >= self.requests_per_minute:
                # Rate limit exceeded
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Maximum {self.requests_per_minute} requests per minute.",
                    headers={
                        "X-RateLimit-Limit": str(self.requests_per_minute),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + 60)),
                        "Retry-After": "60"
                    }
                )
            
            # Add current request to window
            await r.zadd(window_key, {f"{now}:{id(request)}": now})
            await r.expire(window_key, 60)
            
            # Add rate limit headers to response
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
            response.headers["X-RateLimit-Remaining"] = str(self.requests_per_minute - request_count - 1)
            response.headers["X-RateLimit-Reset"] = str(int(now + 60))
            
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            # If Redis fails, allow request through (fail open)
            # Log error in production
            print(f"Rate limit check failed: {e}")
            return await call_next(request)
    
    def _get_rate_limit_key(self, request: Request) -> Optional[str]:
        """Extract rate limit key from request"""
        # Try to get user ID from request state (set by auth middleware)
        if hasattr(request.state, "user_id") and request.state.user_id:
            return f"user:{request.state.user_id}"
        
        # Fall back to IP address
        client_ip = request.client.host if request.client else None
        if client_ip:
            return f"ip:{client_ip}"
        
        return None
