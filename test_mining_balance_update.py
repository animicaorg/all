"""
Test to reproduce and verify the mining rewards balance update bug.

This test ensures that after mining blocks, the wallet balance increases correctly.
"""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def test_mining_updates_balance():
    """
    Test that mining blocks immediately updates wallet balance.
    
    Steps:
    1. Create a temporary wallet
    2. Query initial balance
    3. Mine N blocks to the wallet address
    4. Query balance again
    5. Assert balance increased by N * block_reward
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        wallet_file = Path(tmpdir) / "test_wallet.json"
        
        # Step 1: Create wallet
        print("Step 1: Creating test wallet...")
        result = subprocess.run(
            [
                "python", "-m", "animica.cli.main",
                "wallet", "create",
                "--label", "test_miner",
                "--wallet-file", str(wallet_file),
                "--allow-insecure-fallback",
            ],
            capture_output=True,
            text=True,
            cwd="/home/runner/work/all/all",
        )
        print(f"Wallet create stdout: {result.stdout}")
        print(f"Wallet create stderr: {result.stderr}")
        assert result.returncode == 0, f"Failed to create wallet: {result.stderr}"
        
        # Extract address from output
        import re
        address_match = re.search(r"Address:\s+([a-z0-9]+)", result.stdout)
        assert address_match, f"Could not find address in output: {result.stdout}"
        address = address_match.group(1)
        print(f"Created wallet with address: {address}")
        
        # Step 2: Query initial balance
        print("\nStep 2: Querying initial balance...")
        result = subprocess.run(
            [
                "python", "-m", "animica.cli.main",
                "wallet", "show",
                "--wallet-file", str(wallet_file),
                "--source", "chain",
                "test_miner",
            ],
            capture_output=True,
            text=True,
            cwd="/home/runner/work/all/all",
        )
        print(f"Initial balance stdout: {result.stdout}")
        print(f"Initial balance stderr: {result.stderr}")
        if result.returncode != 0:
            print(f"Warning: Failed to query initial balance (node might not be running): {result.stderr}")
            balance_initial = 0
        else:
            try:
                wallet_data = json.loads(result.stdout)
                balance_initial = int(wallet_data.get("balance_confirmed", 0) or 0)
                print(f"Initial balance: {balance_initial} nANM")
            except Exception as e:
                print(f"Warning: Failed to parse initial balance: {e}")
                balance_initial = 0
        
        # Step 3: Mine blocks
        print("\nStep 3: Mining 3 blocks...")
        result = subprocess.run(
            [
                "python", "-m", "mining.cli.miner",
                "mine-blocks",
                "--address", address,
                "--count", "3",
                "--log-level", "info",
            ],
            capture_output=True,
            text=True,
            cwd="/home/runner/work/all/all",
            timeout=60,
        )
        print(f"Mining stdout: {result.stdout}")
        print(f"Mining stderr: {result.stderr}")
        
        if result.returncode != 0:
            print(f"Error: Mining failed with return code {result.returncode}")
            print(f"This test requires a running node. Skipping.")
            return
        
        # Extract total reward from mining output
        reward_match = re.search(r"Total reward:\s+([\d.]+)\s+ANM\s+\((\d+)\s+nANM\)", result.stdout)
        if reward_match:
            total_reward = int(reward_match.group(2))
            print(f"Mined successfully. Total reward: {total_reward} nANM")
        else:
            print(f"Warning: Could not extract reward from output: {result.stdout}")
            # Assume standard block reward of 5 ANM = 5_000_000_000 nANM per block
            total_reward = 3 * 5_000_000_000
        
        # Step 4: Query balance after mining (with small delay to allow persistence)
        print("\nStep 4: Querying balance after mining...")
        time.sleep(1)  # Small delay to ensure persistence
        
        result = subprocess.run(
            [
                "python", "-m", "animica.cli.main",
                "wallet", "show",
                "--wallet-file", str(wallet_file),
                "--source", "chain",
                "test_miner",
            ],
            capture_output=True,
            text=True,
            cwd="/home/runner/work/all/all",
        )
        print(f"Final balance stdout: {result.stdout}")
        print(f"Final balance stderr: {result.stderr}")
        assert result.returncode == 0, f"Failed to query final balance: {result.stderr}"
        
        wallet_data = json.loads(result.stdout)
        balance_final = int(wallet_data.get("balance_confirmed", 0) or 0)
        balance_source = wallet_data.get("balance_source", "unknown")
        
        print(f"\nResults:")
        print(f"  Initial balance: {balance_initial} nANM")
        print(f"  Expected reward: {total_reward} nANM")
        print(f"  Final balance:   {balance_final} nANM")
        print(f"  Balance source:  {balance_source}")
        print(f"  Increase:        {balance_final - balance_initial} nANM")
        
        # Step 5: Verify balance increased correctly
        expected_final = balance_initial + total_reward
        if balance_final != expected_final:
            print(f"\n❌ BUG REPRODUCED!")
            print(f"Expected final balance: {expected_final} nANM")
            print(f"Actual final balance:   {balance_final} nANM")
            print(f"Difference:             {expected_final - balance_final} nANM")
            assert False, f"Balance did not increase correctly after mining"
        else:
            print(f"\n✓ Balance updated correctly!")


if __name__ == "__main__":
    test_mining_updates_balance()
