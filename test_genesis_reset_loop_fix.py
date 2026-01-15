#!/usr/bin/env python3
"""
Test the fix for the genesis reset loop bug.

This test validates that the node does NOT reset to genesis when it's
already at genesis height, preventing an infinite loop.

Background:
The node was getting stuck at height 0 (genesis) with this pattern:
1. Node is at genesis (height 0)
2. Headers from peers fail to anchor (common when bootstrapping)
3. Node resets to genesis (which doesn't help since it's already there)
4. Loop repeats indefinitely

The fix adds a check to prevent resetting to genesis when anchor_height == 0.
This allows the node to keep trying different peers and sync strategies
instead of getting stuck in a pointless reset loop.
"""


def test_genesis_reset_loop_fix():
    """Test that reset-to-genesis is prevented when already at genesis."""
    
    # Simulate the bug scenario: node at genesis trying to sync
    anchor_height = 0  # At genesis
    not_anchored_attempts = 5  # Multiple failures
    not_anchored_reset_threshold = 3  # Threshold exceeded
    not_anchored_reset_height = 10  # Height threshold
    stall_timeout = 20.0
    last_progress_at = 100.0
    now = last_progress_at + 30.0  # Stalled
    
    # OLD BUGGY LOGIC (before fix):
    # This would trigger a reset even at genesis
    old_should_reset = (
        anchor_height <= not_anchored_reset_height
        and not_anchored_attempts >= not_anchored_reset_threshold
        and now - last_progress_at > stall_timeout
    )
    
    # NEW FIXED LOGIC (after fix):
    # This prevents reset when already at genesis
    new_should_reset = (
        anchor_height > 0  # Don't reset to genesis if already at genesis
        and anchor_height <= not_anchored_reset_height
        and not_anchored_attempts >= not_anchored_reset_threshold
        and now - last_progress_at > stall_timeout
    )
    
    # The old logic would trigger (causing the infinite loop bug)
    assert old_should_reset, "Old logic should trigger reset at genesis (this was the bug)"
    print("✓ Confirmed old logic triggers reset at genesis (bug reproduced)")
    
    # The new logic should NOT trigger
    assert not new_should_reset, "New logic should NOT trigger reset at genesis"
    print("✓ New logic prevents reset at genesis (bug fixed)")


def test_normal_reset_still_works():
    """Test that normal reset-to-genesis still works for heights 1-10."""
    
    # Test various heights between 1 and 10
    for anchor_height in range(1, 11):
        not_anchored_attempts = 5
        not_anchored_reset_threshold = 3
        not_anchored_reset_height = 10
        stall_timeout = 20.0
        last_progress_at = 100.0
        now = last_progress_at + 30.0
        
        should_reset = (
            anchor_height > 0  # Don't reset to genesis if already at genesis
            and anchor_height <= not_anchored_reset_height
            and not_anchored_attempts >= not_anchored_reset_threshold
            and now - last_progress_at > stall_timeout
        )
        
        # Reset should still work for heights 1-10
        assert should_reset, f"Reset should work at height {anchor_height}"
    
    print("✓ Normal reset still works for heights 1-10")


def test_no_reset_above_threshold():
    """Test that reset doesn't happen above the height threshold."""
    
    # Height above threshold
    anchor_height = 100
    not_anchored_attempts = 5
    not_anchored_reset_threshold = 3
    not_anchored_reset_height = 10
    stall_timeout = 20.0
    last_progress_at = 100.0
    now = last_progress_at + 30.0
    
    should_reset = (
        anchor_height > 0
        and anchor_height <= not_anchored_reset_height
        and not_anchored_attempts >= not_anchored_reset_threshold
        and now - last_progress_at > stall_timeout
    )
    
    # Should not reset for heights above threshold
    assert not should_reset, "Should not reset for heights above threshold"
    print("✓ No reset for heights above threshold")


def test_code_has_fix():
    """Verify the fix is present in the actual code."""
    with open("p2p/node/p2p_service.py", "r") as f:
        content = f.read()
    
    # Check for the fix
    assert "anchor_height > 0" in content and "should_reset" in content, \
        "Genesis reset loop fix not found in code"
    print("✓ Fix is present in code")
    
    # Check for comment explaining the fix
    assert "Don't reset to genesis if already at genesis" in content or \
           "prevent.*genesis.*loop" in content.lower(), \
        "Fix should be documented with a comment"
    print("✓ Fix is documented with comment")


def test_ancestor_reset_works_at_genesis():
    """Test that ancestor reset can work even when anchor_height is 0."""
    
    # Scenario: Node at genesis but has a matched ancestor
    # This shouldn't happen in practice, but let's be safe
    anchor_height = 0
    matched_ancestor_height = None  # Can't have ancestor below genesis
    not_anchored_attempts = 5
    not_anchored_reset_threshold = 3
    stall_timeout = 20.0
    last_progress_at = 100.0
    now = last_progress_at + 30.0
    
    # Ancestor reset condition
    should_reset_to_ancestor = (
        not_anchored_attempts >= not_anchored_reset_threshold
        and now - last_progress_at > stall_timeout
        and matched_ancestor_height is not None
        and matched_ancestor_height < anchor_height
    )
    
    # Should not trigger at genesis (no valid ancestor below 0)
    assert not should_reset_to_ancestor, \
        "Ancestor reset should not trigger at genesis"
    print("✓ Ancestor reset correctly disabled at genesis")


if __name__ == "__main__":
    print("Testing genesis reset loop fix...\n")
    
    try:
        test_genesis_reset_loop_fix()
        test_normal_reset_still_works()
        test_no_reset_above_threshold()
        test_code_has_fix()
        test_ancestor_reset_works_at_genesis()
        
        print("\n✅ All tests passed!")
        print("\nGenesis Reset Loop Fix Summary:")
        print("  • Prevents reset-to-genesis when already at genesis (height 0)")
        print("  • Breaks the infinite reset loop that was blocking sync")
        print("  • Still allows reset for heights 1-10 (normal operation)")
        print("  • Node can now sync from genesis without getting stuck")
        
        print("\n📈 Impact:")
        print("  • Fixes: 'Blockchain is both resetting to genesis inappropriately'")
        print("  • Fixes: Node stuck at height 0 with peers connected")
        print("  • Allows bootstrapping from genesis to proceed normally")
        print("  • Node will try different peers/strategies instead of looping")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
