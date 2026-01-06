#!/usr/bin/env python3
"""Test script to verify hashrate calculation logic."""

import math
import time


def test_hashrate_calculation():
    """Test the hashrate calculation logic."""
    
    print("Testing hashrate calculation logic...")
    print("-" * 60)
    
    # Simulate share submission tracking
    share_times = []
    current_theta_micro = 10_000_000  # 10 nats - more realistic for testing
    current_share_target = 0.25  # 25% of theta
    
    # Simulate finding 10 shares over 30 seconds
    base_time = time.time()
    for i in range(10):
        share_times.append(base_time + i * 3)  # One share every 3 seconds
    
    # Calculate hashrate
    time_span = share_times[-1] - share_times[0]
    share_count = len(share_times)
    shares_per_second = share_count / time_span
    
    print(f"Shares found: {share_count}")
    print(f"Time span: {time_span:.2f} seconds")
    print(f"Shares per second: {shares_per_second:.4f}")
    print()
    
    # Method 1: Using exponential relationship
    threshold_share_micro = current_theta_micro * current_share_target
    threshold_nats = threshold_share_micro / 1_000_000
    probability = math.exp(-threshold_nats)
    hashrate = shares_per_second / probability
    
    print(f"Theta (difficulty): {current_theta_micro / 1_000_000:.2f} nats")
    print(f"Share target: {current_share_target * 100:.0f}%")
    print(f"Share threshold: {threshold_nats:.2f} nats")
    print(f"Share probability: {probability:.6f} (1 in {1/probability:.0f})")
    print(f"Calculated hashrate: {hashrate:.2f} H/s")
    print()
    
    # Estimate time to find a block
    block_probability = math.exp(-current_theta_micro / 1_000_000)
    avg_hashes_needed = 1.0 / block_probability
    time_seconds = avg_hashes_needed / hashrate
    
    print(f"Block probability: {block_probability:.9f} (1 in {1/block_probability:.0f})")
    print(f"Average hashes to block: {avg_hashes_needed:.2e}")
    print(f"Estimated time to block: {time_seconds:.0f} seconds ({time_seconds/3600:.2f} hours)")
    print()
    
    # Verify the math makes sense
    # The ratio tells us how much harder a block is than a share
    share_difficulty_ratio = (1/block_probability) / (1/probability)
    print(f"Block is {share_difficulty_ratio:.2f}x harder than share")
    # This should equal e^(theta * (1 - share_target))
    expected_ratio = math.exp(current_theta_micro / 1_000_000 * (1 - current_share_target))
    print(f"Expected ratio (e^(theta*(1-share_target))): {expected_ratio:.2f}x")
    print()
    
    if abs(share_difficulty_ratio - expected_ratio) / expected_ratio < 0.01:
        print("✓ Calculation verified - ratio matches expected value!")
    else:
        print(f"✗ Warning: ratio {share_difficulty_ratio:.2f} doesn't match expected {expected_ratio:.2f}")
    
    print("-" * 60)


def test_formatting():
    """Test time formatting for different ranges."""
    
    print("\nTesting time formatting...")
    print("-" * 60)
    
    test_times = [
        (30, "30s"),
        (90, "1.5m"),
        (300, "5.0m"),
        (3600, "1.0h"),
        (7200, "2.0h"),
        (86400, "1.0d"),
        (172800, "2.0d"),
    ]
    
    for time_seconds, expected in test_times:
        # Format the time
        if time_seconds < 60:
            time_str = f"{time_seconds:.0f}s"
        elif time_seconds < 3600:
            minutes = time_seconds / 60
            time_str = f"{minutes:.1f}m"
        elif time_seconds < 86400:
            hours = time_seconds / 3600
            time_str = f"{hours:.1f}h"
        else:
            days = time_seconds / 86400
            time_str = f"{days:.1f}d"
        
        match = "✓" if time_str == expected else "✗"
        print(f"{match} {time_seconds}s -> ~{time_str} (expected: ~{expected})")
    
    print("-" * 60)


if __name__ == "__main__":
    test_hashrate_calculation()
    test_formatting()
    print("\nAll tests completed!")
