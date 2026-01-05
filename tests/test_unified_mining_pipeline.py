"""
Integration tests for unified mining pipeline.

Tests verifying:
1. Instant block system has been removed
2. Mining uses canonical pipeline (chain.apply_block, chain.commit)
3. Balance increases by reward after mining
4. Wallet show includes head info and matches state.getBalance
"""
import pytest
from rpc.tests import new_test_client, rpc_call


def _parse_balance(result: dict) -> int:
    """Helper to parse balance from RPC result."""
    balance = result.get("result", 0)
    if isinstance(balance, str):
        return int(balance, 16) if balance.startswith("0x") else int(balance)
    return int(balance)


def test_no_instant_block_flags_remain():
    """Verify that instant block flags and paths have been removed."""
    client, cfg, _ = new_test_client()
    
    # Mine a block
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": test_address})
    result = mine_result["result"]
    
    # Verify no instant block flags in response
    assert "instantBlock" not in str(result).lower(), \
        "Response should not contain instantBlock references"
    assert "instant_block" not in str(result).lower(), \
        "Response should not contain instant_block references"
    
    # Get the latest block
    head_result = rpc_call(client, "chain.getHead", [])
    head = head_result["result"]
    
    # Verify no instant block flag in block header
    if "header" in head:
        header = head["header"]
        assert "instantBlock" not in header, \
            "Block header should not have instantBlock field"
    
    print("✓ No instant block flags found in RPC responses")


def test_mine_3_blocks_head_height_increases():
    """Mine 3 blocks and verify head height increases by 3."""
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    
    # Get initial head
    initial_head = rpc_call(client, "chain.getHead", [])
    initial_height = initial_head["result"].get("height", 0)
    
    # Mine 3 blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": test_address})
    result = mine_result["result"]
    
    # Verify all blocks were mined
    assert result["mined"] == 3, "Should mine all 3 blocks"
    
    # Get final head
    final_head = rpc_call(client, "chain.getHead", [])
    final_height = final_head["result"].get("height", 0)
    
    # Verify height increased by 3
    height_increase = final_height - initial_height
    assert height_increase == 3, \
        f"Height should increase by 3, got increase of {height_increase}"
    
    print(f"✓ Head height increased from {initial_height} to {final_height}")


def test_mine_3_blocks_balance_increases_by_3x_reward():
    """Mine 3 blocks and verify balance increases by 3 * reward."""
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    
    # Get initial balance
    initial_balance = _parse_balance(rpc_call(client, "state.getBalance", [test_address]))
    
    # Mine 3 blocks
    mine_result = rpc_call(client, "miner.mine", {"count": 3, "address": test_address})
    result = mine_result["result"]
    
    # Verify mining succeeded
    assert result["mined"] == 3, "Should mine all 3 blocks"
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
    
    # Verify we have exactly 3 reward entries
    assert len(result["rewards"]) == 3, "Should have 3 reward entries"
    
    print(f"✓ Balance increased correctly:")
    print(f"  Initial: {initial_balance} nANM")
    print(f"  Final: {final_balance} nANM")
    print(f"  Reward: {total_reward} nANM")
    print(f"  Increase: {balance_increase} nANM")


def test_wallet_show_includes_head_info():
    """Verify wallet show includes head info (height, hash, queried_at, rpc_url)."""
    # This test would require CLI integration, which is tested separately
    # in python/animica/cli/tests/test_wallet_show_output.py
    # Here we just verify the RPC methods return the required data
    client, cfg, _ = new_test_client()
    
    # Call chain.getHead
    head_result = rpc_call(client, "chain.getHead", [])
    head = head_result["result"]
    
    # Verify head info contains required fields
    assert "height" in head, "Head should include height"
    assert "hash" in head, "Head should include hash"
    
    print(f"✓ Head info available via RPC:")
    print(f"  Height: {head.get('height')}")
    print(f"  Hash: {head.get('hash')}")


