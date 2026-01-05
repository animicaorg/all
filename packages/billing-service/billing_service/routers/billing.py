"""
Billing Router
"""

from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from decimal import Decimal

from billing_service.database import get_db
from billing_service.services import ledger
from billing_service.models import CreditLedger, UsageRecord

router = APIRouter()


# Request/Response Models
class BalanceResponse(BaseModel):
    balance: Decimal
    currency: str = "credits"


class PurchaseCreditsRequest(BaseModel):
    amount: Decimal
    payment_method_id: str


class RecordUsageRequest(BaseModel):
    resource_type: str  # 'tokens', 'gpu_seconds', 'code_execution'
    quantity: Decimal
    cost_credits: Decimal
    metadata: Optional[dict] = None


class UsageResponse(BaseModel):
    id: str
    resource_type: str
    quantity: Decimal
    cost_credits: Decimal
    created_at: datetime


# Helper to get user ID from API key or JWT
async def get_current_user_id(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> str:
    """Extract user ID from auth header or API key"""
    # TODO: Integrate with auth-service to validate token/key
    # For now, return a mock user ID
    if authorization:
        # Parse JWT and extract user_id
        # token = authorization.replace("Bearer ", "")
        # user_id = decode_token(token)["sub"]
        return "00000000-0000-0000-0000-000000000001"  # Mock
    elif x_api_key:
        # Validate API key and get user_id
        return "00000000-0000-0000-0000-000000000001"  # Mock
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )


# Endpoints
@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Get current credit balance"""
    
    balance = await ledger.get_balance(db, user_id)
    
    return BalanceResponse(balance=balance)


@router.get("/balance/history", response_model=List[dict])
async def get_balance_history(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    limit: int = 100
):
    """Get balance history"""
    
    result = await db.execute(
        select(CreditLedger)
        .where(CreditLedger.user_id == user_id)
        .order_by(CreditLedger.created_at.desc())
        .limit(limit)
    )
    
    entries = result.scalars().all()
    
    return [
        {
            "id": str(entry.id),
            "amount": float(entry.amount),
            "balance_after": float(entry.balance_after),
            "type": entry.type,
            "reason": entry.reason,
            "created_at": entry.created_at.isoformat(),
        }
        for entry in entries
    ]


@router.post("/credits/purchase")
async def purchase_credits(
    request: PurchaseCreditsRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Purchase credits via Stripe"""
    
    # TODO: Process Stripe payment
    # stripe.PaymentIntent.create(...)
    
    # Add credits to user balance
    entry = await ledger.add_credits(
        db,
        user_id=user_id,
        amount=request.amount,
        reason="Credit purchase",
        reference_id=request.payment_method_id,
        metadata={"payment_method": request.payment_method_id}
    )
    
    return {
        "success": True,
        "credits_added": float(request.amount),
        "new_balance": float(entry.balance_after)
    }


@router.post("/usage/record")
async def record_usage(
    request: RecordUsageRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Record resource usage and deduct credits"""
    
    # Deduct credits
    entry = await ledger.deduct_credits(
        db,
        user_id=user_id,
        amount=request.cost_credits,
        reason=f"Usage: {request.resource_type}",
        metadata=request.metadata
    )
    
    # Record usage
    usage = UsageRecord(
        user_id=user_id,
        resource_type=request.resource_type,
        quantity=request.quantity,
        cost_credits=request.cost_credits,
        metadata=request.metadata
    )
    
    db.add(usage)
    await db.commit()
    await db.refresh(usage)
    
    return {
        "success": True,
        "credits_deducted": float(request.cost_credits),
        "new_balance": float(entry.balance_after)
    }


@router.get("/usage", response_model=List[UsageResponse])
async def get_usage(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    limit: int = 100
):
    """Get usage history"""
    
    result = await db.execute(
        select(UsageRecord)
        .where(UsageRecord.user_id == user_id)
        .order_by(UsageRecord.created_at.desc())
        .limit(limit)
    )
    
    records = result.scalars().all()
    
    return [
        UsageResponse(
            id=str(record.id),
            resource_type=record.resource_type,
            quantity=record.quantity,
            cost_credits=record.cost_credits,
            created_at=record.created_at
        )
        for record in records
    ]
