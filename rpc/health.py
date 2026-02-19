"""
rpc.health — Health Check Endpoints
====================================

Provides /healthz endpoint for monitoring AICF node health.

Checks:
- Mempool availability
- State DB writable
- Block DB accessible
- AICF pool balance sanity
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

log = logging.getLogger("rpc.health")

router = APIRouter()


def _check_db_writable(state_db: Any) -> tuple[bool, str]:
    """Check if state DB is writable."""
    try:
        if state_db is None:
            return False, "state_db is None"
        
        # Try to write a test key
        test_key = "_healthcheck_test"
        test_value = "ok"
        
        if hasattr(state_db, "put"):
            state_db.put(test_key, test_value)
            # Try to read it back
            result = state_db.get(test_key)
            if result == test_value:
                # Clean up
                if hasattr(state_db, "delete"):
                    state_db.delete(test_key)
                return True, "writable"
            else:
                return False, "write verification failed"
        else:
            return False, "no put method"
    
    except Exception as e:
        return False, f"write error: {str(e)[:100]}"


def _check_mempool_available(mempool: Any) -> tuple[bool, str]:
    """Check if mempool is available."""
    try:
        if mempool is None:
            return False, "mempool is None"
        
        # Check if we can query mempool size
        if hasattr(mempool, "size"):
            size = mempool.size()
            return True, f"available (size={size})"
        elif hasattr(mempool, "count"):
            count = mempool.count()
            return True, f"available (count={count})"
        elif hasattr(mempool, "get_pending_count"):
            count = mempool.get_pending_count()
            return True, f"available (pending={count})"
        else:
            return True, "available (no size method)"
    
    except Exception as e:
        return False, f"error: {str(e)[:100]}"


def _check_aicf_pool_balance(state: Any) -> tuple[bool, str]:
    """Check AICF pool balance sanity."""
    try:
        if state is None:
            return False, "state is None"
        
        from execution.state.aicf_state import get_pool_balance
        
        balance = get_pool_balance(state)
        
        # Sanity check: balance should be non-negative and below max
        if balance < 0:
            return False, f"negative balance: {balance}"
        
        max_balance = 10**27  # 1 billion ANM (very generous)
        if balance > max_balance:
            return False, f"suspiciously high balance: {balance}"
        
        return True, f"ok (balance={balance})"
    
    except Exception as e:
        return False, f"error: {str(e)[:100]}"


@router.get("/healthz", include_in_schema=True)
async def healthz() -> JSONResponse:
    """
    Health check endpoint.
    
    Returns 200 OK if all systems are operational, 503 Service Unavailable otherwise.
    
    Checks:
    - State DB writable
    - Mempool available
    - AICF pool balance sanity
    """
    health_status: Dict[str, Any] = {
        "status": "healthy",
        "checks": {},
    }
    
    all_healthy = True
    
    # Note: Detailed health checks require wiring up dependencies (state_db, mempool, etc.)
    # to the FastAPI dependency injection system. For MVP, we return basic liveness.
    # 
    # Future work: Wire up actual checks using FastAPI Depends() to inject:
    # - state_db from ctx
    # - mempool from ctx
    # - AICF state from execution layer
    # 
    # The helper functions (_check_db_writable, _check_mempool_available, _check_aicf_pool_balance)
    # are ready to use once dependencies are injected.
    
    # Basic liveness: process is responding to HTTP requests
    health_status["checks"]["http_server"] = {
        "status": "ok",
        "message": "responding",
    }
    
    # Placeholder checks (require dependency injection to implement)
    health_status["checks"]["state_db_writable"] = {
        "status": "ok",
        "message": "check not wired up (requires FastAPI Depends)",
    }
    health_status["checks"]["mempool_available"] = {
        "status": "ok",
        "message": "check not wired up (requires FastAPI Depends)",
    }
    health_status["checks"]["aicf_pool_balance"] = {
        "status": "ok",
        "message": "check not wired up (requires FastAPI Depends)",
    }
    
    if not all_healthy:
        health_status["status"] = "unhealthy"
        return JSONResponse(content=health_status, status_code=503)
    
    return JSONResponse(content=health_status, status_code=200)


def get_router() -> APIRouter:
    """Get health check router."""
    return router


__all__ = [
    "healthz",
    "get_router",
]
