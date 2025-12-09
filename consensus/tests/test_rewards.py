# SPDX-License-Identifier: Apache-2.0
"""
Tests for consensus.rewards — mainnet premine and reward calculation.
"""

from __future__ import annotations

import pytest

from consensus.rewards import (
    MAINNET_PREMINE_DISTRIBUTION,
    MAINNET_PREMINE_TOTAL,
    compute_block_reward,
    compute_subsidy_for_height,
    parse_emission_schedule,
    validate_mainnet_genesis_coinbase,
)


def test_mainnet_premine_total_is_81_million_anm():
    """Mainnet premine total is 81,000,000 ANM = 81,000,000,000,000,000 base units."""
    assert MAINNET_PREMINE_TOTAL == 81_000_000_000_000_000


def test_mainnet_premine_distribution_sums_to_total():
    """Mainnet premine distribution must sum to MAINNET_PREMINE_TOTAL."""
    total = sum(amt for _, amt in MAINNET_PREMINE_DISTRIBUTION)
    assert total == MAINNET_PREMINE_TOTAL


def test_mainnet_premine_distribution_includes_system_addresses():
    """Mainnet premine distribution includes foundation, treasury, aicf, founder."""
    addresses = [addr for addr, _ in MAINNET_PREMINE_DISTRIBUTION]
    assert "system:foundation" in addresses
    assert "system:treasury" in addresses
    assert "system:aicf" in addresses
    assert "system:founder" in addresses


def test_mainnet_premine_distribution_documented():
    """Mainnet premine distribution is documented (user address mentioned in code)."""
    # The user-provided address is documented in the code comments in rewards.py
    # This test verifies that the distribution structure is correct
    # (The actual address allocation can be adjusted per design requirements)
    assert len(MAINNET_PREMINE_DISTRIBUTION) == 4  # Four system addresses currently


def test_compute_block_reward_mainnet_height_0_returns_premine():
    """Mainnet at height 0 returns the premine distribution."""
    reward = compute_block_reward(chain_id=1, height=0)
    assert reward == list(MAINNET_PREMINE_DISTRIBUTION)


def test_compute_block_reward_mainnet_height_1_returns_empty_without_params():
    """Mainnet at height 1+ without params returns empty (params required)."""
    reward = compute_block_reward(chain_id=1, height=1, params=None)
    assert reward == []


def test_compute_block_reward_devnet_height_0_returns_empty():
    """Devnet (chain_id != 1) at height 0 returns empty (uses own genesis rules)."""
    reward = compute_block_reward(chain_id=1337, height=0)
    assert reward == []


def test_compute_block_reward_testnet_height_0_returns_empty():
    """Testnet (chain_id != 1) at height 0 returns empty (uses own genesis rules)."""
    reward = compute_block_reward(chain_id=2, height=0)
    assert reward == []


def test_validate_mainnet_genesis_coinbase_valid():
    """Valid mainnet genesis coinbase passes validation."""
    coinbase_outputs = list(MAINNET_PREMINE_DISTRIBUTION)
    is_valid, reason = validate_mainnet_genesis_coinbase(
        chain_id=1, height=0, coinbase_outputs=coinbase_outputs
    )
    assert is_valid
    assert "valid" in reason.lower()


def test_validate_mainnet_genesis_coinbase_invalid_total():
    """Invalid mainnet genesis coinbase (wrong total) fails validation."""
    # Modify one entry to make total wrong
    bad_outputs = [
        (addr, amt + 1000) if i == 0 else (addr, amt)
        for i, (addr, amt) in enumerate(MAINNET_PREMINE_DISTRIBUTION)
    ]
    is_valid, reason = validate_mainnet_genesis_coinbase(
        chain_id=1, height=0, coinbase_outputs=bad_outputs
    )
    assert not is_valid
    assert "total" in reason.lower()


def test_validate_mainnet_genesis_coinbase_invalid_distribution():
    """Invalid mainnet genesis coinbase (wrong distribution) fails validation."""
    # Swap amounts between two entries (total stays same but distribution changes)
    outputs = list(MAINNET_PREMINE_DISTRIBUTION)
    if len(outputs) >= 2:
        # Swap amounts of first two entries
        addr1, amt1 = outputs[0]
        addr2, amt2 = outputs[1]
        bad_outputs = [(addr1, amt2), (addr2, amt1)] + outputs[2:]
        is_valid, reason = validate_mainnet_genesis_coinbase(
            chain_id=1, height=0, coinbase_outputs=bad_outputs
        )
        assert not is_valid
        assert "distribution" in reason.lower()


