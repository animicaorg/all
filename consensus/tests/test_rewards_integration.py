# SPDX-License-Identifier: Apache-2.0
"""
Integration tests for block rewards with params loaded from spec/params.yaml.

These tests verify that:
1. Mainnet params (chain_id=1) load correctly and produce 100% miner rewards
2. Reward calculation matches the 300 ANM base with halving schedule
3. Custom payout addresses are correctly handled
"""

from __future__ import annotations

import pytest
import yaml
from pathlib import Path

from consensus.rewards import (
    MAINNET_PREMINE_TOTAL,
    MAX_MONEY,
    compute_block_reward,
    parse_emission_schedule,
    compute_subsidy_for_height,
)
from consensus import rewards as rewards_module


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


def test_mainnet_params_300_anm_base():
    """Test that mainnet params specify 300 ANM base reward."""
    params = load_mainnet_params()
    subsidy = params["monetary"]["issuance"]["subsidy"]
    
    # 300 ANM = 300_000_000_000 nANM (1 ANM = 10^9 nANM)
    assert subsidy["start_nANM_per_block"] == 300_000_000_000, \
        "Mainnet should have 300 ANM base reward"
    assert subsidy["epoch_length_blocks"] == 1_350_000, \
        "Mainnet should halve every 1.35M blocks"
    assert subsidy["decay_pct_per_epoch"] == 50.0, \
        "Mainnet should have 50% decay (true halving)"
    assert subsidy["tail_nANM_per_block"] == 100_000, \
        "Mainnet should have tail of 100_000 nANM"
    assert subsidy["max_halvings"] == 64, \
        "Mainnet should have 64 halvings"


def test_mainnet_block_reward_at_height_1():
    """Test that mainnet block reward at height 1 gives full 300 ANM to miner."""
    params = load_mainnet_params()
    
    # Compute block reward for mainnet at height 1
    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    # Should return exactly 1 reward entry (100% to miner)
    assert len(rewards) == 1, \
        f"Expected 1 reward entry (100% to miner), got {len(rewards)}"
    
    # Verify miner gets full 300 ANM (300_000_000_000 nANM)
    miner_addr, miner_amt = rewards[0]
    assert miner_amt == 300_000_000_000, \
        f"Expected 300 ANM (300_000_000_000 nANM), got {miner_amt}"
    assert "coinbase" in miner_addr.lower(), \
        f"Expected coinbase address, got {miner_addr}"


def test_mainnet_block_reward_halving_at_1_35m():
    """Mainnet block reward halves at 1.35M blocks (total 150 ANM), split 85/15
    post-7.1.0 (this height is above FORK_FOUNDATION_SPLIT @ 42_001)."""
    from consensus.rewards import FOUNDATION_TREASURY_ADDRESS
    params = load_mainnet_params()

    # Compute block reward at height 1_350_001 (first block of epoch 1)
    rewards = compute_block_reward(chain_id=1, height=1_350_001, params=params)

    # Post-7.1.0: miner + foundation treasury; total == halved subsidy (150 ANM).
    total = sum(amt for _, amt in rewards)
    assert total == 150_000_000_000, \
        f"Expected 150 ANM total after halving, got {total}"
    outs = dict(rewards)
    assert outs[FOUNDATION_TREASURY_ADDRESS] == 22_500_000_000  # 15% of 150 ANM
    assert total - outs[FOUNDATION_TREASURY_ADDRESS] == 127_500_000_000  # 85% miner


def test_mainnet_block_reward_second_halving_at_2_7m():
    """Mainnet block reward halves again at 2.7M blocks (total 75 ANM), split 85/15
    post-7.1.0."""
    from consensus.rewards import FOUNDATION_TREASURY_ADDRESS
    params = load_mainnet_params()

    # Compute block reward at height 2_700_001 (first block of epoch 2)
    rewards = compute_block_reward(chain_id=1, height=2_700_001, params=params)

    total = sum(amt for _, amt in rewards)
    assert total == 75_000_000_000, \
        f"Expected 75 ANM total after second halving, got {total}"
    outs = dict(rewards)
    assert outs[FOUNDATION_TREASURY_ADDRESS] == 11_250_000_000  # 15% of 75 ANM
    assert total - outs[FOUNDATION_TREASURY_ADDRESS] == 63_750_000_000  # 85% miner


def test_mainnet_emission_schedule_parsing():
    """Test that mainnet emission schedule parses correctly."""
    params = load_mainnet_params()
    
    schedule = parse_emission_schedule(params)
    
    assert schedule["start_nANM_per_block"] == 300_000_000_000
    assert schedule["epoch_length_blocks"] == 1_350_000
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
    assert miner == 300_000_000_000, f"Expected 300 ANM to miner, got {miner}"
    assert aicf == 0, f"Expected 0 to AICF, got {aicf}"
    assert treasury == 0, f"Expected 0 to treasury, got {treasury}"
    
    # Verify total is 300 ANM
    total = miner + aicf + treasury
    assert total == 300_000_000_000, f"Expected total 300 ANM, got {total}"


def test_mainnet_subsidy_computation_height_1_35m_plus_1():
    """Test subsidy computation for mainnet after first halving."""
    params = load_mainnet_params()
    schedule = parse_emission_schedule(params)
    
    miner, aicf, treasury = compute_subsidy_for_height(1_350_001, schedule)
    
    # Verify 100% goes to miner (150 ANM after halving)
    assert miner == 150_000_000_000, f"Expected 150 ANM to miner, got {miner}"
    assert aicf == 0, f"Expected 0 to AICF, got {aicf}"
    assert treasury == 0, f"Expected 0 to treasury, got {treasury}"
    
    # Verify total is 150 ANM
    total = miner + aicf + treasury
    assert total == 150_000_000_000, f"Expected total 150 ANM, got {total}"


def test_mainnet_supply_cap_clamps_rewards():
    """Test that mainnet rewards clamp once total supply cap is reached."""
    params = load_mainnet_params()
    schedule = parse_emission_schedule(params)
    cap = MAX_MONEY - MAINNET_PREMINE_TOTAL
    height = 10_000_000_000
    total_before = rewards_module._total_subsidy_through_height(height - 1, schedule)

    assert total_before >= cap, "Height should exceed total subsidy cap for test"

    rewards = compute_block_reward(
        chain_id=1, height=height, params=params, canonical_height=height
    )
    assert rewards == [], "Rewards should be zero once cap is reached"


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
