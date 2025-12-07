"""
Tests for billing utilities.
"""

import pytest

from billing.utils import (
    FeeSplit,
    compute_aicf_cost,
    compute_da_fee,
    compute_fee_split,
    validate_api_key,
)


def test_compute_da_fee():
    """Test compute_da_fee function."""
    # Zero fee
    fee = compute_da_fee(1024, 0.0)
    assert fee == 0.0
    
    # Positive fee
    fee = compute_da_fee(1024, 0.001)
    assert fee == 1.024
    
    # Negative bytes should raise
    with pytest.raises(ValueError, match="bytes_count must be non-negative"):
        compute_da_fee(-100, 0.001)
    
    # Negative fee_per_byte should raise
    with pytest.raises(ValueError, match="fee_per_byte must be non-negative"):
        compute_da_fee(1024, -0.001)


def test_compute_fee_split():
    """Test compute_fee_split function."""
    # 80/20 split
    split = compute_fee_split(100.0, 0.8)
    assert split.total_fee == 100.0
    assert split.validator_amount == pytest.approx(80.0, rel=1e-9)
    assert split.treasury_amount == pytest.approx(20.0, rel=1e-9)
    assert split.validator_split == 0.8
    
    # 100% to validators
    split = compute_fee_split(100.0, 1.0)
    assert split.validator_amount == pytest.approx(100.0, rel=1e-9)
    assert split.treasury_amount == pytest.approx(0.0, abs=1e-9)
    
    # 100% to treasury
    split = compute_fee_split(100.0, 0.0)
    assert split.validator_amount == pytest.approx(0.0, abs=1e-9)
    assert split.treasury_amount == pytest.approx(100.0, rel=1e-9)
    
    # Negative fee should raise
    with pytest.raises(ValueError, match="total_fee must be non-negative"):
        compute_fee_split(-100.0, 0.8)
    
    # Invalid split should raise
    with pytest.raises(ValueError, match="validator_split must be between 0 and 1"):
        compute_fee_split(100.0, 1.5)


def test_validate_api_key_no_validation_dict():
    """Test validate_api_key with no validation dict (free mode)."""
    # Any non-empty key should be valid
    is_valid, plan = validate_api_key("any_key", None)
    assert is_valid
    assert plan == "free"
    
    # Empty key should be invalid
    is_valid, plan = validate_api_key("", None)
    assert not is_valid
    assert plan is None


def test_validate_api_key_with_validation_dict():
    """Test validate_api_key with validation dict (paid mode)."""
    valid_keys = {
        "free_key": "free",
        "pro_key": "pro",
        "enterprise_key": "enterprise",
    }
    
    # Valid keys
    is_valid, plan = validate_api_key("free_key", valid_keys)
    assert is_valid
    assert plan == "free"
    
    is_valid, plan = validate_api_key("pro_key", valid_keys)
    assert is_valid
    assert plan == "pro"
    
    # Invalid key
    is_valid, plan = validate_api_key("invalid_key", valid_keys)
    assert not is_valid
    assert plan is None
    
    # Empty key
    is_valid, plan = validate_api_key("", valid_keys)
    assert not is_valid
    assert plan is None


def test_compute_aicf_cost_no_free_tier():
    """Test compute_aicf_cost with no free tier."""
    cost, billable = compute_aicf_cost(
        units=100,
        rate_per_unit=0.5,
        free_units=0,
        units_used=0,
    )
    assert billable == 100
    assert cost == 50.0


def test_compute_aicf_cost_with_free_tier():
    """Test compute_aicf_cost with free tier."""
    # All units covered by free tier
    cost, billable = compute_aicf_cost(
        units=100,
        rate_per_unit=0.5,
        free_units=1000,
        units_used=0,
    )
    assert billable == 0
    assert cost == 0.0
    
    # Partially covered by free tier
    cost, billable = compute_aicf_cost(
        units=200,
        rate_per_unit=0.5,
        free_units=1000,
        units_used=900,
    )
    assert billable == 100  # 200 - (1000 - 900)
    assert cost == 50.0
    
    # Free tier exhausted
    cost, billable = compute_aicf_cost(
        units=100,
        rate_per_unit=0.5,
        free_units=1000,
        units_used=1000,
    )
    assert billable == 100
    assert cost == 50.0


def test_compute_aicf_cost_validation():
    """Test compute_aicf_cost input validation."""
    # Negative units
    with pytest.raises(ValueError, match="units must be non-negative"):
        compute_aicf_cost(-100, 0.5, 1000, 0)
    
    # Negative rate
    with pytest.raises(ValueError, match="rate_per_unit must be non-negative"):
        compute_aicf_cost(100, -0.5, 1000, 0)
    
    # Negative free_units
    with pytest.raises(ValueError, match="free_units must be non-negative"):
        compute_aicf_cost(100, 0.5, -1000, 0)
    
    # Negative units_used
    with pytest.raises(ValueError, match="units_used must be non-negative"):
        compute_aicf_cost(100, 0.5, 1000, -100)


def test_fee_split_dataclass():
    """Test FeeSplit dataclass."""
    split = FeeSplit(
        total_fee=100.0,
        validator_amount=80.0,
        treasury_amount=20.0,
        validator_split=0.8,
    )
    
    assert split.total_fee == 100.0
    assert split.validator_amount == 80.0
    assert split.treasury_amount == 20.0
    assert split.validator_split == 0.8
