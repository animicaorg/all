"""
Authentication Router

Proxies authentication requests to the auth service.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from typing import Optional
import httpx
import os

router = APIRouter()

# Auth service URL from environment
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8001")


class RegisterRequest(BaseModel):
    """User registration request"""
    email: EmailStr
    password: str
    wallet_address: Optional[str] = None


class LoginRequest(BaseModel):
    """Login request"""
    email: EmailStr
    password: str


class WalletChallengeRequest(BaseModel):
    """Request wallet authentication challenge"""
    wallet_address: str


class WalletVerifyRequest(BaseModel):
    """Wallet signature authentication"""
    wallet_address: str
    signature: str
    public_key: str


class RefreshTokenRequest(BaseModel):
    """Refresh token request"""
    refresh_token: str


class TokenResponse(BaseModel):
    """JWT token response"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest):
    """
    Register a new user with email/password.
    
    Proxies to auth service for registration.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/register",
                json=request.dict(),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Registration failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login with email and password.
    
    Proxies to auth service for authentication.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/login",
                json=request.dict(),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Login failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.post("/wallet/challenge")
async def wallet_challenge(request: WalletChallengeRequest):
    """
    Get a challenge for wallet signature authentication.
    
    Proxies to auth service.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/wallet/challenge",
                json=request.dict(),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Challenge generation failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.post("/wallet/verify", response_model=TokenResponse)
async def wallet_verify(request: WalletVerifyRequest):
    """
    Verify wallet signature and authenticate.
    
    Proxies to auth service for signature verification.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/wallet/verify",
                json=request.dict(),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Wallet verification failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh access token using refresh token.
    
    Proxies to auth service for token refresh.
    """
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{AUTH_SERVICE_URL}/refresh",
                json=request.dict(),
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Token refresh failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.get("/me")
async def get_current_user(authorization: Optional[str] = None):
    """
    Get current authenticated user info.
    
    Proxies to auth service with authorization header.
    """
    async with httpx.AsyncClient() as client:
        try:
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            
            response = await client.get(
                f"{AUTH_SERVICE_URL}/me",
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Failed to get user info")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )


@router.post("/logout")
async def logout(authorization: Optional[str] = None):
    """
    Logout current user.
    
    Proxies to auth service with authorization header.
    """
    async with httpx.AsyncClient() as client:
        try:
            headers = {}
            if authorization:
                headers["Authorization"] = authorization
            
            response = await client.post(
                f"{AUTH_SERVICE_URL}/logout",
                headers=headers,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(
                status_code=e.response.status_code,
                detail=e.response.json().get("detail", "Logout failed")
            )
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Auth service unavailable: {str(e)}"
            )
