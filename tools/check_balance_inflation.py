#!/usr/bin/env python3
"""
Check for Balance Inflation Due to State Rebuild Bug

This script helps detect if balances have been inflated due to the state rebuild bug
where rewards were re-applied multiple times.

Usage:
    python check_balance_inflation.py --rpc http://localhost:8545 --address <address>

The script will:
1. Query the current balance from the node
2. Calculate expected balance based on block rewards
3. Detect if balance is a multiple of expected (indicating rebuilds)
4. Suggest correction factor
"""

import argparse
import json
import sys
from typing import Optional, Tuple
import requests


def query_balance(rpc_url: str, address: str) -> Optional[int]:
    """Query balance from RPC endpoint."""
    try:
        response = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "state.getBalance",
                "params": [address, "latest"],
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        
        if "result" in result:
            # Result is hex string like "0x..."
            balance_hex = result["result"]
            if balance_hex.startswith("0x"):
                return int(balance_hex, 16)
            return int(balance_hex)
        elif "error" in result:
            print(f"RPC error: {result['error']}")
            return None
    except Exception as e:
        print(f"Failed to query balance: {e}")
        return None


def detect_inflation_factor(balance: int) -> Tuple[Optional[int], str]:
    """
    Detect if balance appears to be inflated by checking for common multipliers.
    
    Returns:
        (factor, explanation) where factor is the suspected multiplication count
    """
    # Common factors to check (2x, 3x, 4x, 5x, 6x, 7x, 8x, 9x, 10x)
    factors = [2, 3, 4, 5, 6, 7, 8, 9, 10]
    
    explanations = {
        2: "Balance appears to be doubled (1 state rebuild)",
        3: "Balance appears to be tripled (2 state rebuilds)",
        4: "Balance appears to be quadrupled (3 state rebuilds)",
        5: "Balance appears to be 5x inflated (4 state rebuilds)",
        6: "Balance appears to be 6x inflated (5 state rebuilds)",
        7: "Balance appears to be 7x inflated (6 state rebuilds)",
        8: "Balance appears to be 8x inflated (7 state rebuilds)",
        9: "Balance appears to be 9x inflated (8 state rebuilds)",
        10: "Balance appears to be 10x inflated (9 state rebuilds)",
    }
    
    # Check if balance is divisible by block reward (5 ANM = 5_000_000_000 nANM)
    BLOCK_REWARD = 5_000_000_000
    
    if balance == 0:
        return None, "Balance is zero (no inflation detected)"
    
    if balance % BLOCK_REWARD != 0:
        return None, "Balance is not a clean multiple of block reward (manual inspection needed)"
    
    # Check each factor
    for factor in factors:
        test_balance = balance / factor
        if test_balance % BLOCK_REWARD == 0:
            # This factor makes sense
            blocks_mined = test_balance / BLOCK_REWARD
            return factor, explanations.get(factor, f"Balance appears to be {factor}x inflated") + f" | Estimated blocks mined: {int(blocks_mined)}"
    
    # No clean factor found
    blocks_mined = balance / BLOCK_REWARD
    return None, f"Balance appears normal | Estimated blocks mined: {int(blocks_mined)}"


def main():
    parser = argparse.ArgumentParser(
        description="Check for balance inflation due to state rebuild bug"
    )
    parser.add_argument(
        "--rpc",
        required=True,
        help="RPC endpoint URL (e.g., http://localhost:8545)",
    )
    parser.add_argument(
        "--address",
        required=True,
        help="Address to check (bech32 format)",
    )
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("Balance Inflation Checker")
    print("=" * 80)
    print()
    print(f"RPC URL: {args.rpc}")
    print(f"Address: {args.address}")
    print()
    
    # Query balance
    print("Querying balance...")
    balance = query_balance(args.rpc, args.address)
    
    if balance is None:
        print("Failed to query balance. Exiting.")
        return 1
    
    print(f"Current balance: {balance} nANM ({balance / 1_000_000_000:.9f} ANM)")
    print()
    
    # Detect inflation
    print("Analyzing for inflation...")
    factor, explanation = detect_inflation_factor(balance)
    print(explanation)
    print()
    
    if factor is not None:
        corrected_balance = balance // factor
        print("=" * 80)
        print("⚠️  INFLATION DETECTED!")
        print("=" * 80)
        print(f"Inflation factor: {factor}x")
        print(f"Current balance: {balance} nANM ({balance / 1_000_000_000:.9f} ANM)")
        print(f"Corrected balance: {corrected_balance} nANM ({corrected_balance / 1_000_000_000:.9f} ANM)")
        print()
        print("RECOMMENDATION:")
        print("1. Update to the latest version with the state height tracking fix")
        print("2. Apply balance correction (divide by {})".format(factor))
        print("3. Monitor logs to ensure no further rebuilds occur")
        print()
        print("NOTE: This is a preliminary check. Manual verification recommended.")
    else:
        print("=" * 80)
        print("✓ No obvious inflation detected")
        print("=" * 80)
        print("Balance appears to be consistent with mining activity.")
        print("If you suspect inflation, manual inspection is recommended.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
