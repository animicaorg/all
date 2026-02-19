"""
Tests for AICF economic routing.
"""

import pytest
from aicf.economics.routing import (
    EconomicRoutingConfig,
    DEFAULT_CONFIG,
    compute_block_reward_split,
    compute_tx_fee_split,
    compute_ena_fee_split,
)


def test_default_config():
    """Test default configuration."""
    config = DEFAULT_CONFIG
    
    # Block rewards: 10% AICF, 90% miner
    assert config.block_reward_aicf_bps == 1000
    assert config.block_reward_miner_bps == 9000
    assert config.block_reward_treasury_bps == 0
    
    # Tx fees: 20% AICF, 70% operator, 10% burn
    assert config.tx_fee_aicf_bps == 2000
    assert config.tx_fee_operator_bps == 7000
    assert config.tx_fee_burn_bps == 1000
    
    # ENA fees: 70% AICF, 20% operator, 10% burn
    assert config.ena_fee_aicf_bps == 7000
    assert config.ena_fee_operator_bps == 2000
    assert config.ena_fee_burn_bps == 1000


def test_config_validation():
    """Test configuration validation."""
    # Valid config
    config = DEFAULT_CONFIG
    is_valid, error = config.validate()
    assert is_valid is True
    assert error is None
    
    # Invalid: block rewards exceed 100%
    bad_config = EconomicRoutingConfig(
        block_reward_aicf_bps=6000,
        block_reward_miner_bps=6000,
    )
    is_valid, error = bad_config.validate()
    assert is_valid is False
    assert "exceeds 100%" in error
    
    # Invalid: negative value
    bad_config = EconomicRoutingConfig(
        block_reward_aicf_bps=-100,
    )
    is_valid, error = bad_config.validate()
    assert is_valid is False
    assert "cannot be negative" in error


def test_compute_block_reward_split():
    """Test block reward splitting."""
    total_reward = 300_000_000_000  # 300 ANM in base units
    
    # With default config (10% AICF, 90% miner)
    miner, aicf, treasury = compute_block_reward_split(total_reward)
    
    assert aicf == 30_000_000_000  # 10% = 30 ANM
    assert treasury == 0
    assert miner == 270_000_000_000  # 90% = 270 ANM
    assert miner + aicf + treasury == total_reward


def test_compute_block_reward_split_custom():
    """Test block reward splitting with custom config."""
    total_reward = 100_000_000_000  # 100 ANM
    
    # Custom config: 20% AICF, 70% miner, 10% treasury
    config = EconomicRoutingConfig(
        block_reward_aicf_bps=2000,
        block_reward_miner_bps=7000,
        block_reward_treasury_bps=1000,
    )
    
    miner, aicf, treasury = compute_block_reward_split(total_reward, config)
    
    assert aicf == 20_000_000_000  # 20%
    assert treasury == 10_000_000_000  # 10%
    assert miner == 70_000_000_000  # 70%
    assert miner + aicf + treasury == total_reward


def test_compute_tx_fee_split():
    """Test transaction fee splitting."""
    total_fee = 1_000_000  # 0.001 ANM in base units
    
    # With default config (20% AICF, 70% operator, 10% burn)
    operator, aicf, burn = compute_tx_fee_split(total_fee)
    
    assert aicf == 200_000  # 20%
    assert burn == 100_000  # 10%
    assert operator == 700_000  # 70%
    assert operator + aicf + burn == total_fee


def test_compute_tx_fee_split_custom():
    """Test transaction fee splitting with custom config."""
    total_fee = 10_000_000  # 0.01 ANM
    
    # Custom config: 50% AICF, 30% operator, 20% burn
    config = EconomicRoutingConfig(
        tx_fee_aicf_bps=5000,
        tx_fee_operator_bps=3000,
        tx_fee_burn_bps=2000,
    )
    
    operator, aicf, burn = compute_tx_fee_split(total_fee, config)
    
    assert aicf == 5_000_000  # 50%
    assert burn == 2_000_000  # 20%
    assert operator == 3_000_000  # 30%
    assert operator + aicf + burn == total_fee


def test_compute_ena_fee_split():
    """Test ENA call fee splitting."""
    total_fee = 100_000  # ENA call fee in base units
    
    # With default config (70% AICF, 20% operator, 10% reserve)
    aicf, operator, reserve = compute_ena_fee_split(total_fee)
    
    assert aicf == 70_000  # 70%
    assert operator == 20_000  # 20%
    assert reserve == 10_000  # 10%
    assert aicf + operator + reserve == total_fee


def test_compute_ena_fee_split_custom():
    """Test ENA call fee splitting with custom config."""
    total_fee = 1_000_000
    
    # Custom config: 80% AICF, 15% operator, 5% reserve
    config = EconomicRoutingConfig(
        ena_fee_aicf_bps=8000,
        ena_fee_operator_bps=1500,
        ena_fee_burn_bps=500,
    )
    
    aicf, operator, reserve = compute_ena_fee_split(total_fee, config)
    
    assert aicf == 800_000  # 80%
    assert operator == 150_000  # 15%
    assert reserve == 50_000  # 5%
    assert aicf + operator + reserve == total_fee


def test_split_rounding():
    """Test that splitting handles rounding correctly."""
    # Small amount that won't divide evenly
    total = 100
    
    miner, aicf, treasury = compute_block_reward_split(total)
    
    # Should preserve total
    assert miner + aicf + treasury == total
    
    # AICF should get 10% truncated
    assert aicf == 10
    assert miner == 90


def test_zero_amounts():
    """Test splitting zero amounts."""
    # Zero reward
    miner, aicf, treasury = compute_block_reward_split(0)
    assert miner == 0
    assert aicf == 0
    assert treasury == 0
    
    # Zero fee
    operator, aicf, burn = compute_tx_fee_split(0)
    assert operator == 0
    assert aicf == 0
    assert burn == 0
