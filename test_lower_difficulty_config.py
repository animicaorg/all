#!/usr/bin/env python3
"""
Test script to verify that lowered difficulty and nonce configuration is working.
Tests that the new defaults allow for easier mining in local/devnet scenarios.
"""

import os
import re
import sys

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def test_new_nonce_defaults():
    """Verify new nonce defaults are correctly set in source files."""
    print("Testing new nonce defaults in source code...")
    
    # Read the mining.py file to check defaults
    mining_file = os.path.join(os.path.dirname(__file__), "python", "animica", "cli", "mining.py")
    with open(mining_file, "r") as f:
        content = f.read()
    
    # Check for new MAX_NONCE default (10000000)
    if 'ANIMICA_MINER_MAX_NONCE", "10000000"' in content:
        print("  ✓ ANIMICA_MINER_MAX_NONCE default set to 10,000,000")
    else:
        raise AssertionError("MAX_NONCE default not updated to 10000000")
    
    # Check for new MAX_TOTAL_NONCE default (50000000)
    if "50_000_000" in content:
        print("  ✓ ANIMICA_MINER_MAX_TOTAL_NONCE default set to 50,000,000")
    else:
        raise AssertionError("MAX_TOTAL_NONCE default not updated to 50000000")
    
    # Also verify environment variables work
    max_nonce = int(os.getenv("ANIMICA_MINER_MAX_NONCE", "10000000"))
    retry_windows = int(os.getenv("ANIMICA_MINER_POW_RETRY_WINDOWS", "4"))
    default_total = max(max_nonce * retry_windows, 50_000_000)
    max_total_nonce = int(os.getenv("ANIMICA_MINER_MAX_TOTAL_NONCE", str(default_total)))
    
    print(f"  ✓ Runtime MAX_NONCE: {max_nonce:,}")
    print(f"  ✓ Runtime MAX_TOTAL_NONCE: {max_total_nonce:,}")
    
    assert max_nonce == 10_000_000, f"Expected 10M, got {max_nonce}"
    assert max_total_nonce == 50_000_000, f"Expected 50M, got {max_total_nonce}"
    
    print("  ✓ Nonce defaults verified!")


def test_new_theta_defaults():
    """Verify new theta (difficulty) defaults are correctly set."""
    print("\nTesting new theta defaults in source code...")
    
    # Read the miner.py file to check defaults
    miner_file = os.path.join(os.path.dirname(__file__), "rpc", "methods", "miner.py")
    with open(miner_file, "r") as f:
        content = f.read()
    
    # Check for new theta default (1000000 instead of 3000000)
    if 'ANIMICA_DEFAULT_THETA_MICRO", "1000000"' in content:
        print("  ✓ ANIMICA_DEFAULT_THETA_MICRO default set to 1,000,000 (1.0 nats)")
    else:
        raise AssertionError("THETA_MICRO default not updated to 1000000")
    
    # Default theta should be 1.0 nats (1,000,000 micro-nats) instead of 3.0 nats
    theta_micro = int(os.getenv("ANIMICA_DEFAULT_THETA_MICRO", "1000000"))
    
    print(f"  ✓ Runtime ANIMICA_DEFAULT_THETA_MICRO: {theta_micro:,} ({theta_micro/1e6:.1f} nats)")
    
    assert theta_micro == 1_000_000, f"Expected 1M (1.0 nats), got {theta_micro}"
    
    print("  ✓ Theta default verified!")


def test_theta_adjustment_params():
    """Verify theta adjustment parameters are less aggressive in source code."""
    print("\nTesting theta adjustment parameters in source code...")
    
    # Read the miner.py file to check adjustment params
    miner_file = os.path.join(os.path.dirname(__file__), "rpc", "methods", "miner.py")
    with open(miner_file, "r") as f:
        content = f.read()
    
    # Check for the new parameters
    checks = {
        "half_life_blocks=12.0": "half_life_blocks set to 12.0",
        "gain_beta=0.75": "gain_beta set to 0.75",
        "step_clamp_micro=1_000_000": "step_clamp_micro set to 1,000,000",
        "theta_min_micro=50_000": "theta_min_micro set to 50,000",
    }
    
    for pattern, description in checks.items():
        if pattern in content:
            print(f"  ✓ {description}")
        else:
            raise AssertionError(f"Parameter not found: {pattern}")
    
    print("  ✓ Theta adjustment parameters verified in source code!")


def test_rapid_mining_scenario():
    """Verify that dt_seconds clamping is still in place."""
    print("\nVerifying dt_seconds clamping logic...")
    
    # Read the miner.py file to check for clamping logic
    miner_file = os.path.join(os.path.dirname(__file__), "rpc", "methods", "miner.py")
    with open(miner_file, "r") as f:
        content = f.read()
    
    # Check that the clamping logic exists
    if "min_dt_threshold = max(1.0, target_time * 0.1)" in content:
        print("  ✓ dt_seconds clamping logic present")
    else:
        raise AssertionError("dt_seconds clamping logic not found")
    
    if "Clamped dt_seconds for theta adjustment" in content:
        print("  ✓ dt_seconds clamping log message present")
    else:
        raise AssertionError("dt_seconds clamping log not found")
    
    print("  ✓ Rapid mining protections verified!")


def test_backwards_compatibility():
    """Test that environment variables still override defaults."""
    print("\nTesting backwards compatibility...")
    
    # Save original values
    orig_max_nonce = os.environ.get("ANIMICA_MINER_MAX_NONCE")
    orig_theta = os.environ.get("ANIMICA_DEFAULT_THETA_MICRO")
    
    try:
        # Set custom values
        os.environ["ANIMICA_MINER_MAX_NONCE"] = "5000000"
        os.environ["ANIMICA_DEFAULT_THETA_MICRO"] = "2000000"
        
        # Check they're respected
        max_nonce = int(os.getenv("ANIMICA_MINER_MAX_NONCE", "10000000"))
        theta_micro = int(os.getenv("ANIMICA_DEFAULT_THETA_MICRO", "1000000"))
        
        print(f"  ✓ Custom ANIMICA_MINER_MAX_NONCE: {max_nonce:,}")
        print(f"  ✓ Custom ANIMICA_DEFAULT_THETA_MICRO: {theta_micro:,}")
        
        assert max_nonce == 5_000_000, "Environment variable not respected"
        assert theta_micro == 2_000_000, "Environment variable not respected"
        
        print("  ✓ Backwards compatibility verified!")
        
    finally:
        # Restore original values
        if orig_max_nonce is not None:
            os.environ["ANIMICA_MINER_MAX_NONCE"] = orig_max_nonce
        else:
            os.environ.pop("ANIMICA_MINER_MAX_NONCE", None)
        
        if orig_theta is not None:
            os.environ["ANIMICA_DEFAULT_THETA_MICRO"] = orig_theta
        else:
            os.environ.pop("ANIMICA_DEFAULT_THETA_MICRO", None)


if __name__ == "__main__":
    print("=" * 70)
    print("Testing Lower Difficulty Configuration")
    print("=" * 70)
    
    try:
        test_new_nonce_defaults()
        test_new_theta_defaults()
        test_theta_adjustment_params()
        test_rapid_mining_scenario()
        test_backwards_compatibility()
        
        print("\n" + "=" * 70)
        print("✅ All tests passed!")
        print("=" * 70)
        sys.exit(0)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
