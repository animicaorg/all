#!/usr/bin/env python3
"""
Test script to validate that 100% of mining rewards go to miners.
This validates the changes made to allocate all block rewards to miners.
"""

import sys
sys.path.insert(0, '.')

from consensus.rewards import compute_block_reward, parse_emission_schedule, compute_subsidy_for_height

def test_devnet_100_percent_to_miner():
    """Test devnet (chain_id=1337) gives 100% to miner."""
    print("Testing Devnet (1337) reward allocation...")
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5000000000,  # 5 ANM
                    "epoch_length_blocks": 90000000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    # Test at height 1
    rewards = compute_block_reward(chain_id=1337, height=1, params=params)
    
    assert len(rewards) == 1, f"Expected 1 reward (100% to miner), got {len(rewards)}"
    total = sum(amt for _, amt in rewards)
    assert total == 5000000000, f"Expected 5 ANM (5000000000 nANM), got {total}"
    
    miner_addr, miner_amt = rewards[0]
    assert "coinbase" in miner_addr
    assert miner_amt == 5000000000, f"Miner should get 100% (5 ANM), got {miner_amt}"
    
    print(f"  ✓ Height 1: Miner receives 100% ({miner_amt} nANM = {miner_amt / 1e9:.2f} ANM)")
    return True

def test_testnet_100_percent_to_miner():
    """Test testnet (chain_id=2) gives 100% to miner."""
    print("Testing Testnet (2) reward allocation...")
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5000000000,  # 5 ANM
                    "epoch_length_blocks": 90000000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    rewards = compute_block_reward(chain_id=2, height=1, params=params)
    
    assert len(rewards) == 1, f"Expected 1 reward (100% to miner), got {len(rewards)}"
    total = sum(amt for _, amt in rewards)
    assert total == 5000000000, f"Expected 5 ANM, got {total}"
    
    miner_addr, miner_amt = rewards[0]
    assert "coinbase" in miner_addr
    assert miner_amt == 5000000000
    
    print(f"  ✓ Height 1: Miner receives 100% ({miner_amt} nANM = {miner_amt / 1e9:.2f} ANM)")
    return True

def test_mainnet_100_percent_to_miner():
    """Test mainnet (chain_id=1) gives 100% to miner."""
    print("Testing Mainnet (1) reward allocation...")
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5000000000,  # 5 ANM
                    "epoch_length_blocks": 90000000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    rewards = compute_block_reward(chain_id=1, height=1, params=params)
    
    assert len(rewards) == 1, f"Expected 1 reward (100% to miner), got {len(rewards)}"
    total = sum(amt for _, amt in rewards)
    assert total == 5000000000, f"Expected 5 ANM, got {total}"
    
    miner_addr, miner_amt = rewards[0]
    assert "coinbase" in miner_addr
    assert miner_amt == 5000000000
    
    print(f"  ✓ Height 1: Miner receives 100% ({miner_amt} nANM = {miner_amt / 1e9:.2f} ANM)")
    return True

def test_halving_still_100_percent():
    """Test that after halving, miner still gets 100%."""
    print("Testing reward halving maintains 100% to miner...")
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5000000000,  # 5 ANM
                    "epoch_length_blocks": 90000000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    # Test after first halving (90M blocks)
    rewards = compute_block_reward(chain_id=1337, height=90000001, params=params)
    
    assert len(rewards) == 1, f"Expected 1 reward after halving, got {len(rewards)}"
    total = sum(amt for _, amt in rewards)
    assert total == 2500000000, f"Expected 2.5 ANM after first halving, got {total}"
    
    miner_addr, miner_amt = rewards[0]
    assert "coinbase" in miner_addr
    assert miner_amt == 2500000000
    
    print(f"  ✓ Height 90000001 (after 1st halving): Miner receives 100% ({miner_amt} nANM = {miner_amt / 1e9:.2f} ANM)")
    
    # Test after second halving (180M blocks)
    rewards = compute_block_reward(chain_id=1337, height=180000001, params=params)
    
    assert len(rewards) == 1
    total = sum(amt for _, amt in rewards)
    assert total == 1250000000, f"Expected 1.25 ANM after second halving, got {total}"
    
    miner_addr, miner_amt = rewards[0]
    assert "coinbase" in miner_addr
    assert miner_amt == 1250000000
    
    print(f"  ✓ Height 180000001 (after 2nd halving): Miner receives 100% ({miner_amt} nANM = {miner_amt / 1e9:.2f} ANM)")
    return True

def test_no_aicf_or_treasury_rewards():
    """Verify that AICF and treasury addresses receive no block rewards."""
    print("Testing that AICF and treasury receive 0 rewards...")
    params = {
        "monetary": {
            "issuance": {
                "subsidy": {
                    "start_nANM_per_block": 5000000000,
                    "epoch_length_blocks": 90000000,
                    "decay_pct_per_epoch": 50.0,
                    "tail_nANM_per_block": 100000,
                    "max_halvings": 64,
                },
                "subsidy_split_pct": {
                    "miner": 100,
                    "aicf": 0,
                    "treasury": 0,
                },
            }
        },
        "system_addresses": {
            "coinbase_default": "anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "aicf_treasury": "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "treasury": "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        },
    }
    
    rewards = compute_block_reward(chain_id=1337, height=1, params=params)
    
    # Ensure no rewards go to AICF or treasury
    for addr, amt in rewards:
        assert "aicf" not in addr.lower(), f"AICF should not receive rewards, but got {amt}"
        assert "treasury" not in addr.lower() or "coinbase" in addr.lower(), \
            f"Treasury should not receive rewards, but got {amt}"
    
    print("  ✓ Confirmed: AICF receives 0 nANM")
    print("  ✓ Confirmed: Treasury receives 0 nANM")
    print("  ✓ Confirmed: Only miner receives rewards")
    return True

def main():
    """Run all tests."""
    print("=" * 70)
    print("VALIDATION: 100% Mining Rewards to Miners")
    print("=" * 70)
    print()
    
    tests = [
        ("Devnet 100% to miner", test_devnet_100_percent_to_miner),
        ("Testnet 100% to miner", test_testnet_100_percent_to_miner),
        ("Mainnet 100% to miner", test_mainnet_100_percent_to_miner),
        ("Halving maintains 100% to miner", test_halving_still_100_percent),
        ("No rewards to AICF/Treasury", test_no_aicf_or_treasury_rewards),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print()
        except AssertionError as e:
            failed += 1
            print(f"  ✗ FAILED: {e}")
            print()
        except Exception as e:
            failed += 1
            print(f"  ✗ ERROR: {e}")
            print()
    
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)
    
    if failed > 0:
        sys.exit(1)
    else:
        print("\n✓ All tests passed! Miners now receive 100% of block rewards.")
        sys.exit(0)

if __name__ == "__main__":
    main()
