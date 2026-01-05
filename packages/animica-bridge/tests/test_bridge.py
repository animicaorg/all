"""
Tests for Animica Bridge Service
"""

import pytest
from decimal import Decimal
from animica_bridge.payment_processor import PaymentProcessor
from animica_bridge.receipt_submitter import ReceiptSubmitter
from animica_bridge.config import settings


@pytest.fixture
def payment_processor():
    """Create payment processor instance"""
    return PaymentProcessor()


@pytest.fixture
def receipt_submitter():
    """Create receipt submitter instance"""
    return ReceiptSubmitter(
        rpc_url="http://localhost:8545",
        private_key="test_key"
    )


@pytest.mark.asyncio
async def test_create_payment_intent(payment_processor):
    """Test payment intent creation"""
    intent = await payment_processor.create_payment_intent(
        user_id="test_user",
        amount_anm=Decimal("1.0")
    )
    
    assert "payment_id" in intent
    assert intent["amount_anm"] == 1.0
    assert intent["status"] == "pending"
    assert "payment_address" in intent


@pytest.mark.asyncio
async def test_create_receipt(receipt_submitter):
    """Test receipt creation"""
    receipt = await receipt_submitter.create_receipt(
        job_id="test_job_123",
        result={"output": "test"},
        metadata={"model": "test-model"}
    )
    
    assert receipt["job_id"] == "test_job_123"
    assert "receipt_hash" in receipt
    assert receipt["status"] == "pending"
    assert len(receipt["receipt_hash"]) == 64  # SHA-256 hex


def test_credits_conversion():
    """Test ANM to credits conversion"""
    anm_amount = Decimal("10.0")
    expected_credits = anm_amount * Decimal(settings.CREDITS_PER_ANM)
    
    assert expected_credits == Decimal("10000.0")  # 10 ANM * 1000 credits/ANM


def test_payment_confirmation_blocks():
    """Test confirmation blocks configuration"""
    assert settings.CONFIRMATION_BLOCKS == 3
    assert settings.POLLING_INTERVAL == 12
