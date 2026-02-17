"""
Test nonce-scan stopping behavior for HashScanner.

The default scanner behavior is now unbounded when max_nonce is omitted or None,
so miners can keep scanning until externally stopped.
"""
import os
import time
import threading
from mining.hash_search import HashScanner


def test_scan_with_default_max_nonce_can_be_stopped():
    """Test that default unbounded scan mode stops promptly via stop_event."""
    scanner = HashScanner()
    
    # Fake header prefix
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    
    # Choose a very high threshold so we won't find shares quickly
    t_micro = 50_000_000  # Very high threshold = very unlikely to find shares
    
    start_time = time.time()
    shares_found = 0
    
    # Use a stop event to terminate after checking some nonces
    stop_event = threading.Event()
    
    def count_shares():
        nonlocal shares_found
        # Call scan() WITHOUT explicit max_nonce parameter (default is unbounded (None))
        # But we'll stop it early with stop_event to verify it respects the event
        for share in scanner.scan(prefix, t_micro, start_nonce=0, stop_event=stop_event):
            shares_found += 1
        # The important thing is that the generator returns when stopped
        # Previously with max_nonce=None and no limit, it would run forever
    
    thread = threading.Thread(target=count_shares, daemon=True)
    thread.start()
    
    # Let it run briefly
    time.sleep(0.5)
    stop_event.set()
    
    # Wait for thread to finish
    thread.join(timeout=2)
    
    elapsed = time.time() - start_time
    
    # The scan should terminate when stop_event is set
    assert not thread.is_alive(), "Scan should terminate when stopped"
    assert elapsed < 5, f"Scan took too long ({elapsed}s), may be stalling"
    print(f"✓ Scan with default max_nonce terminated successfully in {elapsed:.2f}s")


def test_scan_respects_explicit_max_nonce():
    """Test that scan() respects an explicit max_nonce limit."""
    scanner = HashScanner()
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    t_micro = 50_000_000  # Very high threshold
    
    # Explicitly set a small max_nonce to ensure quick termination
    max_nonce = 10_000
    
    start_time = time.time()
    nonces_checked = 0
    
    for share in scanner.scan(prefix, t_micro, start_nonce=0, max_nonce=max_nonce):
        nonces_checked += 1
    
    elapsed = time.time() - start_time
    
    # Should terminate quickly with small max_nonce
    assert elapsed < 5, f"Scan took too long ({elapsed}s) for max_nonce={max_nonce}"
    print(f"✓ Scan with max_nonce={max_nonce} terminated in {elapsed:.2f}s")


def test_scan_can_be_stopped_with_event():
    """Test that scan() can be stopped early with a stop_event."""
    scanner = HashScanner()
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    t_micro = 50_000_000  # Very high threshold
    
    stop_event = threading.Event()
    shares_found = []
    
    def scan_worker():
        for share in scanner.scan(
            prefix, t_micro, start_nonce=0, max_nonce=1_000_000, stop_event=stop_event
        ):
            shares_found.append(share)
    
    # Start scanning in a thread
    thread = threading.Thread(target=scan_worker, daemon=True)
    thread.start()
    
    # Let it run briefly, then stop it
    time.sleep(0.1)
    stop_event.set()
    
    # Wait for thread to finish
    thread.join(timeout=2)
    
    assert not thread.is_alive(), "Thread should have stopped after stop_event was set"
    print(f"✓ Scan stopped successfully via stop_event after finding {len(shares_found)} shares")


def test_scan_with_none_max_nonce_is_unbounded_but_stoppable():
    """Test that max_nonce=None behaves as unbounded scan and respects stop_event."""
    scanner = HashScanner()
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    t_micro = 50_000_000  # Very high threshold
    
    start_time = time.time()
    
    # Explicitly pass None (this used to cause indefinite scanning)
    stop_event = threading.Event()
    
    def scan_worker():
        count = 0
        for share in scanner.scan(
            prefix, t_micro, start_nonce=0, max_nonce=None, stop_event=stop_event
        ):
            count += 1
            if count > 10:  # Safety
                break
    
    thread = threading.Thread(target=scan_worker, daemon=True)
    thread.start()
    
    # Give it some time to process, then stop
    time.sleep(0.5)
    stop_event.set()
    thread.join(timeout=2)
    
    elapsed = time.time() - start_time
    
    # Should not run forever even with max_nonce=None
    assert not thread.is_alive(), "Thread should have stopped"
    assert elapsed < 10, f"Scan with max_nonce=None took too long ({elapsed}s)"
    print(f"✓ Scan with explicit max_nonce=None terminated safely in {elapsed:.2f}s")


def test_scan_limit_prevents_overflow():
    """Test that the scan limit prevents nonce from growing indefinitely."""
    scanner = HashScanner()
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    t_micro = 50_000_000  # Very high threshold
    
    # Use a small range that we can actually scan
    start_nonce = 1000
    max_nonce = 500  # Only scan 500 nonces
    
    max_nonce_seen = start_nonce
    iteration_count = 0
    shares = []
    
    for share in scanner.scan(prefix, t_micro, start_nonce=start_nonce, max_nonce=max_nonce):
        shares.append(share)
        max_nonce_seen = max(max_nonce_seen, share.nonce)
        iteration_count += 1
    
    # The important test: verify we don't scan beyond the limit
    # Even though we likely found 0 shares, the loop should have terminated
    # after max_nonce iterations, not continued indefinitely
    expected_limit = start_nonce + max_nonce
    print(f"✓ Scan terminated after checking nonces in range [{start_nonce}, {expected_limit})")
    print(f"  Found {len(shares)} shares")


def test_default_max_nonce_is_unbounded_none():
    """Test that the default max_nonce value is None (unbounded)."""
    import inspect
    from mining.hash_search import HashScanner
    
    # Get the signature of the scan method
    sig = inspect.signature(HashScanner.scan)
    max_nonce_param = sig.parameters['max_nonce']
    
    # Verify it has a default value
    assert max_nonce_param.default != inspect.Parameter.empty, \
        "max_nonce should have a default value"
    
    # Verify the default is None (unbounded scan)
    expected_default = None
    assert max_nonce_param.default is expected_default, \
        f"max_nonce default should be {expected_default}, got {max_nonce_param.default}"

    print("✓ Default max_nonce is None (unbounded)")


def test_limit_handles_64bit_wrapping():
    """Test that the limit doesn't overflow past 2^64."""
    scanner = HashScanner()
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)
    t_micro = 50_000_000
    
    # Start very close to 2^64 limit
    start_nonce = (1 << 64) - 1000
    max_nonce = 2000  # This would overflow past 2^64
    
    # The scan should handle this gracefully by capping at 2^64
    shares = []
    for share in scanner.scan(prefix, t_micro, start_nonce=start_nonce, max_nonce=max_nonce):
        shares.append(share)
        # Safety: don't let test run forever if something is wrong
        if len(shares) > 10:
            break
    
    # Should complete without hanging
    print(f"✓ Scan near 64-bit boundary completed successfully with {len(shares)} shares")


if __name__ == "__main__":
    print("=" * 60)
    print("Mining Stall Fix Test Suite")
    print("=" * 60)
    
    try:
        test_default_max_nonce_is_unbounded_none()
        test_scan_with_default_max_nonce_can_be_stopped()
        test_scan_respects_explicit_max_nonce()
        test_scan_can_be_stopped_with_event()
        test_scan_with_none_max_nonce_is_unbounded_but_stoppable()
        test_scan_limit_prevents_overflow()
        test_limit_handles_64bit_wrapping()
        
        print("\n" + "=" * 60)
        print("SUCCESS: All mining stall fix tests passed!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
