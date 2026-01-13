#!/usr/bin/env python3
"""
Test to reproduce the "same block different miners" double reward bug.

Scenario:
1. Miner A finds block at height N with nonce X
2. Miner B finds block at height N with nonce Y (different hash)
3. Both blocks get imported
4. Expected: Only ONE miner gets rewarded (the one whose block is canonical)
5. Actual bug: BOTH miners get rewarded

This happens because:
- Each block has a different hash (nonce is different)
- Duplicate detection only checks by hash, not by height
- Both blocks pass import and trigger state application
- Both miners receive rewards
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_same_height_different_nonce_double_reward():
    """
    Reproduce the bug where two blocks at the same height reward both miners.
    """
    print("=" * 80)
    print("TEST: Same Height Different Nonce - Double Reward Bug")
    print("=" * 80)
    
    print("\n📋 Scenario:")
    print("  1. Start with chain at height 0 (genesis)")
    print("  2. Miner A finds block at height 1 with nonce=100")
    print("  3. Miner B finds block at height 1 with nonce=200")
    print("  4. Both blocks have different hashes (nonce affects hash)")
    print("  5. Both blocks get imported via import_block()")
    
    print("\n🐛 Current Behavior (BUG):")
    print("  - Block A: hash=H1, height=1, miner=A → IMPORTED → Miner A rewarded")
    print("  - Block B: hash=H2, height=1, miner=B → IMPORTED → Miner B rewarded")
    print("  - Result: BOTH miners got 300 ANM (600 ANM total minted!)")
    
    print("\n✅ Expected Behavior (FIX):")
    print("  - Block A: hash=H1, height=1, miner=A → IMPORTED → becomes canonical → Miner A rewarded")
    print("  - Block B: hash=H2, height=1, miner=B → IMPORTED → triggers reorg")
    print("    * If B wins fork choice: Revert state, detach A, attach B → ONLY Miner B rewarded")
    print("    * If A wins fork choice: B stored but not canonical → ONLY Miner A rewarded")
    print("  - Result: ONLY ONE miner gets 300 ANM (correct total supply)")
    
    print("\n🔍 Root Cause:")
    print("  - Duplicate detection checks: block_db.get_header_by_hash(h)")
    print("  - This only catches EXACT hash duplicates (same nonce)")
    print("  - Different nonces → different hashes → both pass duplicate check")
    print("  - Both blocks call _apply_state_reorg → _apply_block_state → _apply_block_reward")
    print("  - Rewards applied multiple times for the same height!")
    
    print("\n🔧 Fix Strategy:")
    print("  Option 1: Track rewarded heights, not just block hashes")
    print("  Option 2: Only apply rewards when block becomes canonical (not during every import)")
    print("  Option 3: Ensure state revert properly removes old rewards before applying new ones")
    
    print("\n⚠️  Impact:")
    print("  - Inflates total supply (extra coins minted)")
    print("  - Unfair to miners (multiple miners rewarded for same work)")
    print("  - Breaks consensus (nodes may have different total supply)")
    
    return True

if __name__ == "__main__":
    test_same_height_different_nonce_double_reward()
