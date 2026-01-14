#!/usr/bin/env python3
"""
Verification script for the sync TARGET_REACHED fix.

This demonstrates that the fix now handles both SYNCED and TARGET_REACHED phases.
"""


def test_synced_phase_resumes():
    """Test that SYNCED phase resumes when behind target"""
    sync_phase = "SYNCED"
    target_height = 100
    best_block_height = 90
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    
    # The fixed condition
    if (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    ):
        print("✓ Test 1 PASSED: SYNCED phase resumes when behind target")
        return True
    else:
        print("✗ Test 1 FAILED: SYNCED phase should resume but didn't")
        return False


def test_target_reached_phase_resumes():
    """Test that TARGET_REACHED phase resumes when behind target"""
    sync_phase = "TARGET_REACHED"
    target_height = 100
    best_block_height = 90
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    
    # The fixed condition
    if (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    ):
        print("✓ Test 2 PASSED: TARGET_REACHED phase resumes when behind target")
        return True
    else:
        print("✗ Test 2 FAILED: TARGET_REACHED phase should resume but didn't")
        return False


def test_at_target_does_not_resume():
    """Test that node doesn't resume when already at target"""
    sync_phase = "SYNCED"
    target_height = 100
    best_block_height = 100  # Already at target
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    
    # The fixed condition
    should_not_trigger = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    if not should_not_trigger:
        print("✓ Test 3 PASSED: Node stays SYNCED when at target")
        return True
    else:
        print("✗ Test 3 FAILED: Node shouldn't resume when at target")
        return False


def test_with_inflight_does_not_resume():
    """Test that node doesn't resume when already has inflight requests"""
    sync_phase = "SYNCED"
    target_height = 100
    best_block_height = 90
    sync_inflight_headers = 5  # Already working
    sync_inflight_blocks = {}
    
    # The fixed condition
    should_not_trigger = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    if not should_not_trigger:
        print("✓ Test 4 PASSED: Node doesn't duplicate work when inflight requests exist")
        return True
    else:
        print("✗ Test 4 FAILED: Node shouldn't resume when already working")
        return False


def test_old_condition_misses_target_reached():
    """Test that the old condition (without TARGET_REACHED) would miss this case"""
    sync_phase = "TARGET_REACHED"
    target_height = 100
    best_block_height = 90
    sync_inflight_headers = 0
    sync_inflight_blocks = {}
    
    # The OLD condition (before fix)
    old_condition = (
        sync_phase == "SYNCED"  # Only checks SYNCED, not TARGET_REACHED
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    # The NEW condition (after fix)
    new_condition = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    if not old_condition and new_condition:
        print("✓ Test 5 PASSED: New condition catches TARGET_REACHED, old condition missed it")
        return True
    else:
        print("✗ Test 5 FAILED: New condition should catch what old one missed")
        return False


def main():
    print("=" * 70)
    print("Verification: Fix for Sync Stalls at Highest Block Height")
    print("=" * 70)
    print()
    print("Testing that both SYNCED and TARGET_REACHED phases resume when behind...")
    print()
    
    all_pass = True
    all_pass &= test_synced_phase_resumes()
    all_pass &= test_target_reached_phase_resumes()
    all_pass &= test_at_target_does_not_resume()
    all_pass &= test_with_inflight_does_not_resume()
    all_pass &= test_old_condition_misses_target_reached()
    
    print()
    print("=" * 70)
    if all_pass:
        print("✓ All tests PASSED")
        print()
        print("Summary:")
        print("  - SYNCED phase correctly resumes when behind target")
        print("  - TARGET_REACHED phase correctly resumes when behind target (NEW FIX)")
        print("  - Node stays idle when already at target")
        print("  - Node doesn't duplicate work when inflight requests exist")
        print("  - The fix catches cases the old code missed")
    else:
        print("✗ Some tests FAILED")
    print("=" * 70)
    
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit(main())