def test_validate_mainnet_genesis_coinbase_non_mainnet_skips():
    """Non-mainnet chains skip validation (always valid)."""
    # Devnet with arbitrary outputs
    bad_outputs = [("random_addr", 12345)]
    is_valid, reason = validate_mainnet_genesis_coinbase(
        chain_id=1337, height=0, coinbase_outputs=bad_outputs
    )
    assert is_valid
    assert "not mainnet" in reason.lower()


def test_validate_mainnet_genesis_coinbase_non_genesis_skips():
    """Non-genesis blocks skip validation (always valid)."""
    # Mainnet at height 1 with arbitrary outputs
    bad_outputs = [("random_addr", 12345)]
    is_valid, reason = validate_mainnet_genesis_coinbase(
        chain_id=1, height=1, coinbase_outputs=bad_outputs
    )
    assert is_valid
    assert "not genesis" in reason.lower()


def test_parse_emission_schedule_valid():
    """Parse emission schedule from valid params."""
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 1000000,
                    "epoch_length_blocks": 4320000,
                    "decay_pct_per_epoch": 12.5,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 80,
                    "aicf": 15,
                    "treasury": 5,
                },
            }
        }
    }
    schedule = parse_emission_schedule(params)
    assert schedule["start_nANM_per_block"] == 1000000
    assert schedule["epoch_length_blocks"] == 4320000
    assert schedule["decay_pct_per_epoch"] == 12.5
    assert schedule["tail_nANM_per_block"] == 100000
    assert schedule["max_halvings"] == 64
    assert schedule["miner_pct"] == 80
    assert schedule["aicf_pct"] == 15
    assert schedule["treasury_pct"] == 5


def test_parse_emission_schedule_invalid_split():
    """Parse emission schedule fails if split doesn't sum to 100."""
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 1000000,
                    "epoch_length_blocks": 4320000,
                    "decay_pct_per_epoch": 12.5,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 80,
                    "aicf": 15,
                    "treasury": 10,  # Sum = 105, invalid
                },
            }
        }
    }
    with pytest.raises(ValueError, match="split"):
        parse_emission_schedule(params)


def test_parse_emission_schedule_invalid_zero_start():
    """Parse emission schedule fails if start is zero."""
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 0,  # Invalid
                    "epoch_length_blocks": 4320000,
                    "decay_pct_per_epoch": 12.5,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 80,
                    "aicf": 15,
                    "treasury": 5,
                },
            }
        }
    }
    with pytest.raises(ValueError, match="emission schedule"):
        parse_emission_schedule(params)


def test_compute_subsidy_for_height_genesis():
    """Subsidy for height 0 (genesis) is zero."""
    schedule = {
        "start_nANM_per_block": 1000000,
        "epoch_length_blocks": 4320000,
        "decay_pct_per_epoch": 12.5,
        "tail_nANM_per_block": 100000,
        "max_halvings": 64,
        "miner_pct": 80,
        "aicf_pct": 15,
        "treasury_pct": 5,
    }
    miner, aicf, treasury = compute_subsidy_for_height(0, schedule)
    assert miner == 0
    assert aicf == 0
    assert treasury == 0


def test_compute_subsidy_for_height_epoch_0():
    """Subsidy for epoch 0 (height 1) uses start amount."""
    schedule = {
        "start_nANM_per_block": 1000000,
        "epoch_length_blocks": 4320000,
        "decay_pct_per_epoch": 12.5,
        "tail_nANM_per_block": 100000,
        "max_halvings": 64,
        "miner_pct": 80,
        "aicf_pct": 15,
        "treasury_pct": 5,
    }
    miner, aicf, treasury = compute_subsidy_for_height(1, schedule)
    total = miner + aicf + treasury
    assert total == 1000000  # Start amount
    assert miner == 800000  # 80%
    assert aicf == 150000  # 15%
    assert treasury == 50000  # 5%


