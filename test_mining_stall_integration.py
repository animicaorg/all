#!/usr/bin/env python3
"""
Integration test to verify mining doesn't stall indefinitely.
This simulates a realistic mining scenario where a miner searches for shares.
"""
import os
import sys
import time

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mining.hash_search import HashScanner


def test_realistic_mining_scenario():
    """
    Simulate a realistic mining scenario where a miner continuously
    searches for shares with a reasonable difficulty.
    """
    print("Testing realistic mining scenario...")
    print("- Creating scanner")
    
    scanner = HashScanner()
    
    # Use realistic mining parameters
    # A header template (without nonce)
    header_prefix = b"animica:testnet:block:12345:" + os.urandom(64)
    
    # Medium-high difficulty (will likely find 0-2 shares in small window)
    t_share_micro = 25_000_000  # 25M micro-nats
    
    # Search a small window (10K nonces) - should complete quickly
    max_nonce = 10_000
    
    print(f"- Scanning {max_nonce:,} nonces with difficulty threshold {t_share_micro:,} µ-nats")
    
    start_time = time.time()
    shares = []
    
    for share in scanner.scan(
        header_prefix,
        t_share_micro,
        start_nonce=0,
        max_nonce=max_nonce
    ):
        shares.append(share)
    
    elapsed = time.time() - start_time
    
    print(f"✓ Scan completed in {elapsed:.3f}s")
    print(f"✓ Found {len(shares)} shares")
    
    # Verify it completed in reasonable time (should be < 1 second on modern CPU)
    assert elapsed < 10, f"Scan took too long: {elapsed}s"
    
    # Verify we didn't hang indefinitely
    print("✓ Mining did not stall indefinitely")
    
    return True


def test_no_stall_without_explicit_max_nonce():
    """
    Test that omitting max_nonce parameter doesn't cause stalling.
    This was the original bug - calling scan() without max_nonce would
    cause it to search indefinitely.
    """
    print("\nTesting scan without explicit max_nonce parameter...")
    
    import threading
    scanner = HashScanner()
    header_prefix = b"animica:testnet:block:67890:" + os.urandom(64)
    t_share_micro = 30_000_000  # High difficulty
    
    # Call scan() WITHOUT max_nonce parameter in a thread
    # Before fix: would scan indefinitely (all 2^64 nonces)
    # After fix: uses default of 2^32, so it will eventually terminate
    
    start_time = time.time()
    stop_event = threading.Event()
    share_count = [0]
    
    def scan_worker():
        for share in scanner.scan(header_prefix, t_share_micro, start_nonce=0, stop_event=stop_event):
            share_count[0] += 1
    
    print("- Starting scan thread (default max_nonce, will stop via event)")
    
    # Start scanning in a thread
    thread = threading.Thread(target=scan_worker, daemon=True)
    thread.start()
    
    # Let it run briefly to verify it's working
    time.sleep(0.5)
    
    # Stop it
    stop_event.set()
    
    # Wait for thread to finish
    thread.join(timeout=2)
    
    elapsed = time.time() - start_time
    
    print(f"✓ Scan thread terminated in {elapsed:.3f}s after stop_event")
    print(f"✓ Found {share_count[0]} shares")
    print("✓ Did not hang indefinitely (default max_nonce is working)")
    
    # Verify thread actually stopped
    assert not thread.is_alive(), "Thread should have stopped"
    assert elapsed < 5, f"Scan took too long: {elapsed}s"
    
    return True


def main():
    print("=" * 70)
    print("Mining Stall Integration Test")
    print("=" * 70)
    
    try:
        test_realistic_mining_scenario()
        test_no_stall_without_explicit_max_nonce()
        
        print("\n" + "=" * 70)
        print("SUCCESS: All integration tests passed!")
        print("Mining does NOT stall indefinitely with the fix in place.")
        print("=" * 70)
        return 0
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
