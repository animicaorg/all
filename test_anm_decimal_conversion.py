#!/usr/bin/env python3
"""
Test to verify that 9 decimals is the correct value for ANM.

This test demonstrates the difference between using 18 decimals (wrong) and 9 decimals (correct).
"""

def test_anm_decimals():
    """Test ANM decimal conversion."""
    
    # Example: Mining 100 blocks = 500 ANM = 500,000,000,000 nANM
    balance_nANM = 500_000_000_000  # 500 ANM
    
    print("=" * 80)
    print("ANM DECIMAL CONVERSION TEST")
    print("=" * 80)
    print()
    print(f"Balance in nANM: {balance_nANM:,}")
    print()
    
    # Convert with 9 decimals (CORRECT for ANM)
    anm_9_decimals = balance_nANM / (10 ** 9)
    print(f"With 9 decimals (CORRECT): {anm_9_decimals:,.9f} ANM")
    print(f"  → 1 ANM = 10^9 nANM")
    print()
    
    # Convert with 18 decimals (WRONG - Ethereum standard)
    anm_18_decimals = balance_nANM / (10 ** 18)
    print(f"With 18 decimals (WRONG):  {anm_18_decimals:.18f} ANM")
    print(f"  → Would display as: {anm_18_decimals} ANM")
    print(f"  → This is 10^9 times SMALLER than correct!")
    print()
    
    # Calculate the error ratio
    ratio = anm_9_decimals / anm_18_decimals if anm_18_decimals > 0 else 0
    print(f"Error ratio: {ratio:,.0f}x")
    print()
    
    # Show what happens with explorer2 vs wrong wallet
    print("=" * 80)
    print("COMPARISON: Explorer2 vs Wallet (with wrong decimals)")
    print("=" * 80)
    print()
    print(f"Explorer2 (9 decimals): {anm_9_decimals:,.2f} ANM")
    print(f"Wallet (18 decimals):   {anm_18_decimals:.9f} ANM")
    print()
    print(f"Explorer2 shows {ratio:,.0f}x MORE than wallet!")
    print()
    
    # But the problem statement says 3x, not 10^9x
    # So the actual issue must be state DB inflation, not decimal mismatch
    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)
    print()
    print("The decimal mismatch (18 vs 9) would cause explorer2 to show")
    print(f"1,000,000,000x MORE than wallet, not 3x.")
    print()
    print("Therefore, the '3x balance' issue is most likely due to:")
    print("  1. State DB has 3x inflated balances (due to 2 state rebuilds)")
    print("  2. Explorer2 correctly displays the inflated value")
    print("  3. Wallet may be using cached/old value or different node")
    print()
    print("The decimal fix (18→9) is still needed for correctness, but it's")
    print("not the primary cause of the 3x discrepancy.")
    print()


if __name__ == "__main__":
    test_anm_decimals()
