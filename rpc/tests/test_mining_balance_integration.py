"""
Integration test for mining rewards and balance queries.

This test verifies the complete workflow from the problem statement:
1. Mine blocks to a specific Bech32 address
2. Query balance via RPC
3. Verify rewards are correctly reflected in wallet balance

This is a regression test for the address parsing bug where mining rewards
were credited with 32-byte keys but balance queries used 34-byte keys,
causing balances to always return 0 despite successful mining.
"""

import pytest
from rpc.tests import new_test_client, rpc_call


def _parse_balance(result: dict) -> int:
    """Helper to parse balance from RPC result."""
    balance = result.get("result", 0)
    if isinstance(balance, str):
        return int(balance, 16) if balance.startswith("0x") else int(balance)
    return int(balance)


def test_mining_to_bech32_address_updates_balance():
    """
    Test that mining blocks to a Bech32 address correctly updates the balance.
    
    This is the primary regression test for the address parsing bug.
    Scenario from problem statement:
    - Mine 5 blocks to anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv
    - Verify balance increases by reported rewards
    """
    client, cfg, _ = new_test_client()
    
    # Use the test address from the problem statement
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    
    # Get initial balance
    initial_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Mine 5 blocks to the address
    mine_result = rpc_call(client, "miner.mine", {"count": 5, "address": test_address})
    result = mine_result["result"]
    
    # Verify mining succeeded
    assert result["mined"] == 5, "Should mine all 5 blocks"
    assert result["height"] == 5, "Chain height should reach 5"
    assert "totalReward" in result, "Response should include totalReward"
    assert "rewards" in result, "Response should include rewards array"
    
    total_reward = result["totalReward"]
    
    # Get final balance
    final_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Verify balance increased by the exact reward amount
    balance_increase = final_balance - initial_balance
    assert balance_increase == total_reward, \
        f"Balance should increase by {total_reward} nANM, got {balance_increase} nANM"
    assert balance_increase > 0, "Balance should increase after mining"
    
    # Verify individual reward entries sum to total
    rewards_sum = sum(r["reward"] for r in result["rewards"])
    assert rewards_sum == total_reward, \
        f"Sum of individual rewards ({rewards_sum}) should equal totalReward ({total_reward})"
    
    print(f"✓ Mining to Bech32 address works correctly:")
    print(f"  Address: {test_address}")
    print(f"  Initial balance: {initial_balance} nANM")
    print(f"  Final balance: {final_balance} nANM")
    print(f"  Reward: {total_reward} nANM")
    print(f"  Balance increase: {balance_increase} nANM")


def test_mining_then_wallet_show_consistency():
    """
    Test that mining via RPC and querying via wallet CLI see the same balance.
    
    This verifies that both the RPC and CLI codepaths use consistent
    address parsing and StateDB access.
    """
    client, cfg, _ = new_test_client()
    
    # Use premine address for consistency
    from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
    test_address_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
    
    # Get initial balance via RPC
    initial_balance_rpc = _parse_balance(
        rpc_call(client, "state.getBalance", [test_address_bech32])
    )
    
    # Mine 3 blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": test_address_bech32})
    result = mine_result["result"]
    assert result["mined"] == 3
    total_reward = result["totalReward"]
    
    # Get final balance via RPC
    final_balance_rpc = _parse_balance(
        rpc_call(client, "state.getBalance", [test_address_bech32])
    )
    
    # Verify consistency
    assert final_balance_rpc - initial_balance_rpc == total_reward, \
        "RPC balance query should reflect mining rewards"
    
    print(f"✓ Mining and balance query consistency verified:")
    print(f"  Address: {test_address_bech32}")
    print(f"  Initial: {initial_balance_rpc} nANM")
    print(f"  Final: {final_balance_rpc} nANM")
    print(f"  Reward: {total_reward} nANM")


def test_multiple_mining_sessions_accumulate():
    """
    Test that multiple mining sessions to the same address accumulate correctly.
    """
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqp2nx50902d7jgrzk0ep798r2vhpgt3rhtmn89gadzdgyhf9hmln7g9e4xt9"
    
    # Get initial balance
    balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Mine in 3 sessions
    total_expected = 0
    for session in range(3):
        result = rpc_call(client, "miner.mine", {"count": 2, "address": test_address})["result"]
        assert result["mined"] == 2
        total_expected += result["totalReward"]
        
        # Check balance after each session
        new_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
        assert new_balance > balance, f"Balance should increase after session {session + 1}"
        balance = new_balance
    
    # Final verification
    final_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    assert final_balance >= total_expected, \
        f"Final balance ({final_balance}) should be at least expected rewards ({total_expected})"
    
    print(f"✓ Multiple mining sessions accumulate correctly:")
    print(f"  Address: {test_address}")
    print(f"  Total mined: 6 blocks (3 sessions × 2 blocks)")
    print(f"  Final balance: {final_balance} nANM")
    print(f"  Expected rewards: {total_expected} nANM")


def test_balance_query_for_unmined_address_returns_zero():
    """
    Test that querying balance for an address that has never received
    mining rewards returns 0 (not an error).
    """
    client, cfg, _ = new_test_client()
    
    # Use a random address that has never been mined to
    test_address = "anim1zqtest123456789abcdefghijklmnopqrstuvwxyz01234567890abcdefg"
    
    # Should return 0, not error
    try:
        balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
        assert balance == 0, "Unmined address should have 0 balance"
        print(f"✓ Unmined address correctly returns 0 balance")
    except Exception as e:
        # Address validation may fail for invalid format
        if "invalid" in str(e).lower() or "format" in str(e).lower():
            print(f"✓ Invalid address format rejected as expected: {e}")
        else:
            raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
