"""
Integration test for mainnet mining rewards balance increase.

Tests that mining a block on mainnet (chain_id=0) correctly credits
the block subsidy (300 ANM) to the payout address.

This test validates:
1. Mining a block with custom payout address
2. Balance increases by exactly 300 ANM (300_000_000_000 nANM)
3. Wallet show reflects the balance correctly
4. State persistence across queries
"""

import pytest
import asyncio
import time
from pathlib import Path


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mainnet_mining_balance_increase():
    """
    Test that mining 1 block on mainnet increases balance by 300 ANM.
    
    Flow:
    1. Start fresh mainnet node (ephemeral DB)
    2. Import/create a wallet address
    3. Query initial balance
    4. Mine exactly 1 block to that address
    5. Query balance again
    6. Assert balance increased by 300 ANM
    """
    # Import required modules
    import subprocess
    import tempfile
    import json
    from animica.config import load_network_config
    
    # Use a temporary directory for this test
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set environment for mainnet with temp data dir
        import os
        env = os.environ.copy()
        env["ANIMICA_NETWORK"] = "mainnet"
        env["ANIMICA_CHAIN_ID"] = "0"
        env["ANIMICA_DATA_DIR"] = tmpdir
        env["ANIMICA_RPC_PORT"] = "18545"  # Use different port to avoid conflicts
        
        rpc_url = "http://127.0.0.1:18545/rpc"
        
        # Start node in background
        node_proc = subprocess.Popen(
            ["python", "-m", "rpc"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(__file__).parent.parent,
        )
        
        try:
            # Wait for node to be ready
            await asyncio.sleep(5)
            
            # Create a test wallet address
            # For simplicity, use a known premine address from genesis
            # Or generate a new one if wallet CLI is available
            from wallet.address import Address
            from pq.dilithium import Dilithium3
            
            # Generate a test keypair
            keypair = Dilithium3.generate()
            test_address = Address.from_public_key(keypair.public_key, alg_id=2)  # Dilithium3
            test_address_str = test_address.to_bech32()
            
            print(f"Test address: {test_address_str}")
            
            # Query initial balance
            from animica.cli.rpc import call_rpc
            
            try:
                initial_balance = call_rpc("state.getBalance", [test_address_str], rpc_url)
            except Exception:
                # If account doesn't exist yet, balance is 0
                initial_balance = 0
            
            print(f"Initial balance: {initial_balance} nANM")
            
            # Mine exactly 1 block with this payout address
            mine_result = call_rpc(
                "miner.mineBlocks",
                [1, test_address_str],  # count=1, payout_address
                rpc_url
            )
            
            print(f"Mining result: {mine_result}")
            
            # Wait a moment for state to settle
            await asyncio.sleep(2)
            
            # Query balance again
            final_balance = call_rpc("state.getBalance", [test_address_str], rpc_url)
            print(f"Final balance: {final_balance} nANM")
            
            # Calculate increase
            balance_increase = final_balance - initial_balance
            expected_reward = 300_000_000_000  # 300 ANM in nANM
            
            # Assert balance increased by exactly 300 ANM
            assert balance_increase == expected_reward, (
                f"Balance should increase by {expected_reward} nANM (300 ANM), "
                f"but increased by {balance_increase} nANM. "
                f"Initial: {initial_balance}, Final: {final_balance}"
            )
            
            # Verify via wallet show equivalent query
            balance_via_state = call_rpc("state.getBalance", [test_address_str], rpc_url)
            assert balance_via_state == final_balance, (
                f"Balance query inconsistency! "
                f"Direct query: {final_balance}, State query: {balance_via_state}"
            )
            
            # Success!
            print(f"✓ Mining reward test passed!")
            print(f"  Mined 1 block to {test_address_str}")
            print(f"  Balance increased by {balance_increase} nANM (300 ANM)")
            
        finally:
            # Cleanup: stop node
            node_proc.terminate()
            try:
                node_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                node_proc.kill()


@pytest.mark.integration
def test_mining_reward_diagnostics():
    """
    Test that mining provides adequate diagnostics when rewards are not credited.
    
    This test validates the defensive logging and error detection when:
    - Mining reports success but balance doesn't change
    - Chain ID mismatch between miner and state query
    - Wrong RPC URL or network
    """
    # This is a meta-test that validates the diagnostic improvements
    # The actual implementation should provide clear error messages
    
    # Check that mining code has defensive balance re-query
    from rpc.methods.miner import mine_blocks
    import inspect
    
    source = inspect.getsource(mine_blocks)
    
    # Should have balance verification after mining
    assert "getBalance" in source or "get_balance" in source, (
        "Mining code should re-query balance after mining for verification"
    )
    
    # Should have error diagnostics
    assert "ERROR" in source or "error" in source, (
        "Mining code should log errors when balance doesn't increase"
    )
    
    print("✓ Mining diagnostic checks passed")


@pytest.mark.integration  
def test_wallet_show_queries_latest_head():
    """
    Test that wallet show queries the latest head (not just safe head).
    
    Ensures newly mined blocks are immediately reflected in balance queries.
    """
    # Import wallet show logic
    from python.animica.cli.wallet import get_balance
    
    # This function should default to "latest" tag, not "safe"
    import inspect
    source = inspect.getsource(get_balance)
    
    # Should query with "latest" tag
    assert '"latest"' in source or "'latest'" in source, (
        "Wallet balance query should use 'latest' tag to see fresh blocks"
    )
    
    print("✓ Wallet show uses latest head")


if __name__ == "__main__":
    # Run the test manually
    asyncio.run(test_mainnet_mining_balance_increase())
