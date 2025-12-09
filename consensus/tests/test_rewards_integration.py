# SPDX-License-Identifier: Apache-2.0
"""
Integration tests for block rewards with params loaded from spec/params.yaml.

These tests verify that:
1. Mainnet params (chain_id=1) load correctly and produce 100% miner rewards
2. Reward calculation matches the 5 ANM base with halving schedule
3. Custom payout addresses are correctly handled
"""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from consensus.rewards import (
    compute_block_reward,
    parse_emission_schedule,
    compute_subsidy_for_height,
)


def load_mainnet_params() -> dict:
    """Load mainnet params from spec/params.yaml."""
    params_path = Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    with params_path.open("r") as f:
        params_yaml = yaml.safe_load(f)
    return params_yaml["networks"]["animica:1"]


def test_mainnet_params_100_pct_miner():
    """Test that mainnet params specify 100% miner subsidy split."""
    params = load_mainnet_params()
    split = params["monetary"]["issuance"]["subsidy_split_pct"]
    
    assert split["miner"] == 100, "Mainnet should give 100% to miner"
    assert split["aicf"] == 0, "Mainnet should give 0% to AICF"
    assert split["treasury"] == 0, "Mainnet should give 0% to treasury"


def test_mainnet_params_5_anm_base():
    """Test that mainnet params specify 5 ANM base reward."""
    params = load_mainnet_params()
    subsidy = params["monetary"]["issuance"]["subsidy"]
    
    # 5 ANM = 5_000_000_000 nANM (1 ANM = 10^9 nANM)
    assert subsidy["start_nANM_per_block"] == 5_000_000_000, \
        "Mainnet should have 5 ANM base reward"
    assert subsidy["epoch_length_blocks"] == 90_000_000, \
        "Mainnet should halve every 90M blocks"
    assert subsidy["decay_pct_per_epoch"] == 50.0, \
        "Mainnet should have 50% decay (true halving)"
    assert subsidy["tail_nANM_per_block"] == 100_000, \
        "Mainnet should have tail of 100_000 nANM"
    assert subsidy["max_halvings"] == 64, \
        "Mainnet should have 64 halvings"


def test_mainnet_block_reward_at_height_1():
    """Test that mainnet block reward at height 1 gives full 5 ANM to miner."""
    params = load_mainnet_params()
    
    # Compute block reward for mainnet at height 1
    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    # Should return exactly 1 reward entry (100% to miner)
    assert len(rewards) == 1, \
        f"Expected 1 reward entry (100% to miner), got {len(rewards)}"
    
    # Verify miner gets full 5 ANM (5_000_000_000 nANM)
    miner_addr, miner_amt = rewards[0]
    assert miner_amt == 5_000_000_000, \
        f"Expected 5 ANM (5_000_000_000 nANM), got {miner_amt}"
    assert "coinbase" in miner_addr.lower(), \
        f"Expected coinbase address, got {miner_addr}"


def test_mainnet_block_reward_halving_at_90m():
    """Test that mainnet block reward halves at 90M blocks."""
    params = load_mainnet_params()
    
    # Compute block reward at height 90_000_001 (first block of epoch 1)
    rewards = compute_block_reward(chain_id=1, height=90_000_001, params=params)
    
    # Should return exactly 1 reward entry (100% to miner)
    assert len(rewards) == 1, \
        f"Expected 1 reward entry (100% to miner), got {len(rewards)}"
    
    # Verify miner gets 2.5 ANM (half of 5 ANM)
    miner_addr, miner_amt = rewards[0]
    assert miner_amt == 2_500_000_000, \
        f"Expected 2.5 ANM (2_500_000_000 nANM) after halving, got {miner_amt}"


