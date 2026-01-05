"""
Test to verify that mining multiple blocks accumulates rewards correctly.

This test addresses the issue where "only first reward appears" when mining
multiple blocks consecutively.
"""
import os
import sys

# Add the repo root to path for imports
sys.path.insert(0, "/home/runner/work/all/all")

def test_mining_multiple_blocks_balance_accumulation():
    """
    Test that mining 3 blocks consecutively credits all 3 rewards to the payout address.
    
    This is a reproduction test for the reported issue where only the first block's
    reward is credited to the wallet balance.
    """
    # Import after path is set
    from rpc import server as rpc_server
    from rpc.tests import new_test_client, rpc_call
    from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION
    from pq.py.address import decode_address
    
    # Get premine address in hex format
    premine_addr_bech32 = MAINNET_PREMINE_DISTRIBUTION[0][0]
    addr_record = decode_address(premine_addr_bech32)
    digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    premine_addr_bytes = digest[:32].ljust(32, b"\x00")
    premine_addr_hex = "0x" + premine_addr_bytes.hex()
    
    # Create test client
    client, cfg, _ = new_test_client()
    
    def get_balance(addr_hex: str) -> int:
        """Helper to get balance from RPC."""
        result = rpc_call(client, "state.getBalance", [addr_hex])
        balance = result.get("result", 0)
        if isinstance(balance, str):
            return int(balance, 16) if balance.startswith("0x") else int(balance)
        return int(balance)
    
    # Get initial balance
    balance_before = get_balance(premine_addr_hex)
    print(f"\n=== Initial State ===")
    print(f"Address: {premine_addr_bech32}")
    print(f"Address (hex): {premine_addr_hex}")
    print(f"Initial balance: {balance_before} nANM")
    
    # Mine 3 blocks one at a time to test accumulation
    print(f"\n=== Mining Blocks ===")
    blocks_to_mine = 3
    rewards_per_block = []
    
    for i in range(blocks_to_mine):
        print(f"\nMining block {i+1}/{blocks_to_mine}...")
        
        # Get balance before this block
        balance_before_block = get_balance(premine_addr_hex)
        
        # Mine one block
        result = rpc_call(client, "miner.mine", {"count": 1, "address": premine_addr_hex})
        mine_info = result.get("result", {})
        
        # Get balance after this block
        balance_after_block = get_balance(premine_addr_hex)
        
        # Calculate reward for this block
        block_reward = balance_after_block - balance_before_block
        rewards_per_block.append(block_reward)
        
        print(f"  Mined: {mine_info.get('mined', 0)} block(s)")
        print(f"  Height: {mine_info.get('height', 'unknown')}")
        print(f"  Reported reward: {mine_info.get('totalReward', 0)} nANM")
        print(f"  Balance before: {balance_before_block} nANM")
        print(f"  Balance after:  {balance_after_block} nANM")
        print(f"  Actual reward:  {block_reward} nANM")
        
        # Check that the block was actually mined
        assert mine_info.get("mined") == 1, f"Expected 1 block mined, got {mine_info.get('mined')}"
    
    # Get final balance
    balance_after = get_balance(premine_addr_hex)
    total_reward = balance_after - balance_before
    
    print(f"\n=== Final State ===")
    print(f"Initial balance: {balance_before} nANM")
    print(f"Final balance:   {balance_after} nANM")
    print(f"Total reward:    {total_reward} nANM")
    print(f"Rewards per block: {rewards_per_block}")
    
    # Verify that ALL blocks were credited
    print(f"\n=== Verification ===")
    for i, reward in enumerate(rewards_per_block):
        print(f"Block {i+1}: {reward} nANM {'✓' if reward > 0 else '✗ FAILED - NO REWARD'}")
    
    # Check if all blocks received rewards
    zero_reward_blocks = [i+1 for i, r in enumerate(rewards_per_block) if r == 0]
    
    if zero_reward_blocks:
        print(f"\n❌ ISSUE CONFIRMED: Blocks {zero_reward_blocks} received no rewards!")
        print(f"   Only {sum(1 for r in rewards_per_block if r > 0)}/{blocks_to_mine} blocks were credited")
        return False
    else:
        print(f"\n✓ SUCCESS: All {blocks_to_mine} blocks received rewards")
        print(f"   Total accumulated: {total_reward} nANM")
        return True


if __name__ == "__main__":
    try:
        success = test_mining_multiple_blocks_balance_accumulation()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
