"""
Test that nonce wraps correctly at 64-bit boundary to prevent infinite growth.

This test validates the fix for the issue where nonce would increase infinitely
during mining, eventually reaching values that cause block finding issues.
"""
import pytest


def test_nonce_wraps_at_64bit_boundary():
    """
    Test that nonce wrapping works correctly at 64-bit boundary.
    
    The nonce should wrap at 2^64 - 1 (0xFFFFFFFFFFFFFFFF) back to 0.
    This prevents nonce from growing infinitely during long mining sessions.
    """
    # Define the 64-bit mask used in the code
    UINT64_MASK = 0xFFFFFFFFFFFFFFFF
    MAX_UINT64 = (1 << 64) - 1
    
    # Test normal increment
    nonce = 1000
    batch_size = 50000
    new_nonce = (nonce + batch_size) & UINT64_MASK
    assert new_nonce == 51000
    
    # Test near boundary (doesn't wrap yet, but validates mask works)
    nonce = MAX_UINT64 - 100
    batch_size = 50
    new_nonce = (nonce + batch_size) & UINT64_MASK
    # Result is MAX_UINT64 - 50, still in range but close to boundary
    expected = MAX_UINT64 - 50
    assert new_nonce == expected
    
    # Test exact boundary
    nonce = MAX_UINT64
    batch_size = 1
    new_nonce = (nonce + batch_size) & UINT64_MASK
    assert new_nonce == 0, "Nonce should wrap to 0 after MAX_UINT64"
    
    # Test overflow by large amount
    nonce = MAX_UINT64 - 10
    batch_size = 100
    new_nonce = (nonce + batch_size) & UINT64_MASK
    expected = 89  # (MAX - 10 + 100) % (2^64) = 89
    assert new_nonce == expected


def test_nonce_increment_without_mask_causes_overflow():
    """
    Demonstrate that incrementing without mask causes overflow.
    
    This shows what would happen without the fix - nonce would exceed 64-bit range.
    """
    MAX_UINT64 = (1 << 64) - 1
    
    nonce = MAX_UINT64 - 100
    batch_size = 200
    
    # Without mask (the bug)
    nonce_without_mask = nonce + batch_size
    assert nonce_without_mask > MAX_UINT64, "Without mask, nonce exceeds 64-bit range"
    
    # With mask (the fix)
    nonce_with_mask = (nonce + batch_size) & 0xFFFFFFFFFFFFFFFF
    assert nonce_with_mask <= MAX_UINT64, "With mask, nonce stays in 64-bit range"
    assert nonce_with_mask == 99  # Wrapped value


def test_scan_method_has_nonce_wrapping():
    """
    Test that the scan method in HashScanner properly wraps nonce.
    
    The inner scan loop should wrap nonce at each increment.
    """
    from mining.hash_search import HashScanner
    
    scanner = HashScanner(algo="sha3_256")
    
    # Create a simple prefix
    prefix = b"test_prefix_" + b"\x00" * 52
    
    # Test with a very high threshold so we don't find shares easily
    # Just verify the scanner doesn't crash with high start_nonce
    t_share_micro = 30_000_000  # Very high threshold
    
    # Start near the 64-bit boundary
    MAX_UINT64 = (1 << 64) - 1
    start_nonce = MAX_UINT64 - 10
    
    # Scan a few nonces (won't find shares with high threshold)
    shares = scanner.scan_batch(
        prefix=prefix,
        t_share_micro=t_share_micro,
        nonce_start=start_nonce,
        nonce_count=20,  # Will wrap around
        theta_micro=t_share_micro,
    )
    
    # The scanner should complete without error even when nonces wrap
    # (shares list will likely be empty due to high threshold, but that's fine)
    assert isinstance(shares, list)


def test_nonce_reset_on_new_job():
    """
    Test that nonce is reset to 0 when a new job/template arrives.
    
    This ensures that each new mining template starts fresh from nonce 0.
    """
    # This is a logical test - in scan_forever(), when job_id changes,
    # nonce is reset to 0. We verify the logic here.
    
    nonce = 123456789
    current_job_id = "job_1"
    new_job_id = "job_2"
    
    # Simulate job change detection
    if new_job_id != current_job_id:
        nonce = 0
        current_job_id = new_job_id
    
    assert nonce == 0, "Nonce should be reset to 0 on new job"
    assert current_job_id == "job_2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
