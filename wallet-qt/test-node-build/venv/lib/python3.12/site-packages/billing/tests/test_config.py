"""
Tests for billing configuration module.
"""

import os
import pytest

from billing.config import (
    AICFBillingConfig,
    BillingConfig,
    DAFeeConfig,
    PlanConfig,
    RPCFeeConfig,
    load_billing_config,
)


def test_plan_config_defaults():
    """Test PlanConfig default values."""
    plan = PlanConfig(
        name="test",
        rate_limit_rpm=100,
        da_fee_per_byte=0.001,
        rpc_fee_flat=0.01,
        aicf_rate_per_unit=0.1,
        aicf_free_units=1000,
    )
    
    assert plan.name == "test"
    assert plan.rate_limit_rpm == 100
    assert plan.da_fee_per_byte == 0.001
    assert plan.rpc_fee_flat == 0.01
    assert plan.aicf_rate_per_unit == 0.1
    assert plan.aicf_free_units == 1000


def test_da_fee_config_validation():
    """Test DAFeeConfig validation."""
    # Valid config
    config = DAFeeConfig(
        fee_per_byte=0.001,
        treasury_address="0x123",
        validator_split=0.8,
    )
    config.validate()  # Should not raise
    
    # Invalid fee_per_byte
    with pytest.raises(ValueError, match="fee_per_byte must be non-negative"):
        config = DAFeeConfig(
            fee_per_byte=-0.001,
            treasury_address="0x123",
            validator_split=0.8,
        )
        config.validate()
    
    # Invalid validator_split
    with pytest.raises(ValueError, match="validator_split must be between 0 and 1"):
        config = DAFeeConfig(
            fee_per_byte=0.001,
            treasury_address="0x123",
            validator_split=1.5,
        )
        config.validate()


def test_rpc_fee_config_validation():
    """Test RPCFeeConfig validation."""
    # Valid config
    config = RPCFeeConfig(
        fee_flat=0.01,
        treasury_address="0x123",
        validator_split=0.8,
    )
    config.validate()  # Should not raise
    
    # Invalid fee_flat
    with pytest.raises(ValueError, match="fee_flat must be non-negative"):
        config = RPCFeeConfig(
            fee_flat=-0.01,
            treasury_address="0x123",
            validator_split=0.8,
        )
        config.validate()


def test_aicf_billing_config_validation():
    """Test AICFBillingConfig validation."""
    # Valid config
    config = AICFBillingConfig(
        mode="free",
        rate_per_unit=0.1,
        free_units=1000,
    )
    config.validate()  # Should not raise
    
    # Invalid rate_per_unit
    with pytest.raises(ValueError, match="rate_per_unit must be non-negative"):
        config = AICFBillingConfig(
            mode="free",
            rate_per_unit=-0.1,
            free_units=1000,
        )
        config.validate()
    
    # Invalid free_units
    with pytest.raises(ValueError, match="free_units must be non-negative"):
        config = AICFBillingConfig(
            mode="free",
            rate_per_unit=0.1,
            free_units=-100,
        )
        config.validate()


def test_billing_config_defaults():
    """Test BillingConfig default values from load_billing_config."""
    # Clear any existing env vars
    for key in list(os.environ.keys()):
        if key.startswith("ANIMICA_"):
            del os.environ[key]
    
    config = load_billing_config()
    
    # Check defaults
    assert config.mode == "free"
    assert config.api_key_header == "x-animica-key"
    assert config.default_plan == "free"
    assert "free" in config.plans
    assert "pro" in config.plans
    assert "enterprise" in config.plans
    
    # Check plan defaults
    free_plan = config.plans["free"]
    assert free_plan.rate_limit_rpm == 60
    assert free_plan.da_fee_per_byte == 0.0
    assert free_plan.rpc_fee_flat == 0.0
    assert free_plan.aicf_rate_per_unit == 0.0
    assert free_plan.aicf_free_units == 1000
    
    # Check DA fee defaults
    assert config.da_fee.fee_per_byte == 0.0
    assert config.da_fee.validator_split == 1.0
    
    # Check RPC fee defaults
    assert config.rpc_fee.fee_flat == 0.0
    
    # Check AICF defaults
    assert config.aicf.mode == "free"
    assert config.aicf.rate_per_unit == 0.0
    assert config.aicf.free_units == 1000


