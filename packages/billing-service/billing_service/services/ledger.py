"""
Credit ledger service for managing user balances
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from decimal import Decimal
from typing import Optional
from billing_service.models import CreditLedger
import uuid


async def get_balance(db: AsyncSession, user_id: str, organization_id: Optional[str] = None) -> Decimal:
    """Get current balance for user/org"""
    
    query = select(CreditLedger).where(CreditLedger.user_id == user_id)
    
    if organization_id:
        query = query.where(CreditLedger.organization_id == organization_id)
    
    query = query.order_by(CreditLedger.created_at.desc()).limit(1)
    
    result = await db.execute(query)
    latest = result.scalar_one_or_none()
    
    if latest:
        return latest.balance_after
    else:
        return Decimal(0)


async def add_credits(
    db: AsyncSession,
    user_id: str,
    amount: Decimal,
    reason: str,
    reference_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> CreditLedger:
    """Add credits to user balance"""
    
    # Get current balance
    current_balance = await get_balance(db, user_id, organization_id)
    new_balance = current_balance + amount
    
    # Create ledger entry
    entry = CreditLedger(
        user_id=user_id,
        organization_id=organization_id,
        amount=amount,
        balance_after=new_balance,
        type="credit",
        reason=reason,
        reference_id=reference_id,
        metadata=metadata,
    )
    
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    
    return entry


async def deduct_credits(
    db: AsyncSession,
    user_id: str,
    amount: Decimal,
    reason: str,
    reference_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> CreditLedger:
    """Deduct credits from user balance"""
    
    # Get current balance
    current_balance = await get_balance(db, user_id, organization_id)
    
    if current_balance < amount:
        raise ValueError(f"Insufficient credits. Current balance: {current_balance}, required: {amount}")
    
    new_balance = current_balance - amount
    
    # Create ledger entry
    entry = CreditLedger(
        user_id=user_id,
        organization_id=organization_id,
        amount=-amount,
        balance_after=new_balance,
        type="debit",
        reason=reason,
        reference_id=reference_id,
        metadata=metadata,
    )
    
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    
    return entry


async def refund_credits(
    db: AsyncSession,
    user_id: str,
    amount: Decimal,
    reason: str,
    reference_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    metadata: Optional[dict] = None
) -> CreditLedger:
    """Refund credits to user balance"""
    
    current_balance = await get_balance(db, user_id, organization_id)
    new_balance = current_balance + amount
    
    entry = CreditLedger(
        user_id=user_id,
        organization_id=organization_id,
        amount=amount,
        balance_after=new_balance,
        type="refund",
        reason=reason,
        reference_id=reference_id,
        metadata=metadata,
    )
    
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    
    return entry