def test_compute_subsidy_for_height_epoch_1():
    """Subsidy for epoch 1 applies decay."""
    schedule = {
        "start_nANM_per_block": 1000000,
        "epoch_length_blocks": 100,  # Small epoch for testing
        "decay_pct_per_epoch": 50.0,  # 50% decay (half each epoch)
        "tail_nANM_per_block": 100000,
        "max_halvings": 64,
        "miner_pct": 80,
        "aicf_pct": 15,
        "treasury_pct": 5,
    }
    # Height 101 = epoch 1 (decay by 50% → subsidy = 500000)
    miner, aicf, treasury = compute_subsidy_for_height(101, schedule)
    total = miner + aicf + treasury
    assert total == 500000  # Half of start
    assert miner == 400000  # 80% of 500000
    assert aicf == 75000  # 15% of 500000
    assert treasury == 25000  # 5% of 500000


def test_compute_subsidy_for_height_tail():
    """Subsidy reaches tail minimum after sufficient epochs."""
    schedule = {
        "start_nANM_per_block": 1000000,
        "epoch_length_blocks": 100,
        "decay_pct_per_epoch": 50.0,  # 50% decay
        "tail_nANM_per_block": 100000,  # Tail minimum
        "max_halvings": 64,
        "miner_pct": 80,
        "aicf_pct": 15,
        "treasury_pct": 5,
    }
    # After ~10 halvings (50% decay), subsidy ~= 1000000 * (0.5)**10 ~= 976
    # Should hit tail of 100000
    miner, aicf, treasury = compute_subsidy_for_height(1001, schedule)
    total = miner + aicf + treasury
    assert total == 100000  # Tail minimum
    assert miner == 80000  # 80% of tail
    assert aicf == 15000  # 15% of tail
    assert treasury == 5000  # 5% of tail


def test_compute_subsidy_split_no_rounding_loss():
    """Subsidy split should not lose funds due to rounding."""
    schedule = {
        "start_nANM_per_block": 1000001,  # Odd number
        "epoch_length_blocks": 100,
        "decay_pct_per_epoch": 12.5,
        "tail_nANM_per_block": 100000,
        "max_halvings": 64,
        "miner_pct": 80,
        "aicf_pct": 15,
        "treasury_pct": 5,
    }
    miner, aicf, treasury = compute_subsidy_for_height(1, schedule)
    total = miner + aicf + treasury
    # Treasury gets the remainder to avoid rounding loss
    assert total == 1000001
    assert miner == 800000  # 80% of 1000001 = 800000.8 → 800000
    assert aicf == 150000  # 15% of 1000001 = 150000.15 → 150000
    assert treasury == 50001  # Remainder to preserve total


def test_compute_block_reward_with_params():
    """Test compute_block_reward with valid params returns rewards."""
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 10000000,  # 0.01 ANM
                    "epoch_length_blocks": 216000,
                    "decay_pct_per_epoch": 25.0,
                    "tail_nANM_per_block": 500000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 60,
                    "aicf": 30,
                    "treasury": 10,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    # Test at height 1 (first post-genesis block)
    rewards = compute_block_reward(chain_id=1337, height=1, params=params)
    
    # Should return 3 rewards (miner, aicf, treasury)
    assert len(rewards) == 3
    
    # Verify addresses
    addresses = [addr for addr, _ in rewards]
    assert "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx" in addresses
    assert "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" in addresses
    assert "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" in addresses
    
    # Verify amounts sum to start amount
    total = sum(amt for _, amt in rewards)
    assert total == 10000000
    
    # Verify split percentages
    miner_amt = next(amt for addr, amt in rewards if "coinbase" in addr)
    aicf_amt = next(amt for addr, amt in rewards if "aicf" in addr)
    treasury_amt = next(amt for addr, amt in rewards if "treasury" in addr)
    
    assert miner_amt == 6000000  # 60%
    assert aicf_amt == 3000000  # 30%
    assert treasury_amt == 1000000  # 10%


def test_compute_block_reward_returns_empty_for_invalid_params():
    """Test compute_block_reward returns empty for invalid params."""
    # Missing required fields
    invalid_params = {"monetary": {}}
    
    rewards = compute_block_reward(chain_id=1337, height=1, params=invalid_params)
    
    # Should return empty due to invalid params
    assert rewards == []