def test_billing_config_env_overrides():
    """Test BillingConfig environment variable overrides."""
    # Set env vars
    os.environ["ANIMICA_BILLING_MODE"] = "paid"
    os.environ["ANIMICA_API_KEY_HEADER"] = "x-api-key"
    os.environ["ANIMICA_DEFAULT_PLAN"] = "pro"
    os.environ["ANIMICA_RATE_LIMIT_FREE"] = "100"
    os.environ["ANIMICA_RATE_LIMIT_PRO"] = "1000"
    os.environ["ANIMICA_DA_FEE_PER_BYTE"] = "0.001"
    os.environ["ANIMICA_RPC_FEE_FLAT"] = "0.01"
    os.environ["ANIMICA_FEE_TREASURY_ADDRESS"] = "0xTREASURY"
    os.environ["ANIMICA_FEE_VALIDATOR_SPLIT"] = "0.8"
    os.environ["ANIMICA_AICF_BILLING_MODE"] = "paid"
    os.environ["ANIMICA_AICF_RATE_PER_UNIT"] = "0.5"
    os.environ["ANIMICA_AICF_FREE_UNITS"] = "500"
    
    try:
        config = load_billing_config()
        
        # Check overrides
        assert config.mode == "paid"
        assert config.api_key_header == "x-api-key"
        assert config.default_plan == "pro"
        
        # Check plan overrides
        free_plan = config.plans["free"]
        assert free_plan.rate_limit_rpm == 100
        
        pro_plan = config.plans["pro"]
        assert pro_plan.rate_limit_rpm == 1000
        
        # Check DA fee overrides
        assert config.da_fee.fee_per_byte == 0.001
        assert config.da_fee.treasury_address == "0xTREASURY"
        assert config.da_fee.validator_split == 0.8
        
        # Check RPC fee overrides
        assert config.rpc_fee.fee_flat == 0.01
        
        # Check AICF overrides
        assert config.aicf.mode == "paid"
        assert config.aicf.rate_per_unit == 0.5
        assert config.aicf.free_units == 500
    finally:
        # Clean up
        for key in list(os.environ.keys()):
            if key.startswith("ANIMICA_"):
                del os.environ[key]


def test_billing_config_get_plan():
    """Test BillingConfig get_plan method."""
    config = load_billing_config()
    
    # Get existing plan
    free_plan = config.get_plan("free")
    assert free_plan.name == "free"
    
    # Get non-existent plan (should fall back to default)
    unknown_plan = config.get_plan("unknown")
    assert unknown_plan.name == config.default_plan


def test_billing_config_validation():
    """Test BillingConfig validation."""
    # Valid config
    config = load_billing_config()
    config.validate()  # Should not raise
    
    # Invalid default_plan
    config = BillingConfig(
        mode="free",
        api_key_header="x-api-key",
        default_plan="nonexistent",
        plans={
            "free": PlanConfig(
                name="free",
                rate_limit_rpm=60,
                da_fee_per_byte=0.0,
                rpc_fee_flat=0.0,
                aicf_rate_per_unit=0.0,
                aicf_free_units=1000,
            ),
        },
        da_fee=DAFeeConfig(
            fee_per_byte=0.0,
            treasury_address="",
            validator_split=1.0,
        ),
        rpc_fee=RPCFeeConfig(
            fee_flat=0.0,
            treasury_address="",
            validator_split=1.0,
        ),
        aicf=AICFBillingConfig(
            mode="free",
            rate_per_unit=0.0,
            free_units=1000,
        ),
    )
    
    with pytest.raises(ValueError, match="default_plan .* not in plans"):
        config.validate()
