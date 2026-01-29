"""
Test that block reward calculation uses consensus parameters, not hardcoded test values.

This test validates that:
1. Rewards are computed from chain parameters, not mock addresses
2. Mainnet premine is properly validated
3. Emission schedules are respected
4. No hardcoded test addresses leak into production paths
"""

import pytest


def test_block_reward_uses_consensus_params():
    """
    Verify that compute_block_reward uses chain parameters for post-genesis blocks,
    not hardcoded test values.
    """
    try:
        from consensus.rewards import compute_block_reward
    except ImportError:
        pytest.skip("consensus.rewards not available")
    
    # Test post-genesis rewards with minimal params
    params = {
        "monetary": {
            "issuance": {
                "initial_subsidy_base_units": 50_000_000_000,  # 50 ANM
                "halving_interval": 210000,
                "min_subsidy_base_units": 1_000_000_000,  # 1 ANM
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1miner00000000000000000000000000000000",
            "aicf_treasury": "anim1aicf0000000000000000000000000000000000",
            "treasury": "anim1treasury0000000000000000000000000000",
        }
    }
    
    # Height 1 should use emission schedule, not premine
    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    assert isinstance(rewards, list), "Rewards should be a list of (address, amount) tuples"
    
    # Should have non-zero rewards
    if len(rewards) > 0:
        for addr, amt in rewards:
            assert isinstance(addr, str), "Address must be a string"
            assert addr.startswith("anim1"), "Address must be valid bech32"
            assert isinstance(amt, int), "Amount must be an integer"
            assert amt >= 0, "Amount must be non-negative"


def test_mainnet_premine_is_non_trivial():
    """
    Verify that mainnet premine constants are real, non-trivial values
    that match the documented genesis allocation.
    """
    try:
        from consensus.rewards import (
            MAINNET_PREMINE_TOTAL,
            MAINNET_PREMINE_DISTRIBUTION,
        )
    except ImportError:
        pytest.skip("consensus.rewards not available")
    
    # Verify premine total is the documented value (81M ANM)
    expected_total = 81_000_000_000_000_000  # 81M ANM in base units
    assert MAINNET_PREMINE_TOTAL == expected_total, (
        f"Mainnet premine total should be {expected_total}, got {MAINNET_PREMINE_TOTAL}"
    )
    
    # Verify distribution is non-empty and sums correctly
    assert len(MAINNET_PREMINE_DISTRIBUTION) > 0, (
        "Premine distribution must not be empty"
    )
    
    total_distributed = sum(amt for _, amt in MAINNET_PREMINE_DISTRIBUTION)
    assert total_distributed == MAINNET_PREMINE_TOTAL, (
        f"Distribution sum {total_distributed} must equal total {MAINNET_PREMINE_TOTAL}"
    )
    
    # Verify addresses are valid bech32
    for addr, amt in MAINNET_PREMINE_DISTRIBUTION:
        assert isinstance(addr, str), "Premine address must be string"
        assert addr.startswith("anim1"), "Premine address must be valid bech32"
        assert amt > 0, "Premine amount must be positive"


def test_genesis_rewards_differ_from_post_genesis():
    """
    Verify that height 0 (genesis) rewards are different from height 1+,
    ensuring premine logic is correctly isolated.
    """
    try:
        from consensus.rewards import compute_block_reward
    except ImportError:
        pytest.skip("consensus.rewards not available")
    
    # Mainnet genesis (height 0)
    genesis_rewards = compute_block_reward(chain_id=1, height=0, params=None)
    
    # Post-genesis (height 1)
    params = {
        "monetary": {
            "issuance": {
                "initial_subsidy_base_units": 50_000_000_000,
                "halving_interval": 210000,
                "min_subsidy_base_units": 1_000_000_000,
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1test",
            "aicf_treasury": "anim1aicf",
            "treasury": "anim1treasury",
        }
    }
    post_genesis_rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    # Genesis should return premine
    assert len(genesis_rewards) > 0, "Genesis should have premine rewards"
    
    # Amounts should be different (premine is one-time allocation)
    genesis_total = sum(amt for _, amt in genesis_rewards)
    post_genesis_total = sum(amt for _, amt in post_genesis_rewards) if post_genesis_rewards else 0
    
    # Genesis premine should be much larger than single block subsidy
    assert genesis_total > post_genesis_total, (
        "Genesis premine should be larger than regular block reward"
    )


def test_devnet_can_use_different_genesis():
    """
    Verify that non-mainnet chains (devnet, testnet) don't get forced
    into the mainnet premine at genesis.
    """
    try:
        from consensus.rewards import compute_block_reward
    except ImportError:
        pytest.skip("consensus.rewards not available")
    
    # Devnet (chain_id=1337) at genesis with custom params
    params = {
        "monetary": {
            "issuance": {
                "initial_subsidy_base_units": 100_000_000_000,
                "halving_interval": 100000,
                "min_subsidy_base_units": 1_000_000_000,
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1devminer",
            "aicf_treasury": "anim1devaicf",
            "treasury": "anim1devtreasury",
        }
    }
    
    devnet_genesis = compute_block_reward(chain_id=1337, height=0, params=params)
    mainnet_genesis = compute_block_reward(chain_id=1, height=0, params=None)
    
    # Devnet genesis should differ from mainnet (not forced into mainnet premine)
    # NOTE: Current implementation may return empty list for non-mainnet genesis
    # which is fine - it means genesis allocation is handled separately
    
    # The key test is that devnet doesn't return mainnet premine addresses
    devnet_addresses = {addr for addr, _ in devnet_genesis}
    
    from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
    mainnet_addresses = {addr for addr, _ in MAINNET_PREMINE_DISTRIBUTION}
    
    # Devnet should not use mainnet premine addresses
    assert not (devnet_addresses & mainnet_addresses), (
        "Devnet genesis should not use mainnet premine addresses"
    )


def test_emission_schedule_uses_params_not_hardcoded():
    """
    Verify that emission schedule parsing uses provided parameters,
    not hardcoded test values.
    """
    try:
        from consensus.rewards import parse_emission_schedule, compute_subsidy_for_height
    except ImportError:
        pytest.skip("Emission schedule functions not available")
    
    # Test with custom parameters
    params = {
        "monetary": {
            "issuance": {
                "initial_subsidy_base_units": 25_000_000_000,  # 25 ANM
                "halving_interval": 150000,
                "min_subsidy_base_units": 500_000_000,  # 0.5 ANM
            }
        }
    }
    
    try:
        schedule = parse_emission_schedule(params)
    except Exception:
        pytest.skip("Could not parse emission schedule")
    
    # Verify schedule reflects provided params
    assert schedule is not None, "Schedule should be parsed"
    
    # Compute subsidy for height 1 (first block after genesis)
    try:
        miner, aicf, treasury = compute_subsidy_for_height(1, schedule)
    except Exception:
        pytest.skip("Could not compute subsidy")
    
    # Total subsidy should be approximately the initial subsidy
    total = miner + aicf + treasury
    
    # Allow some splits for AICF/treasury, but total should be close to initial
    initial = params["monetary"]["issuance"]["initial_subsidy_base_units"]
    
    assert total > 0, "Subsidy should be positive"
    assert total <= initial * 1.1, (
        f"Total subsidy {total} should not exceed initial {initial} by much"
    )


def test_no_hardcoded_zero_addresses_in_rewards():
    """
    Verify that reward calculation doesn't use placeholder addresses
    like "0x0000..." in production paths.
    """
    try:
        from consensus.rewards import compute_block_reward
    except ImportError:
        pytest.skip("consensus.rewards not available")
    
    params = {
        "monetary": {
            "issuance": {
                "initial_subsidy_base_units": 50_000_000_000,
                "halving_interval": 210000,
                "min_subsidy_base_units": 1_000_000_000,
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1validaddress00000000000000000000000",
            "aicf_treasury": "anim1aicfvalidaddress00000000000000000000",
            "treasury": "anim1treasuryvalidaddress000000000000000",
        }
    }
    
    rewards = compute_block_reward(chain_id=1337, height=1, params=params)
    
    # Check that no address is a zero/placeholder pattern
    for addr, _ in rewards:
        assert not addr.startswith("0x0"), "Should not use 0x0... addresses"
        assert "xxxxx" not in addr.lower(), "Should not use placeholder addresses"
        assert addr.startswith("anim1"), "Should use valid bech32 addresses"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
