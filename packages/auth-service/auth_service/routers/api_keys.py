"""
API Keys Router
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from auth_service.database import get_db
from auth_service.models import User, APIKey
from auth_service.security import generate_api_key
from auth_service.routers.auth import get_current_user

router = APIRouter()


# Request/Response Models
class APIKeyCreate(BaseModel):
    name: str
    organization_id: Optional[str] = None


class APIKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: Optional[datetime]
    is_active: bool


class APIKeyCreateResponse(BaseModel):
    id: str
    name: str
    key: str  # Only returned once
    prefix: str
    created_at: datetime


# Endpoints
@router.get("", response_model=List[APIKeyResponse])
async def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's API keys"""
    
    result = await db.execute(
        select(APIKey)
        .where(APIKey.user_id == current_user.id)
        .where(APIKey.is_active == True)
    )
    keys = result.scalars().all()
    
    return [
        APIKeyResponse(
            id=str(key.id),
            name=key.name,
            prefix=key.prefix,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            is_active=key.is_active,
        )
        for key in keys
    ]


@router.post("", response_model=APIKeyCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    request: APIKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new API key"""
    
    # Generate key
    full_key, prefix, key_hash = generate_api_key()
    
    # Create API key record
    api_key = APIKey(
        user_id=current_user.id,
        organization_id=request.organization_id,
        name=request.name,
        key_hash=key_hash,
        prefix=prefix,
        is_active=True,
    )
    
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    
    return APIKeyCreateResponse(
        id=str(api_key.id),
        name=api_key.name,
        key=full_key,  # Return full key only once
        prefix=prefix,
        created_at=api_key.created_at,
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Revoke an API key"""
    
    result = await db.execute(
        select(APIKey)
        .where(APIKey.id == key_id)
        .where(APIKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )
    
    # Soft delete
    api_key.is_active = False
    await db.commit()
    
    return None
