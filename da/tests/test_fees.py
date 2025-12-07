"""
Tests for DA fee accounting.
"""

import os
import pytest

from da.fees import DAFeeReceipt, calculate_da_fee, format_fee_receipt


def test_da_fee_receipt():
    """Test DAFeeReceipt creation."""
    receipt = DAFeeReceipt(
        bytes_posted=1024,
        total_fee=1.024,
        validator_amount=0.8192,
        treasury_amount=0.2048,
        fee_per_byte=0.001,
        treasury_address="0xTREASURY",
        validator_split=0.8,
    )
    
    assert receipt.bytes_posted == 1024
    assert receipt.total_fee == 1.024
    assert receipt.validator_amount == 0.8192
    assert receipt.treasury_amount == 0.2048
    assert receipt.fee_per_byte == 0.001
    assert receipt.treasury_address == "0xTREASURY"
    assert receipt.validator_split == 0.8


def test_calculate_da_fee_free_mode():
    """Test calculate_da_fee in free mode (default)."""
    # Clear billing env vars
    for key in list(os.environ.keys()):
        if key.startswith("ANIMICA_"):
            del os.environ[key]
    
    receipt = calculate_da_fee(1024)
    
    assert receipt.bytes_posted == 1024
    assert receipt.total_fee == 0.0
    assert receipt.validator_amount == 0.0
    assert receipt.treasury_amount == 0.0
    assert receipt.fee_per_byte == 0.0


def test_calculate_da_fee_paid_mode():
    """Test calculate_da_fee in paid mode."""
    # Set up paid mode
    os.environ["ANIMICA_BILLING_MODE"] = "paid"
    os.environ["ANIMICA_DA_FEE_PER_BYTE"] = "0.001"
    os.environ["ANIMICA_FEE_TREASURY_ADDRESS"] = "0xTREASURY"
    os.environ["ANIMICA_FEE_VALIDATOR_SPLIT"] = "0.8"
    
    try:
        receipt = calculate_da_fee(1024)
        
        assert receipt.bytes_posted == 1024
        assert receipt.total_fee == pytest.approx(1.024, rel=1e-9)
        assert receipt.validator_amount == pytest.approx(0.8192, rel=1e-9)
        assert receipt.treasury_amount == pytest.approx(0.2048, rel=1e-9)
        assert receipt.fee_per_byte == 0.001
        assert receipt.treasury_address == "0xTREASURY"
        assert receipt.validator_split == 0.8
    finally:
        # Clean up
        for key in list(os.environ.keys()):
            if key.startswith("ANIMICA_"):
                del os.environ[key]


def test_calculate_da_fee_validation():
    """Test calculate_da_fee input validation."""
    with pytest.raises(ValueError, match="bytes_count must be non-negative"):
        calculate_da_fee(-100)


def test_format_fee_receipt():
    """Test format_fee_receipt."""
    receipt = DAFeeReceipt(
        bytes_posted=1024,
        total_fee=1.024,
        validator_amount=0.8192,
        treasury_amount=0.2048,
        fee_per_byte=0.001,
        treasury_address="0xTREASURY",
        validator_split=0.8,
    )
    
    formatted = format_fee_receipt(receipt)
    
    assert isinstance(formatted, dict)
    assert formatted["bytes_posted"] == 1024
    assert formatted["total_fee"] == 1.024
    assert formatted["validator_amount"] == 0.8192
    assert formatted["treasury_amount"] == 0.2048
    assert formatted["fee_per_byte"] == 0.001
    assert formatted["treasury_address"] == "0xTREASURY"
    assert formatted["validator_split"] == 0.8


def test_calculate_da_fee_zero_bytes():
    """Test calculate_da_fee with zero bytes."""
    receipt = calculate_da_fee(0)
    
    assert receipt.bytes_posted == 0
    assert receipt.total_fee == 0.0
    assert receipt.validator_amount == 0.0
    assert receipt.treasury_amount == 0.0


def test_calculate_da_fee_large_blob():
    """Test calculate_da_fee with large blob."""
    os.environ["ANIMICA_DA_FEE_PER_BYTE"] = "0.00001"
    os.environ["ANIMICA_FEE_VALIDATOR_SPLIT"] = "0.9"
    
    try:
        # 10 MB blob
        receipt = calculate_da_fee(10 * 1024 * 1024)
        
        assert receipt.bytes_posted == 10 * 1024 * 1024
        expected_fee = 10 * 1024 * 1024 * 0.00001
        assert receipt.total_fee == pytest.approx(expected_fee, rel=1e-9)
        assert receipt.validator_amount == pytest.approx(expected_fee * 0.9, rel=1e-9)
        assert receipt.treasury_amount == pytest.approx(expected_fee * 0.1, rel=1e-9)
    finally:
        # Clean up
        for key in list(os.environ.keys()):
            if key.startswith("ANIMICA_"):
                del os.environ[key]
