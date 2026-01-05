"""Authentication Middleware - JWT validation and user extraction"""
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT authentication"""
    
    async def dispatch(self, request: Request, call_next):
        # TODO: Implement JWT validation
        # Skip auth for public endpoints
        if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)
        
        # Extract and validate JWT
        # Add user info to request.state
        
        response = await call_next(request)
        return response
