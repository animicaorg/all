"""
API Key Authentication and Plan-Based Rate Limiting Middleware.

This middleware integrates with the billing module to:
- Extract and validate API keys from request headers
- Look up user plans (free, pro, enterprise)
- Enforce per-plan rate limits
- Track usage metrics
- Return clean 401/429 errors with no stack traces
"""

from __future__ import annotations

import json
import time
import typing as t
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Import billing module
try:
    from billing.config import get_billing_config
    from billing.store import FileBackedUsageStore, UsageStore
    from billing.utils import validate_api_key
    
    BILLING_AVAILABLE = True
except ImportError:
    BILLING_AVAILABLE = False


def _get_api_key(request: Request, header_name: str) -> t.Optional[str]:
    """Extract API key from request headers."""
    return request.headers.get(header_name, "").strip() or None


def _get_client_ip(request: Request) -> str:
    """Extract client IP from request."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "-"


class APIKeyMiddleware(BaseHTTPMiddleware):
    """
    API Key authentication and plan-based rate limiting middleware.
    
    Features:
    - Validates API keys and looks up plans
    - Enforces per-plan rate limits (requests per minute)
    - Tracks usage metrics (requests, DA bytes, RPC calls, AICF units)
    - Returns clean 401 (unauthorized) and 429 (rate limit) errors
    - Integrates with billing.UsageStore for persistence
    
    Configuration:
    - Uses billing.config for mode, header name, plans, and limits
    - Supports in-memory or file-backed usage tracking
    - Optional API key validation dict (if None, free mode accepts any key)
    
    Behavior in free mode (ANIMICA_BILLING_MODE=free):
    - API key not required
    - If provided, any key is accepted with "free" plan
    - Rate limits still enforced based on free tier
    
    Behavior in paid mode (ANIMICA_BILLING_MODE=paid):
    - API key required
    - Must be in valid_keys dict
    - Rate limits enforced based on plan (free/pro/enterprise)
    """
    
    def __init__(
        self,
        app,
        *,
        usage_store: t.Optional[UsageStore] = None,
        valid_keys: t.Optional[t.Dict[str, str]] = None,
        exempt_paths: t.Optional[t.Iterable[str]] = None,
    ):
        """
        Initialize API key middleware.
        
        Args:
            app: ASGI application
            usage_store: Usage tracking store (default: in-memory)
            valid_keys: Dict mapping API keys to plan names (None = free mode)
            exempt_paths: Paths to skip authentication (e.g., /healthz)
        """
        super().__init__(app)
        
        if not BILLING_AVAILABLE:
            raise RuntimeError(
                "Billing module not available. "
                "Ensure billing/ directory is in PYTHONPATH."
            )
        
        # Load billing config
        self.config = get_billing_config()
        
        # Usage store
        self.usage_store = usage_store or UsageStore()
        
        # Valid API keys (None = free mode, accept any)
        self.valid_keys = valid_keys
        
        # Exempt paths
        self.exempt_paths = set(exempt_paths or {
            "/healthz", "/readyz", "/metrics", "/openrpc.json"
        })
    
    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        """Process request through API key middleware."""
        
        # Skip non-HTTP scopes (websockets, etc.)
        if request.scope.get("type") != "http":
            return await call_next(request)
        
        # Skip exempt paths
        path = request.url.path
        if path in self.exempt_paths:
            return await call_next(request)
        
        # Extract API key
        api_key = _get_api_key(request, self.config.api_key_header)
        
        # Authenticate
        if self.config.mode == "paid" and not api_key:
            return self._error_unauthorized("API key required in paid mode")
        
        # Validate and get plan
        is_valid, plan = validate_api_key(api_key or "", self.valid_keys)
        if not is_valid:
            return self._error_unauthorized("Invalid API key")
        
        # Get plan configuration
        plan = plan or self.config.default_plan
        plan_config = self.config.get_plan(plan)
        
        # Check rate limit
        api_key = api_key or f"ip-{_get_client_ip(request)}"  # Use IP if no key
        allowed, requests_in_window = self.usage_store.check_rate_limit(
            api_key=api_key,
            limit_rpm=plan_config.rate_limit_rpm,
        )
        
        if not allowed:
            return self._error_rate_limit(plan_config.rate_limit_rpm, requests_in_window)
        
        # Track request
        self.usage_store.increment_requests(api_key, count=1)
        self.usage_store.increment_rpc_calls(api_key, count=1)
        
        # Store plan in request state for downstream handlers
        request.state.api_key = api_key
        request.state.plan = plan
        request.state.plan_config = plan_config
        
        return await call_next(request)
    
    def _error_unauthorized(self, message: str) -> Response:
        """Return 401 Unauthorized error."""
        detail = {
            "error": {
                "code": -32000,
                "message": "Unauthorized",
                "data": {
                    "reason": message,
                    "hint": f"Provide a valid API key in '{self.config.api_key_header}' header",
                },
            }
        }
        return JSONResponse(detail, status_code=401)
    
    def _error_rate_limit(self, limit_rpm: int, current_requests: int) -> Response:
        """Return 429 Rate Limit Exceeded error."""
        retry_after = 60  # 1 minute window
        detail = {
            "error": {
                "code": -32005,
                "message": "Rate limit exceeded",
                "data": {
                    "limit_rpm": limit_rpm,
                    "current_requests": current_requests,
                    "retry_after_seconds": retry_after,
                    "hint": "Upgrade your plan for higher rate limits",
                },
            }
        }
        headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit_rpm),
            "X-RateLimit-Remaining": str(max(0, limit_rpm - current_requests)),
        }
        return JSONResponse(detail, status_code=429, headers=headers)


def build_api_key_middleware(
    app,
    *,
    usage_store_path: t.Optional[str] = None,
    valid_keys_path: t.Optional[str] = None,
) -> APIKeyMiddleware:
    """
    Build API key middleware with optional file-backed storage.
    
    Args:
        app: ASGI application
        usage_store_path: Path to usage store JSON file (None = in-memory)
        valid_keys_path: Path to valid keys JSON file (None = free mode)
        
    Returns:
        Configured APIKeyMiddleware
    """
    # Setup usage store
    usage_store: UsageStore
    if usage_store_path:
        usage_store = FileBackedUsageStore(
            file_path=usage_store_path,
            auto_save_interval=60.0,
        )
    else:
        usage_store = UsageStore()
    
    # Load valid keys if provided
    valid_keys: t.Optional[t.Dict[str, str]] = None
    if valid_keys_path:
        path = Path(valid_keys_path)
        if path.exists():
            try:
                with open(path, "r") as f:
                    valid_keys = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load valid keys from {path}: {e}")
    
    return APIKeyMiddleware(
        app,
        usage_store=usage_store,
        valid_keys=valid_keys,
    )


__all__ = [
    "APIKeyMiddleware",
    "build_api_key_middleware",
]
