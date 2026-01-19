#!/usr/bin/env python3
"""
Manual test script to verify node status and wallet balance fixes.

This script simulates:
1. Mining a new block
2. Checking if `animica node status` shows the newly mined block
3. Checking if `animica wallet show` reflects the mining reward

Usage:
    python test_node_status_wallet_fix.py
"""

import subprocess
import sys
import time
import json
from pathlib import Path


def run_command(cmd: list[str], capture_output=True):
    """Run a command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=True,
        cwd=Path(__file__).parent,
    )
    return result


def get_node_status_height():
    """Get current height from node status command."""
    result = run_command(["python", "-m", "animica.cli.main", "node", "status"])
    if result.returncode != 0:
        print(f"Error getting node status: {result.stderr}")
        return None
    
    # Parse output to find head height
    for line in result.stdout.splitlines():
        if "Head height:" in line:
            try:
                height = int(line.split(":")[-1].strip())
                return height
            except ValueError:
                pass
    return None


def get_wallet_balance(address_or_label: str):
    """Get wallet balance for an address or label."""
    result = run_command([
        "python", "-m", "animica.cli.main",
        "wallet", "show", address_or_label
    ])
    if result.returncode != 0:
        print(f"Error getting wallet balance: {result.stderr}")
        return None
    
    try:
        data = json.loads(result.stdout)
        return data.get("balance")
    except json.JSONDecodeError:
        print(f"Error parsing wallet output: {result.stdout}")
        return None


def check_recent_blocks_in_status():
    """Check if recent blocks section in status command shows current height."""
    result = run_command(["python", "-m", "animica.cli.main", "node", "status"])
    if result.returncode != 0:
        return False, "Failed to get node status"
    
    output = result.stdout
    
    # Check for "Recent blocks:" section
    if "Recent blocks:" not in output:
        return False, "No 'Recent blocks' section found in status output"
    
    # Extract heights from recent blocks section
    lines = output.splitlines()
    recent_blocks_started = False
    heights = []
    
    for line in lines:
        if "Recent blocks:" in line:
            recent_blocks_started = True
            continue
        
        if recent_blocks_started:
            # Look for lines like "  123: 0x1234abcd 2026-01-19 20:24:25Z txs=0"
            stripped = line.strip()
            if stripped:
                try:
                    # Extract the height (part before the colon) and check if it's a digit
                    height_part = stripped.split(":")[0]
                    if height_part.isdigit():
                        height = int(height_part)
                        heights.append(height)
                except (ValueError, IndexError):
                    pass
            
            # Check if we've moved past the recent blocks section
            if stripped and not stripped.startswith(" ") and not stripped[0].isdigit():
                # End of recent blocks section
                break
    
    if not heights:
        return False, "No block heights found in recent blocks section"
    
    # The highest height should be the current head
    max_recent_height = max(heights)
    current_height = get_node_status_height()
    
    if current_height and max_recent_height == current_height:
        return True, f"Recent blocks shows current height {max_recent_height}"
    else:
        return False, f"Recent blocks max height {max_recent_height} != current height {current_height}"


def main():
    """Main test function."""
    print("=" * 80)
    print("Testing Node Status and Wallet Balance Fixes")
    print("=" * 80)
    print()
    
    # Test 1: Check if node status shows recent blocks correctly
    print("Test 1: Checking if node status recent blocks section is up-to-date")
    print("-" * 80)
    
    success, message = check_recent_blocks_in_status()
    if success:
        print(f"✅ PASS: {message}")
    else:
        print(f"❌ FAIL: {message}")
    print()
    
    # Test 2: Check if wallet balance updates (requires actual mining)
    print("Test 2: Wallet balance update after mining")
    print("-" * 80)
    print("NOTE: This test requires:")
    print("  1. A running node")
    print("  2. A wallet configured for mining")
    print("  3. Ability to mine a block")
    print()
    print("Manual verification steps:")
    print("  1. Get initial balance: animica wallet show <address>")
    print("  2. Mine a block: animica mine blocks --count 1 --address <address>")
    print("  3. Get new balance: animica wallet show <address>")
    print("  4. Verify balance increased by block reward")
    print()
    
    # Summary
    print("=" * 80)
    print("Test Summary")
    print("=" * 80)
    if success:
        print("✅ Node status fix verified: Recent blocks shows current height")
    else:
        print("❌ Node status fix needs investigation")
    print()
    print("⚠️  Wallet balance fix requires manual verification with actual mining")
    print()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
