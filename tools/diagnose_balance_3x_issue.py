#!/usr/bin/env python3
"""
Diagnose the 3x Balance Issue Between Explorer2 and Wallets

This script helps diagnose why explorer2 shows 3x the balance compared to wallets.
It checks:
1. Whether both RPC methods return the same value
2. Whether the state DB has inflated values
3. What the actual inflation factor is

Usage:
    python diagnose_balance_3x_issue.py --rpc http://localhost:8545 --address <address>
"""

import argparse
import json
import sys
import requests


def query_rpc(rpc_url: str, method: str, params: list) -> dict:
    """Query RPC endpoint."""
    try:
        response = requests.post(
            rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Failed to query {method}: {e}")
        return {}


def hex_to_anm(hex_value: str) -> float:
    """Convert hex balance to ANM."""
    if hex_value.startswith("0x"):
        nANM = int(hex_value, 16)
    else:
        nANM = int(hex_value)
    return nANM / 1_000_000_000  # 1 ANM = 10^9 nANM


def detect_inflation(balance_nANM: int) -> tuple:
    """Detect if balance is inflated.
    
    Note: This assumes a block reward of 5 ANM (5,000,000,000 nANM) which is
    the default for Animica mainnet. Different networks may use different rewards.
    """
    BLOCK_REWARD = 5_000_000_000  # 5 ANM per block (mainnet default)
    
    if balance_nANM == 0:
        return None, "Zero balance"
    
    if balance_nANM % BLOCK_REWARD != 0:
        return None, "Not a clean multiple of block reward"
    
    blocks = balance_nANM // BLOCK_REWARD
    
    # Threshold for inflation detection: 10,000 blocks (~50,000 ANM)
    # Balances above this that are divisible by small factors (2-10) are flagged.
    # This threshold assumes early-stage chains; adjust for mature networks.
    INFLATION_THRESHOLD_BLOCKS = 10_000
    
    if blocks < INFLATION_THRESHOLD_BLOCKS:
        return None, f"Normal (~{blocks} blocks mined)"
    
    # Check for inflation factors 2-10
    for factor in range(2, 11):
        if blocks % factor == 0:
            corrected_blocks = blocks // factor
            return factor, f"{factor}x inflated (actually ~{corrected_blocks} blocks)"
    
    return None, f"Normal (~{blocks} blocks mined)"


def main():
    parser = argparse.ArgumentParser(description="Diagnose 3x balance issue")
    parser.add_argument("--rpc", required=True, help="RPC URL (e.g., http://localhost:8545/rpc)")
    parser.add_argument("--address", required=True, help="Address to check")
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("BALANCE 3X ISSUE DIAGNOSIS")
    print("=" * 80)
    print()
    print(f"Address: {args.address}")
    print(f"RPC URL: {args.rpc}")
    print()
    
    # Test 1: Query via state.getBalance (used by explorer2)
    print("Test 1: Query via state.getBalance (used by explorer2)")
    print("-" * 80)
    result1 = query_rpc(args.rpc, "state.getBalance", [args.address, "latest"])
    if "result" in result1:
        balance1_hex = result1["result"]
        balance1_nANM = int(balance1_hex, 16) if balance1_hex.startswith("0x") else int(balance1_hex)
        balance1_ANM = balance1_nANM / 1_000_000_000
        print(f"✓ Result: {balance1_hex}")
        print(f"  In nANM: {balance1_nANM:,}")
        print(f"  In ANM:  {balance1_ANM:,.9f}")
    else:
        print(f"✗ Error: {result1.get('error', 'Unknown error')}")
        balance1_nANM = None
    print()
    
    # Test 2: Query via animica_getBalance (used by wallet extension)
    print("Test 2: Query via animica_getBalance (used by wallet extension)")
    print("-" * 80)
    result2 = query_rpc(args.rpc, "animica_getBalance", [args.address, "latest"])
    if "result" in result2:
        balance2_hex = result2["result"]
        balance2_nANM = int(balance2_hex, 16) if balance2_hex.startswith("0x") else int(balance2_hex)
        balance2_ANM = balance2_nANM / 1_000_000_000
        print(f"✓ Result: {balance2_hex}")
        print(f"  In nANM: {balance2_nANM:,}")
        print(f"  In ANM:  {balance2_ANM:,.9f}")
    else:
        print(f"✗ Error: {result2.get('error', 'Unknown error')}")
        balance2_nANM = None
    print()
    
    # Test 3: Query via eth_getBalance (Ethereum compatibility)
    print("Test 3: Query via eth_getBalance (Ethereum compatibility)")
    print("-" * 80)
    result3 = query_rpc(args.rpc, "eth_getBalance", [args.address, "latest"])
    if "result" in result3:
        balance3_hex = result3["result"]
        balance3_nANM = int(balance3_hex, 16) if balance3_hex.startswith("0x") else int(balance3_hex)
        balance3_ANM = balance3_nANM / 1_000_000_000
        print(f"✓ Result: {balance3_hex}")
        print(f"  In nANM: {balance3_nANM:,}")
        print(f"  In ANM:  {balance3_ANM:,.9f}")
    else:
        print(f"✗ Error: {result3.get('error', 'Unknown error')}")
        balance3_nANM = None
    print()
    
    # Analysis
    print("=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print()
    
    if balance1_nANM and balance2_nANM and balance3_nANM:
        if balance1_nANM == balance2_nANM == balance3_nANM:
            print("✓ All three methods return the SAME value")
            print("  → The RPC aliases are working correctly")
            print()
            
            # Check for inflation
            factor, explanation = detect_inflation(balance1_nANM)
            if factor:
                print(f"⚠ INFLATION DETECTED: {explanation}")
                print(f"  → Current balance:   {balance1_ANM:,.9f} ANM")
                corrected_nANM = balance1_nANM // factor
                corrected_ANM = corrected_nANM / 1_000_000_000
                print(f"  → Corrected balance: {corrected_ANM:,.9f} ANM")
                print()
                print("RECOMMENDATION:")
                print(f"  The balance in the state DB is {factor}x inflated.")
                print(f"  Run the balance correction tool to fix it:")
                print()
                print(f"  python tools/correct_balance_inflation.py \\")
                print(f"    --rpc {args.rpc} \\")
                print(f"    --db-path ~/.animica/chain-*/state.db \\")
                print(f"    --apply")
            else:
                print(f"✓ No inflation detected: {explanation}")
                print()
                print("CONCLUSION:")
                print("  The balance appears normal. The issue may be elsewhere:")
                print("  - Check if wallet extension has caching issues")
                print("  - Check if wallet is connected to a different node")
                print("  - Check if wallet is using a different address format")
        else:
            print("✗ The methods return DIFFERENT values!")
            print("  This indicates a bug in the RPC aliases.")
            print()
            if balance1_nANM and balance2_nANM:
                ratio = balance1_nANM / balance2_nANM if balance2_nANM else 0
                print(f"  state.getBalance / animica_getBalance ratio: {ratio:.2f}x")
    else:
        print("✗ Failed to query one or more methods")
        print("  Check that:")
        print("  - The node is running")
        print("  - The RPC endpoint is accessible")
        print("  - The address format is correct")
    
    print()
    print("=" * 80)


if __name__ == "__main__":
    main()