def test_mainnet_block_reward_second_halving_at_180m():
    """Test that mainnet block reward halves again at 180M blocks."""
    params = load_mainnet_params()
    
    # Compute block reward at height 180_000_001 (first block of epoch 2)
    rewards = compute_block_reward(chain_id=1, height=180_000_001, params=params)
    
    # Should return exactly 1 reward entry (100% to miner)
    assert len(rewards) == 1, \
        f"Expected 1 reward entry (100% to miner), got {len(rewards)}"
    
    # Verify miner gets 1.25 ANM (quarter of 5 ANM)
    miner_addr, miner_amt = rewards[0]
    assert miner_amt == 1_250_000_000, \
        f"Expected 1.25 ANM (1_250_000_000 nANM) after second halving, got {miner_amt}"


def test_mainnet_emission_schedule_parsing():
    """Test that mainnet emission schedule parses correctly."""
    params = load_mainnet_params()
    
    schedule = parse_emission_schedule(params)
    
    assert schedule["start_nANM_per_block"] == 5_000_000_000
    assert schedule["epoch_length_blocks"] == 90_000_000
    assert schedule["decay_pct_per_epoch"] == 50.0
    assert schedule["tail_nANM_per_block"] == 100_000
    assert schedule["max_halvings"] == 64
    assert schedule["miner_pct"] == 100
    assert schedule["aicf_pct"] == 0
    assert schedule["treasury_pct"] == 0


def test_mainnet_subsidy_computation_height_1():
    """Test subsidy computation for mainnet at height 1."""
    params = load_mainnet_params()
    schedule = parse_emission_schedule(params)
    
    miner, aicf, treasury = compute_subsidy_for_height(1, schedule)
    
    # Verify 100% goes to miner
    assert miner == 5_000_000_000, f"Expected 5 ANM to miner, got {miner}"
    assert aicf == 0, f"Expected 0 to AICF, got {aicf}"
    assert treasury == 0, f"Expected 0 to treasury, got {treasury}"
    
    # Verify total is 5 ANM
    total = miner + aicf + treasury
    assert total == 5_000_000_000, f"Expected total 5 ANM, got {total}"


def test_mainnet_subsidy_computation_height_90m_plus_1():
    """Test subsidy computation for mainnet after first halving."""
    params = load_mainnet_params()
    schedule = parse_emission_schedule(params)
    
    miner, aicf, treasury = compute_subsidy_for_height(90_000_001, schedule)
    
    # Verify 100% goes to miner (2.5 ANM after halving)
    assert miner == 2_500_000_000, f"Expected 2.5 ANM to miner, got {miner}"
    assert aicf == 0, f"Expected 0 to AICF, got {aicf}"
    assert treasury == 0, f"Expected 0 to treasury, got {treasury}"
    
    # Verify total is 2.5 ANM
    total = miner + aicf + treasury
    assert total == 2_500_000_000, f"Expected total 2.5 ANM, got {total}"


def test_devnet_still_has_split():
    """Test that devnet (chain_id=1337) still uses split distribution."""
    params_path = Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    with params_path.open("r") as f:
        params_yaml = yaml.safe_load(f)
    
    devnet_params = params_yaml["networks"]["animica:1337"]
    split = devnet_params["monetary"]["issuance"]["subsidy_split_pct"]
    
    # Devnet should NOT be 100% miner (should have split)
    assert split["miner"] != 100, "Devnet should NOT have 100% miner"
    assert split["miner"] + split["aicf"] + split["treasury"] == 100, \
        "Devnet split should sum to 100%"


def test_testnet_still_has_split():
    """Test that testnet (chain_id=2) still uses split distribution."""
    params_path = Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    with params_path.open("r") as f:
        params_yaml = yaml.safe_load(f)
    
    testnet_params = params_yaml["networks"]["animica:2"]
    split = testnet_params["monetary"]["issuance"]["subsidy_split_pct"]
    
    # Testnet should NOT be 100% miner (should have split)
    assert split["miner"] != 100, "Testnet should NOT have 100% miner"
    assert split["miner"] + split["aicf"] + split["treasury"] == 100, \
        "Testnet split should sum to 100%"
