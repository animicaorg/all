#!/usr/bin/env python3
"""
Demonstration script for instant blocks feature.

This script shows how instant blocks work when enabled.
Set ANIMICA_INSTANT_BLOCKS_ENABLED=1 to enable the feature.
"""
import os
import sys


def check_instant_blocks_enabled():
    """Check if instant blocks are enabled."""
    enabled = os.environ.get("ANIMICA_INSTANT_BLOCKS_ENABLED", "true").lower() in {
        "1", "true", "yes", "on"
    }
    return enabled


def main():
    print("=" * 70)
    print("Instant Blocks Feature Demonstration")
    print("=" * 70)
    print()
    
    enabled = check_instant_blocks_enabled()
    print(f"Instant blocks enabled: {enabled}")
    print()
    
    if not enabled:
        print("Instant blocks have been DISABLED.")
        print("To re-enable instant blocks (default behavior), set:")
        print("  export ANIMICA_INSTANT_BLOCKS_ENABLED=true")
        print()
        print("With instant blocks enabled:")
        print("  - Transactions create instant blocks immediately (< 1 second)")
        print("  - Instant blocks have zero block rewards")
        print("  - Instant blocks do NOT advance canonical height")
        print("  - Halving schedule is unchanged")
        print()
        return 0
    
    print("Instant blocks are ENABLED (default)!")
    print()
    print("What happens when you submit a transaction:")
    print()
    print("1. CLI: animica tx send --from <addr> --to <addr> --value 1.0")
    print("   → Transaction added to mempool")
    print("   → Instant block created automatically")
    print("   → Transaction included in instant block (< 1 second)")
    print("   → Block reward = 0")
    print("   → Canonical height unchanged")
    print()
    print("2. RPC: tx.sendRawTransaction")
    print("   → Same flow as CLI")
    print()
    print("3. P2P: Transaction received from peer")
    print("   → Same flow as CLI/RPC")
    print()
    print("Example:")
    print()
    print("  Chain state before:")
    print("    Block height: 100")
    print("    Canonical height: 100")
    print("    Total rewards: 100 blocks × subsidy")
    print()
    print("  After 5 transactions (creating 5 instant blocks):")
    print("    Block height: 105")
    print("    Canonical height: 100  ← unchanged!")
    print("    Total rewards: 100 blocks × subsidy  ← unchanged!")
    print()
    print("  After 1 normal block:")
    print("    Block height: 106")
    print("    Canonical height: 101  ← incremented!")
    print("    Total rewards: 101 blocks × subsidy")
    print()
    print("Key properties:")
    print("  ✅ Zero block rewards")
    print("  ✅ Non-advancing canonical height")
    print("  ✅ Skip PoW validation (nonce=0)")
    print("  ✅ Immediate transaction finality")
    print("  ✅ Unchanged halving schedule")
    print()
    
    # Try to import and show module status
    try:
        from rpc.methods import miner
        print("Module Status:")
        print(f"  ✅ miner module available")
        print(f"  ✅ _mine_instant_block: {hasattr(miner, '_mine_instant_block')}")
        print(f"  ✅ trigger_instant_block_on_tx_arrival: {hasattr(miner, 'trigger_instant_block_on_tx_arrival')}")
    except ImportError:
        print("Module Status:")
        print("  ⚠️  miner module not available (PYTHONPATH not set)")
        print("  ℹ️  Run from node environment for full functionality")
    
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
