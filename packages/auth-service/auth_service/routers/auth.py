"""
Authentication Router
"""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
import redis.asyncio as redis

from auth_service.database import get_db
from auth_service.models import User, Organization, OrganizationMember
from auth_service.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_wallet_challenge,
)
from auth_service.config import settings

router = APIRouter()
security = HTTPBearer()

# Redis client for challenges
redis_client = None


async def get_redis():
    """Get Redis client"""
    global redis_client
    if redis_client is None:
        redis_client = await redis.from_url(settings.REDIS_URL, decode_responses=True)
    return redis_client


# Request/Response Models
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class WalletChallengeRequest(BaseModel):
    wallet_address: str


class WalletVerifyRequest(BaseModel):
    wallet_address: str
    signature: str
    public_key: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    wallet_address: Optional[str]
    is_active: bool
    is_verified: bool
    created_at: datetime


# Helper functions
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Dependency to get current authenticated user"""
    token = credentials.credentials
    payload = decode_token(token)
    
    if not payload or payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )
    
    return user


# Endpoints
@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user with email and password"""
    
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == request.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    user = User(
        email=request.email,
        password_hash=hash_password(request.password),
        is_active=True,
        is_verified=False,
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    # Create personal organization
    org_slug = request.email.split("@")[0].lower().replace(".", "-")
    org = Organization(
        name=f"{request.email}'s Organization",
        slug=org_slug,
        owner_id=user.id,
    )
    db.add(org)
    
    # Add user as owner member
    member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role="owner",
    )
    db.add(member)
    
    await db.commit()
    
    # Generate tokens
    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_EXPIRATION_MINUTES * 60,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Login with email and password"""
    
    # Find user
    result = await db.execute(select(User).where(User.email == request.email))
    user = result.scalar_one_or_none()
    
    if not user or not user.password_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Verify password
    if not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive"
        )
    
    # Generate tokens
    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_EXPIRATION_MINUTES * 60,
    )


@router.post("/wallet/challenge")
async def wallet_challenge(request: WalletChallengeRequest):
    """Get a challenge for wallet signature authentication"""
    
    # Generate challenge
    challenge = generate_wallet_challenge()
    
    # Store challenge in Redis with TTL
    r = await get_redis()
    await r.setex(
        f"wallet_challenge:{request.wallet_address}",
        settings.WALLET_CHALLENGE_TTL,
        challenge
    )
    
    return {
        "challenge": challenge,
        "message": f"Sign this message to authenticate: {challenge}",
        "expires_in": settings.WALLET_CHALLENGE_TTL,
    }


@router.post("/wallet/verify", response_model=TokenResponse)
async def wallet_verify(request: WalletVerifyRequest, db: AsyncSession = Depends(get_db)):
    """Verify wallet signature and authenticate"""
    
    # Get challenge from Redis
    r = await get_redis()
    challenge = await r.get(f"wallet_challenge:{request.wallet_address}")
    
    if not challenge:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Challenge expired or not found. Request a new challenge."
        )
    
    # Verify Dilithium3 signature
    try:
        # NOTE: Dynamic path manipulation is a development workaround
        # In production, pq should be installed as a proper package dependency
        # TODO: Install pq module as a package dependency and remove sys.path manipulation
        import sys
        import os
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../"))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        
        from pq.py.algs.dilithium3 import verify
        
        # Decode hex signature and public key
        signature_bytes = bytes.fromhex(request.signature)
        public_key_bytes = bytes.fromhex(request.public_key)
        challenge_bytes = challenge.encode('utf-8')
        
        # Verify signature
        if not verify(public_key_bytes, challenge_bytes, signature_bytes):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature"
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid signature format: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Signature verification failed: {str(e)}"
        )
    
    # Find or create user
    result = await db.execute(
        select(User).where(User.wallet_address == request.wallet_address)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # Create new user with wallet
        user = User(
            email=f"{request.wallet_address[:8]}@wallet.animica.ai",
            wallet_address=request.wallet_address,
            wallet_public_key=request.public_key,
            is_active=True,
            is_verified=True,  # Wallet ownership proves identity
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        # Create personal organization
        org = Organization(
            name=f"Wallet {request.wallet_address[:8]}",
            slug=f"wallet-{request.wallet_address[:8]}".lower(),
            owner_id=user.id,
        )
        db.add(org)
        
        member = OrganizationMember(
            organization_id=org.id,
            user_id=user.id,
            role="owner",
        )
        db.add(member)
        await db.commit()
    
    # Delete challenge
    await r.delete(f"wallet_challenge:{request.wallet_address}")
    
    # Generate tokens
    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_EXPIRATION_MINUTES * 60,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """Refresh access token using refresh token"""
    
    payload = decode_token(request.refresh_token)
    
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    user_id = payload.get("sub")
    
    # Verify user exists and is active
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user"
        )
    
    # Generate new tokens
    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    new_refresh_token = create_refresh_token({"sub": str(user.id)})
    
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=settings.JWT_EXPIRATION_MINUTES * 60,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """Get current user information"""
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        wallet_address=current_user.wallet_address,
        is_active=current_user.is_active,
        is_verified=current_user.is_verified,
        created_at=current_user.created_at,
    )


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Logout user (client should discard tokens)"""
    # In a production system, you might want to blacklist the token
    return {"message": "Successfully logged out"}
