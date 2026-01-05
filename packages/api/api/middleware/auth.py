"""Authentication Middleware - JWT validation and user extraction"""
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from jose import JWTError, jwt
from typing import Optional
import os


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT authentication"""
    
    def __init__(self, app, jwt_secret: Optional[str] = None, jwt_algorithm: str = "HS256"):
        super().__init__(app)
        self.jwt_secret = jwt_secret or os.getenv("JWT_SECRET_KEY", "dev_jwt_secret_change_in_production")
        self.jwt_algorithm = jwt_algorithm
        
        # Public endpoints that don't require authentication
        self.public_paths = {
            "/", "/health", "/docs", "/openapi.json", "/redoc",
            "/api/v1/auth/register", "/api/v1/auth/login",
            "/api/v1/auth/wallet/challenge", "/api/v1/auth/wallet/verify"
        }
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for public endpoints
        if request.url.path in self.public_paths:
            return await call_next(request)
        
        # Also skip auth for paths starting with /static or /assets
        if request.url.path.startswith(("/static/", "/assets/")):
            return await call_next(request)
        
        # Extract JWT from Authorization header
        auth_header = request.headers.get("Authorization")
        
        if not auth_header:
            # Allow API keys via X-API-Key header as alternative
            api_key = request.headers.get("X-API-Key")
            if api_key:
                # TODO: Validate API key with auth service
                request.state.user_id = "api_key_user"
                request.state.auth_method = "api_key"
                return await call_next(request)
            
            # No authentication provided
            return await call_next(request)
        
        # Parse Bearer token
        try:
            scheme, token = auth_header.split()
            if scheme.lower() != "bearer":
                raise ValueError("Invalid authentication scheme")
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization header format",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Validate JWT
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
            
            # Check token type
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Extract user info and add to request state
            request.state.user_id = payload.get("sub")
            request.state.user_email = payload.get("email")
            request.state.auth_method = "jwt"
            
        except JWTError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        response = await call_next(request)
        return response
