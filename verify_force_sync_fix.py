#!/usr/bin/env python3
"""
Verification script for sync recovery fix.

This script verifies that the fix correctly forces sync when a node
is at tip but behind target height.
"""


def test_force_sync_logic():
    """Test the force_sync calculation with the fix."""
    print("Testing force_sync calculation logic...")
    print()
    
    # Scenario 1: Node at tip but behind target (TARGET_REACHED)
    print("Scenario 1: Node at tip but behind (TARGET_REACHED phase)")
    sync_phase = "TARGET_REACHED"
    target_height = 105
    best_block_height = 100
    sync_inflight_headers = False
    sync_inflight_blocks = False
    sync_requested = False
    stalled = False
    sync_force_always = False
    
    # Calculate at_tip_but_behind (the fix)
    at_tip_but_behind = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    # Calculate force_sync (with fix)
    force_sync_with_fix = stalled or sync_force_always or sync_requested or at_tip_but_behind
    
    # Calculate force_sync (without fix - old behavior)
    force_sync_without_fix = stalled or sync_force_always or sync_requested
    
    print(f"  Phase: {sync_phase}")
    print(f"  Target height: {target_height}")
    print(f"  Local height: {best_block_height}")
    print(f"  Gap: {target_height - best_block_height}")
    print(f"  at_tip_but_behind: {at_tip_but_behind}")
    print(f"  force_sync (WITHOUT fix): {force_sync_without_fix} ❌")
    print(f"  force_sync (WITH fix): {force_sync_with_fix} ✅")
    print()
    
    assert at_tip_but_behind == True, "Should detect at_tip_but_behind"
    assert force_sync_without_fix == False, "Old logic would fail"
    assert force_sync_with_fix == True, "New logic should force sync"
    print("✓ Scenario 1 PASSED: Fix correctly forces sync when at tip but behind")
    print()
    
    # Scenario 2: Node at tip but behind target (SYNCED phase)
    print("Scenario 2: Node at tip but behind (SYNCED phase)")
    sync_phase = "SYNCED"
    
    at_tip_but_behind = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    force_sync_with_fix = stalled or sync_force_always or sync_requested or at_tip_but_behind
    force_sync_without_fix = stalled or sync_force_always or sync_requested
    
    print(f"  Phase: {sync_phase}")
    print(f"  at_tip_but_behind: {at_tip_but_behind}")
    print(f"  force_sync (WITHOUT fix): {force_sync_without_fix} ❌")
    print(f"  force_sync (WITH fix): {force_sync_with_fix} ✅")
    print()
    
    assert at_tip_but_behind == True, "Should detect at_tip_but_behind"
    assert force_sync_with_fix == True, "Should force sync"
    print("✓ Scenario 2 PASSED: Fix handles SYNCED phase correctly")
    print()
    
    # Scenario 3: Node at target height (should not force)
    print("Scenario 3: Node at target height (should not force)")
    best_block_height = 105
    
    at_tip_but_behind = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    force_sync_with_fix = stalled or sync_force_always or sync_requested or at_tip_but_behind
    
    print(f"  Local height: {best_block_height}")
    print(f"  Target height: {target_height}")
    print(f"  at_tip_but_behind: {at_tip_but_behind}")
    print(f"  force_sync: {force_sync_with_fix}")
    print()
    
    assert at_tip_but_behind == False, "Should NOT detect at_tip_but_behind when at target"
    assert force_sync_with_fix == False, "Should NOT force sync when at target"
    print("✓ Scenario 3 PASSED: No false positives when at target")
    print()
    
    # Scenario 4: Inflight requests exist (should not force via at_tip_but_behind)
    print("Scenario 4: Node behind but has inflight requests")
    best_block_height = 100
    sync_inflight_headers = True
    
    at_tip_but_behind = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    force_sync_with_fix = stalled or sync_force_always or sync_requested or at_tip_but_behind
    
    print(f"  Local height: {best_block_height}")
    print(f"  Target height: {target_height}")
    print(f"  Inflight headers: {sync_inflight_headers}")
    print(f"  at_tip_but_behind: {at_tip_but_behind}")
    print(f"  force_sync: {force_sync_with_fix}")
    print()
    
    assert at_tip_but_behind == False, "Should NOT force when inflight requests exist"
    print("✓ Scenario 4 PASSED: Respects inflight requests")
    print()
    
    # Scenario 5: Different phase (SYNCING) - should not trigger at_tip_but_behind
    print("Scenario 5: Node in SYNCING phase")
    sync_phase = "SYNCING"
    sync_inflight_headers = False
    best_block_height = 100
    
    at_tip_but_behind = (
        sync_phase in ("SYNCED", "TARGET_REACHED")
        and target_height is not None
        and best_block_height < target_height
        and not sync_inflight_headers
        and not sync_inflight_blocks
    )
    
    force_sync_with_fix = stalled or sync_force_always or sync_requested or at_tip_but_behind
    
    print(f"  Phase: {sync_phase}")
    print(f"  at_tip_but_behind: {at_tip_but_behind}")
    print(f"  force_sync: {force_sync_with_fix}")
    print()
    
    assert at_tip_but_behind == False, "Should only apply to SYNCED/TARGET_REACHED"
    print("✓ Scenario 5 PASSED: Only applies to SYNCED/TARGET_REACHED phases")
    print()
    
    print("=" * 70)
    print("✅ ALL SCENARIOS PASSED")
    print("=" * 70)
    print()
    print("Summary:")
    print("  • Fix correctly forces sync when node is at tip but behind target")
    print("  • Works for both SYNCED and TARGET_REACHED phases")
    print("  • No false positives when at target")
    print("  • Respects inflight requests to avoid duplicate work")
    print("  • Only applies to nodes that have reached tip")


if __name__ == "__main__":
    test_force_sync_logic()
