"""
Database Models for Billing Service
"""

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from decimal import Decimal
import uuid

Base = declarative_base()


class CreditLedger(Base):
    """Credit ledger for tracking balance changes"""
    __tablename__ = "credit_ledger"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    amount = Column(Numeric(20, 8), nullable=False)
    balance_after = Column(Numeric(20, 8), nullable=False)
    type = Column(String(50), nullable=False)  # 'credit', 'debit', 'refund'
    reason = Column(String(255), nullable=True)
    reference_id = Column(String(255), nullable=True, index=True)
    metadata = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Subscription(Base):
    """Subscription plans"""
    __tablename__ = "subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    plan = Column(String(50), nullable=False)  # 'starter', 'pro', 'enterprise'
    status = Column(String(50), nullable=False)  # 'active', 'cancelled', 'expired'
    stripe_subscription_id = Column(String(255), unique=True, nullable=True)
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    cancel_at_period_end = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class PaymentMethod(Base):
    """Payment methods"""
    __tablename__ = "payment_methods"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    type = Column(String(50), nullable=False)  # 'card', 'paypal', 'wallet'
    stripe_payment_method_id = Column(String(255), unique=True, nullable=True)
    paypal_billing_agreement_id = Column(String(255), unique=True, nullable=True)
    wallet_address = Column(String(255), nullable=True)
    is_default = Column(Boolean, default=False)
    last_four = Column(String(4), nullable=True)
    brand = Column(String(50), nullable=True)
    exp_month = Column(Integer, nullable=True)
    exp_year = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Invoice(Base):
    """Invoices and receipts"""
    __tablename__ = "invoices"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    amount = Column(Numeric(20, 2), nullable=False)
    currency = Column(String(3), default="USD", nullable=False)
    status = Column(String(50), nullable=False)  # 'draft', 'paid', 'void', 'refunded'
    stripe_invoice_id = Column(String(255), unique=True, nullable=True)
    description = Column(Text, nullable=True)
    pdf_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    paid_at = Column(DateTime, nullable=True)


class UsageRecord(Base):
    """Usage tracking"""
    __tablename__ = "usage_records"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    organization_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    resource_type = Column(String(50), nullable=False)  # 'tokens', 'gpu_seconds', 'code_execution'
    quantity = Column(Numeric(20, 8), nullable=False)
    cost_credits = Column(Numeric(20, 8), nullable=False)
    metadata = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class ANMPaymentIntent(Base):
    """ANM token payment intents"""
    __tablename__ = "anm_payment_intents"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    amount_anm = Column(Numeric(20, 8), nullable=False)
    amount_credits = Column(Numeric(20, 8), nullable=False)
    wallet_address = Column(String(255), nullable=False)
    tx_hash = Column(String(255), unique=True, nullable=True, index=True)
    status = Column(String(50), nullable=False)  # 'pending', 'confirmed', 'failed', 'expired'
    confirmations = Column(Integer, default=0)
    required_confirmations = Column(Integer, default=6)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    confirmed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
