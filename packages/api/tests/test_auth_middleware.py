"""
Tests for API Gateway Authentication Middleware
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from packages.api.api.middleware.auth import AuthMiddleware


@pytest.fixture
def app():
    """Create test FastAPI app"""
    app = FastAPI()
    app.add_middleware(AuthMiddleware, jwt_secret="test_secret")
    
    @app.get("/test")
    async def test_endpoint(request: Request):
        user_id = getattr(request.state, "user_id", None)
        return {"user_id": user_id}
    
    @app.get("/health")
    async def health_endpoint():
        return {"status": "ok"}
    
    return app


def test_public_endpoint_no_auth(app):
    """Test that public endpoints don't require authentication"""
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    response = client.get("/health")
    
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_protected_endpoint_no_auth(app):
    """Test that protected endpoints work without auth (optional auth)"""
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    response = client.get("/test")
    
    # Should succeed but no user_id
    assert response.status_code == 200
    assert response.json()["user_id"] is None


def test_protected_endpoint_with_jwt(app):
    """Test that JWT token is properly validated"""
    from fastapi.testclient import TestClient
    from jose import jwt
    
    # Create test token
    token = jwt.encode(
        {"sub": "test_user_id", "email": "test@example.com", "type": "access"},
        "test_secret",
        algorithm="HS256"
    )
    
    client = TestClient(app)
    response = client.get("/test", headers={"Authorization": f"Bearer {token}"})
    
    assert response.status_code == 200
    assert response.json()["user_id"] == "test_user_id"


def test_invalid_jwt_token(app):
    """Test that invalid JWT tokens are rejected"""
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    response = client.get("/test", headers={"Authorization": "Bearer invalid_token"})
    
    assert response.status_code == 401
