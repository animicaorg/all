"""
Organizations Router
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import List
from datetime import datetime

from auth_service.database import get_db
from auth_service.models import User, Organization, OrganizationMember
from auth_service.routers.auth import get_current_user

router = APIRouter()


# Request/Response Models
class OrganizationCreate(BaseModel):
    name: str
    slug: str


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str
    owner_id: str
    created_at: datetime


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: str
    role: str
    created_at: datetime


# Endpoints
@router.get("", response_model=List[OrganizationResponse])
async def list_organizations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List user's organizations"""
    
    result = await db.execute(
        select(Organization)
        .join(OrganizationMember)
        .where(OrganizationMember.user_id == current_user.id)
    )
    orgs = result.scalars().all()
    
    return [
        OrganizationResponse(
            id=str(org.id),
            name=org.name,
            slug=org.slug,
            owner_id=str(org.owner_id),
            created_at=org.created_at,
        )
        for org in orgs
    ]


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    request: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new organization"""
    
    # Check if slug is already taken
    result = await db.execute(select(Organization).where(Organization.slug == request.slug))
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization slug already taken"
        )
    
    # Create organization
    org = Organization(
        name=request.name,
        slug=request.slug,
        owner_id=current_user.id,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    
    # Add creator as owner
    member = OrganizationMember(
        organization_id=org.id,
        user_id=current_user.id,
        role="owner",
    )
    db.add(member)
    await db.commit()
    
    return OrganizationResponse(
        id=str(org.id),
        name=org.name,
        slug=org.slug,
        owner_id=str(org.owner_id),
        created_at=org.created_at,
    )


@router.get("/{org_id}/members", response_model=List[MemberResponse])
async def list_members(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """List organization members"""
    
    # Verify user is member
    result = await db.execute(
        select(OrganizationMember)
        .where(OrganizationMember.organization_id == org_id)
        .where(OrganizationMember.user_id == current_user.id)
    )
    membership = result.scalar_one_or_none()
    
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this organization"
        )
    
    # Get all members
    result = await db.execute(
        select(OrganizationMember, User)
        .join(User, OrganizationMember.user_id == User.id)
        .where(OrganizationMember.organization_id == org_id)
    )
    members = result.all()
    
    return [
        MemberResponse(
            id=str(member.OrganizationMember.id),
            user_id=str(member.User.id),
            email=member.User.email,
            role=member.OrganizationMember.role,
            created_at=member.OrganizationMember.created_at,
        )
        for member in members
    ]
