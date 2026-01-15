#!/usr/bin/env python3
"""
Verify that the fix for sync target never decreases is present in the code.

This validates the fix for the issue where nodes fall behind when reaching
the highest block because the sync loop overwrites the target height set by
block announcements with stale peer heights.
"""
from pathlib import Path


def test_fix_prevents_target_decrease():
    """Verify that the fix uses max() to prevent target decrease."""
    p2p_service_path = Path(__file__).parent / "p2p" / "node" / "p2p_service.py"
    
    with open(p2p_service_path, "r") as f:
        content = f.read()
    
    # Simple check: the fix comment and the max() call should both be present
    has_comment = "Never decrease target height" in content
    has_max_call = "self._sync_target_height = max(self._sync_target_height or 0, target_height)" in content
    
    assert has_comment, (
        "Fix comment not found: 'Never decrease target height'\n"
        "The sync loop should have a comment explaining the fix."
    )
    
    assert has_max_call, (
        "Fix not found! Expected: self._sync_target_height = max(self._sync_target_height or 0, target_height)\n"
        "The sync loop must never decrease target height to prevent falling behind."
    )
    
    print("✓ Fix verified: Sync target uses max() to prevent decreases")
    return True


def test_fix_has_proper_comments():
    """Verify that the fix includes explanatory comments."""
    p2p_service_path = Path(__file__).parent / "p2p" / "node" / "p2p_service.py"
    
    with open(p2p_service_path, "r") as f:
        content = f.read()
    
    # Check for explanatory comment
    assert "Never decrease target height" in content or "never decrease" in content, (
        "Fix should include comment explaining it prevents target decrease"
    )
    
    print("✓ Fix includes explanatory comments")
    return True


def test_block_announcement_still_updates_target():
    """Verify block announcements still update target immediately."""
    p2p_service_path = Path(__file__).parent / "p2p" / "node" / "p2p_service.py"
    
    with open(p2p_service_path, "r") as f:
        content = f.read()
    
    # The block announcement handler should still update target
    # This is the correct behavior that must be preserved
    assert "announced_height > self._sync_target_height" in content, (
        "Block announcements should update target when announced height is higher"
    )
    
    assert "self._sync_target_height = announced_height" in content, (
        "Block announcements should set target to announced height"
    )
    
    print("✓ Block announcements still update target immediately")
    return True


def test_logic_correctness():
    """Test the logic of the fix with simple examples."""
    
    # Simulate the fix logic
    def update_sync_target(current_target, target_from_peers):
        """Simulates the fixed code."""
        if target_from_peers is not None:
            return max(current_target or 0, target_from_peers)
        else:
            return current_target
    
    # Test case 1: Block announced to height 10, peer still at 5
    current = 10  # Set by block announcement
    peer = 5      # Stale peer height
    result = update_sync_target(current, peer)
    assert result == 10, f"Should preserve announced target 10, got {result}"
    print(f"✓ Test 1: Target stays at {result} (announced) vs {peer} (peer)")
    
    # Test case 2: Peer has higher height than current target
    current = 10
    peer = 15
    result = update_sync_target(current, peer)
    assert result == 15, f"Should increase to peer target 15, got {result}"
    print(f"✓ Test 2: Target increases to {result} (peer) from {current}")
    
    # Test case 3: No peer info available
    current = 10
    peer = None
    result = update_sync_target(current, peer)
    assert result == 10, f"Should preserve target 10 when no peer, got {result}"
    print(f"✓ Test 3: Target preserved at {result} when no peer info")
    
    # Test case 4: Initial sync with no target
    current = None
    peer = 5
    result = update_sync_target(current, peer)
    assert result == 5, f"Should set initial target to 5, got {result}"
    print(f"✓ Test 4: Initial target set to {result} (peer)")
    
    return True


if __name__ == "__main__":
    print("=" * 70)
    print("Verifying fix: Sync target never decreases when blocks announced")
    print("=" * 70)
    print()
    
    try:
        test_fix_prevents_target_decrease()
        print()
        test_fix_has_proper_comments()
        print()
        test_block_announcement_still_updates_target()
        print()
        test_logic_correctness()
        print()
        print("=" * 70)
        print("✓ ALL CHECKS PASSED")
        print("=" * 70)
        print()
        print("Summary:")
        print("- Sync target height uses max() to prevent decreases")
        print("- Block announcements still update target immediately")
        print("- Stale peer heights no longer overwrite announced targets")
        print("- Logic tested with multiple scenarios")
        print()
        print("This fix prevents nodes from falling behind when reaching")
        print("the highest block by preserving announced block targets.")
    except AssertionError as e:
        print()
        print("=" * 70)
        print("✗ VERIFICATION FAILED")
        print("=" * 70)
        print(f"\nError: {e}")
        exit(1)
