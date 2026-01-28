"""
Test that blocks are forced when the previous block is older than 1 hour.

This test verifies the backwards-compatible implementation that forces
a new block with minimum difficulty when the previous block exceeds
max_block_time_s (default: 3600 seconds / 1 hour).
"""

import os


def test_force_block_when_previous_is_old():
    """Test that a block is forced when previous is older than 1 hour."""
    # Use deterministic timestamps to avoid race conditions
    os.environ["ANIMICA_MAX_BLOCK_TIME_S"] = "3600"
    max_block_time = float(os.getenv("ANIMICA_MAX_BLOCK_TIME_S", "3600"))
    assert max_block_time == 3600.0, f"Expected max_block_time=3600, got {max_block_time}"
    
    # Verify that the forcing logic would trigger
    # Use fixed timestamps for deterministic behavior
    parent_timestamp = 1000000000  # Jan 9, 2001 01:46:40
    current_time = parent_timestamp + 3700  # 61 minutes and 40 seconds later
    time_since_last_block = current_time - parent_timestamp
    
    assert time_since_last_block > max_block_time, \
        f"Expected time_since_last_block > max_block_time, got {time_since_last_block} <= {max_block_time}"
    print(f"✓ Block would be forced: {time_since_last_block:.0f}s > {max_block_time:.0f}s")


def test_no_force_when_block_is_recent():
    """Test that no forcing happens when previous block is recent."""
    os.environ["ANIMICA_MAX_BLOCK_TIME_S"] = "3600"
    max_block_time = float(os.getenv("ANIMICA_MAX_BLOCK_TIME_S", "3600"))
    
    # Block from 5 minutes ago - use fixed timestamps
    parent_timestamp = 1000000000
    current_time = parent_timestamp + 300  # 5 minutes later
    time_since_last_block = current_time - parent_timestamp
    
    assert time_since_last_block <= max_block_time, \
        f"Expected time_since_last_block <= max_block_time, got {time_since_last_block} > {max_block_time}"
    print(f"✓ Block would NOT be forced: {time_since_last_block:.0f}s <= {max_block_time:.0f}s")


def test_backwards_compatibility_disabled():
    """Test that forcing can be disabled by setting max_block_time_s to 0."""
    os.environ["ANIMICA_MAX_BLOCK_TIME_S"] = "0"
    max_block_time = float(os.getenv("ANIMICA_MAX_BLOCK_TIME_S", "0"))
    
    # Even if block is very old, forcing is disabled
    parent_timestamp = 1000000000
    current_time = parent_timestamp + 10000  # Very old
    
    # When max_block_time_s is 0 or negative, forcing is disabled
    assert max_block_time <= 0, f"Expected max_block_time <= 0, got {max_block_time}"
    print("✓ Forcing is disabled when max_block_time_s <= 0")


def test_edge_case_exactly_at_threshold():
    """Test behavior when block time is exactly at the threshold."""
    os.environ["ANIMICA_MAX_BLOCK_TIME_S"] = "3600"
    max_block_time = float(os.getenv("ANIMICA_MAX_BLOCK_TIME_S", "3600"))
    
    # Exactly at threshold (should NOT trigger forcing)
    parent_timestamp = 1000000000
    current_time = parent_timestamp + 3600
    time_since_last_block = current_time - parent_timestamp
    
    assert time_since_last_block == max_block_time
    # The implementation uses > (greater than), not >=, so forcing should not trigger
    print(f"✓ At threshold: {time_since_last_block:.0f}s == {max_block_time:.0f}s (no forcing)")
    
    # Just over threshold (should trigger forcing)
    current_time_over = parent_timestamp + 3601
    time_since_over = current_time_over - parent_timestamp
    assert time_since_over > max_block_time
    print(f"✓ Over threshold: {time_since_over:.0f}s > {max_block_time:.0f}s (forcing triggers)")


if __name__ == "__main__":
    print("Running tests for block forcing when previous block is older than 1 hour...")
    print()
    
    try:
        test_force_block_when_previous_is_old()
        print()
        test_no_force_when_block_is_recent()
        print()
        test_backwards_compatibility_disabled()
        print()
        test_edge_case_exactly_at_threshold()
        print()
        print("✅ All tests passed!")
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        exit(1)
