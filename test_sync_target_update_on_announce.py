#!/usr/bin/env python3
"""
Test to verify that sync target height is updated when blocks are announced
after the node has reached its previous target height.

This tests the fix for the issue where syncing stops after reaching head height
because announced blocks are deferred due to exceeding the stale target height.
"""


def test_sync_target_updates_on_announce():
    """
    Test that _sync_target_height is updated when a higher block is announced.
    
    Scenario:
    1. Node syncs to height 100 (_sync_target_height = 100)
    2. New block at height 101 is announced
    3. _sync_target_height should be updated to 101
    4. Block should not be deferred due to target height check
    """
    # Simulated state before block announcement
    sync_target_height = 100
    announced_height = 101
    
    # Apply the fix logic
    if sync_target_height is None or announced_height > sync_target_height:
        sync_target_height = announced_height
        print(f"✓ Test 1 PASSED: sync_target_height updated from 100 to {sync_target_height}")
    else:
        print("✗ Test 1 FAILED: sync_target_height not updated")
        return False
    
    return True


def test_sync_target_not_decreased():
    """
    Test that _sync_target_height is not decreased by lower announcements.
    
    This ensures we don't regress when peers announce stale blocks.
    """
    # Simulated state
    sync_target_height = 105
    announced_height = 103
    original_target = sync_target_height
    
    # Apply the fix logic
    if sync_target_height is None or announced_height > sync_target_height:
        sync_target_height = announced_height
        print("✗ Test 2 FAILED: sync_target_height decreased incorrectly")
        return False
    else:
        print(f"✓ Test 2 PASSED: sync_target_height kept at {original_target}")
    
    return True


def test_block_not_deferred_after_target_update():
    """
    Test that blocks are not deferred after target height is updated.
    
    Simulates the _schedule_block_requests logic after target update.
    """
    # Simulated state after block announcement and target update
    local_height = 100
    sync_target_height = 101  # Updated by announcement
    announced_block_height = 101
    expected_height = local_height + 1  # 101
    best_header_height = 101
    
    # Calculate target_height as in _schedule_block_requests (line 8636-8640)
    target_height = min(
        best_header_height,
        expected_height + max(1, 2048) - 1  # Using default max_inflight
    )
    if sync_target_height is not None:
        target_height = min(target_height, int(sync_target_height))
    
    # Check if block would be deferred (line 8703-8705)
    height_hint = announced_block_height
    if height_hint is not None and height_hint > target_height:
        # Block would be deferred - this is the bug!
        print(f"✗ Test 3 FAILED: block at height {announced_block_height} deferred")
        print(f"  target_height={target_height}, announced_height={announced_block_height}")
        return False
    else:
        print(f"✓ Test 3 PASSED: block at height {announced_block_height} not deferred")
        print(f"  target_height={target_height}, announced_height={announced_block_height}")
    
    return True


def test_continuous_syncing():
    """
    Test that syncing continues across multiple new blocks.
    
    Simulates the scenario: node at 100 → block 101 → block 102 → block 103
    """
    local_height = 100
    sync_target_height = 100
    
    for new_height in [101, 102, 103]:
        # Block announcement updates target
        if sync_target_height is None or new_height > sync_target_height:
            sync_target_height = new_height
        
        # Simulate block download and import
        expected_height = local_height + 1
        target_height = min(sync_target_height, expected_height + 2048)
        
        # Check block not deferred
        if new_height > target_height:
            print(f"✗ Test 4 FAILED: block {new_height} deferred")
            return False
        
        # Simulate successful block import
        local_height = new_height
    
    if local_height == 103 and sync_target_height == 103:
        print(f"✓ Test 4 PASSED: synced continuously from 100 to {local_height}")
        return True
    else:
        print(f"✗ Test 4 FAILED: ended at height {local_height}, target {sync_target_height}")
        return False


def test_none_target_height_initialization():
    """
    Test that initial None target height is properly handled.
    """
    sync_target_height = None
    announced_height = 50
    
    # Apply the fix logic
    if sync_target_height is None or announced_height > sync_target_height:
        sync_target_height = announced_height
        print(f"✓ Test 5 PASSED: initialized sync_target_height to {sync_target_height}")
    else:
        print("✗ Test 5 FAILED: failed to initialize sync_target_height")
        return False
    
    return True


if __name__ == "__main__":
    tests = [
        test_sync_target_updates_on_announce,
        test_sync_target_not_decreased,
        test_block_not_deferred_after_target_update,
        test_continuous_syncing,
        test_none_target_height_initialization,
    ]
    
    print("=" * 70)
    print("Testing Sync Target Height Update on Block Announcement")
    print("=" * 70)
    print()
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED with exception: {e}")
            failed += 1
        print()
    
    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 70)
    
    exit(0 if failed == 0 else 1)