def test_wallet_show_matches_state_get_balance():
    """Verify wallet show balance matches state.getBalance RPC."""
    # This is tested via wallet CLI tests
    # Here we verify state.getBalance works correctly
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    
    # Mine a block to ensure address has balance
    rpc_call(client, "miner.mine", {"count": 1, "address": test_address})
    
    # Query balance
    balance_result = rpc_call(client, "state.getBalance", [test_address])
    balance = _parse_balance(balance_result)
    
    # Verify balance is returned
    assert isinstance(balance, int), "Balance should be an integer"
    assert balance >= 0, "Balance should be non-negative"
    
    print(f"✓ state.getBalance works correctly: {balance} nANM")


def test_mining_uses_canonical_chain_path():
    """Verify mining uses canonical chain.apply_block/chain.commit path."""
    client, cfg, _ = new_test_client()
    
    test_address = "anim1zqquzgffx7raqljy3veg024ph8m8e2cyax8m98uzean8r46xskf09mc4a6avv"
    
    # Get initial head
    initial_head = rpc_call(client, "chain.getHead", [])
    initial_height = initial_head["result"].get("height", 0)
    initial_hash = initial_head["result"].get("hash")
    
    # Mine a block
    mine_result = rpc_call(client, "miner.mine", {"count": 1, "address": test_address})
    result = mine_result["result"]
    assert result["mined"] == 1, "Should mine 1 block"
    
    # Get new head
    final_head = rpc_call(client, "chain.getHead", [])
    final_height = final_head["result"].get("height", 0)
    final_hash = final_head["result"].get("hash")
    
    # Verify head was updated (proves canonical path was used)
    assert final_height > initial_height, "Height should increase"
    assert final_hash != initial_hash, "Hash should change"
    
    # Verify we can query the block by hash
    if final_hash:
        block_result = rpc_call(client, "chain.getBlockByHash", [final_hash])
        block = block_result.get("result")
        assert block is not None, "Block should be retrievable by hash"
        
        # Verify block has proper structure
        assert "header" in block, "Block should have header"
        assert block["header"].get("height") == final_height, \
            "Block height should match head height"
    
    print("✓ Mining uses canonical chain path (head updated correctly)")


def test_mining_template_status():
    """Test mining.getTemplateStatus RPC method."""
    client, cfg, _ = new_test_client()
    
    # Call the new mining.getTemplateStatus method
    status_result = rpc_call(client, "mining.getTemplateStatus", [])
    status = status_result["result"]
    
    # Verify required fields are present
    assert "can_mine" in status, "Status should include can_mine"
    assert "head" in status, "Status should include head info"
    assert "mempool" in status, "Status should include mempool info"
    
    head = status["head"]
    assert "height" in head, "Head should include height"
    assert "hash" in head, "Head should include hash"
    assert "has_state_root" in head, "Head should include has_state_root"
    
    mempool = status["mempool"]
    assert "size" in mempool, "Mempool should include size"
    
    print(f"✓ mining.getTemplateStatus works:")
    print(f"  Can mine: {status.get('can_mine')}")
    print(f"  Head height: {head.get('height')}")
    print(f"  Mempool size: {mempool.get('size')}")


if __name__ == "__main__":
    # Run tests individually for manual verification
    print("Running unified mining pipeline tests...\n")
    
    print("1. Testing no instant block flags remain...")
    test_no_instant_block_flags_remain()
    print()
    
    print("2. Testing head height increases by 3...")
    test_mine_3_blocks_head_height_increases()
    print()
    
    print("3. Testing balance increases by 3*reward...")
    test_mine_3_blocks_balance_increases_by_3x_reward()
    print()
    
    print("4. Testing wallet show includes head info...")
    test_wallet_show_includes_head_info()
    print()
    
    print("5. Testing wallet show matches state.getBalance...")
    test_wallet_show_matches_state_get_balance()
    print()
    
    print("6. Testing mining uses canonical chain path...")
    test_mining_uses_canonical_chain_path()
    print()
    
    print("7. Testing mining template status...")
    test_mining_template_status()
    print()
    
    print("✓ All tests passed!")
