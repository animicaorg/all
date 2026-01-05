"""
Test to verify mining rewards are immediately visible in balance queries.

This test ensures that after mining a block, the balance query immediately
reflects the mined rewards without needing to wait for WAL checkpoints or
database flushes.
"""

import pytest
from rpc.tests import new_test_client, rpc_call


def _parse_balance(result: dict) -> int:
    """Helper to parse balance from RPC result."""
    balance = result.get("result", 0)
    if isinstance(balance, str):
        return int(balance, 16) if balance.startswith("0x") else int(balance)
    return int(balance)


def test_mining_rewards_immediately_visible():
    """
    Test that mining rewards are immediately visible in balance queries.
    
    This is a regression test for the WAL checkpoint issue where rewards
    were credited but not immediately visible to balance queries due to
    SQLite WAL buffering.
    """
    client, cfg, _ = new_test_client()
    
    # Use a test address
    test_address = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
    
    # Get initial balance
    initial_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Mine a single block
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": test_address})
    result = mine_result["result"]
    
    assert result["mined"] == 1, "Should mine 1 block"
    reward = result["totalReward"]
    assert reward > 0, "Reward should be positive"
    
    # Query balance IMMEDIATELY after mining (without any delays)
    # This should see the reward if WAL checkpoint was forced
    final_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Verify balance increased by exactly the reward amount
    balance_increase = final_balance - initial_balance
    assert balance_increase == reward, \
        f"Balance should increase immediately by {reward} nANM, got {balance_increase} nANM. " \
        f"This suggests WAL checkpoint is not being forced after mining."
    
    print(f"✓ Mining reward immediately visible:")
    print(f"  Address: {test_address}")
    print(f"  Initial: {initial_balance} nANM")
    print(f"  Reward: {reward} nANM")
    print(f"  Final: {final_balance} nANM")
    print(f"  Increase: {balance_increase} nANM")


def test_multiple_rapid_mining_sessions():
    """
    Test that rapid mining sessions (without delays) correctly accumulate rewards.
    
    This stresses the WAL checkpoint mechanism by mining multiple blocks
    in quick succession and verifying each reward is visible.
    """
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqqtest123mining456rapid789accumulate012session345test"
    
    # Get initial balance
    balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Mine 3 blocks in rapid succession
    for i in range(3):
        result = rpc_call(client, "miner.mine", {"count": 1, "address": test_address})["result"]
        assert result["mined"] == 1
        reward = result["totalReward"]
        
        # Check balance immediately after each block
        new_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
        expected_balance = balance + reward
        
        assert new_balance == expected_balance, \
            f"After block {i+1}: expected {expected_balance} nANM, got {new_balance} nANM"
        
        balance = new_balance
        print(f"✓ Block {i+1}: reward={reward}, new_balance={balance}")
    
    print(f"✓ All 3 rapid mining sessions correctly accumulated rewards")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
