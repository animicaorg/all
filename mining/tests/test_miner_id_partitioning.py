"""Tests for miner_id nonce space partitioning."""

from __future__ import annotations

from mining.parallel_nonce_search import iter_stride, parallel_nonce_search


def test_miner_id_prevents_overlap():
    """Test that different miner_ids result in non-overlapping nonce spaces."""
    # Simulate 2 miners with 2 workers each
    miner0_worker0 = set(list(iter_stride(0, 10000, 0, 2, miner_id=0)))
    miner0_worker1 = set(list(iter_stride(0, 10000, 1, 2, miner_id=0)))
    miner1_worker0 = set(list(iter_stride(0, 10000, 0, 2, miner_id=1)))
    miner1_worker1 = set(list(iter_stride(0, 10000, 1, 2, miner_id=1)))
    
    # Verify no overlaps between any pair
    assert len(miner0_worker0 & miner0_worker1) == 0, "Workers within same miner overlap"
    assert len(miner0_worker0 & miner1_worker0) == 0, "Miners with different IDs overlap"
    assert len(miner0_worker0 & miner1_worker1) == 0, "Cross-miner cross-worker overlap"
    assert len(miner0_worker1 & miner1_worker0) == 0, "Cross-miner cross-worker overlap"
    assert len(miner0_worker1 & miner1_worker1) == 0, "Cross-miner cross-worker overlap"
    assert len(miner1_worker0 & miner1_worker1) == 0, "Workers within same miner overlap"


def test_miner_id_coverage():
    """Test that multiple miners with different IDs cover the nonce space efficiently."""
    # Simulate 3 miners with 2 workers each
    all_nonces = set()
    for miner_id in range(3):
        for worker_id in range(2):
            nonces = set(list(iter_stride(0, 5000, worker_id, 2, miner_id=miner_id)))
            all_nonces.update(nonces)
    
    # All nonces should be unique (no worker checked the same nonce twice)
    expected_unique = sum(
        len(list(iter_stride(0, 5000, worker_id, 2, miner_id=miner_id)))
        for miner_id in range(3)
        for worker_id in range(2)
    )
    assert len(all_nonces) == expected_unique, "Duplicate nonces found across miners"


def test_miner_id_zero_default():
    """Test that miner_id=0 works as default (single miner)."""
    # Default behavior should still work
    nonces_explicit = list(iter_stride(0, 100, 0, 2, miner_id=0))
    nonces_default = list(iter_stride(0, 100, 0, 2))
    
    # Should be identical
    assert nonces_explicit == nonces_default, "Default miner_id=0 behavior changed"


def test_miner_id_stride_consistency():
    """Test that stride is consistent across all miners."""
    # All miners should use the same stride (workers * 256)
    workers = 4
    stride = workers * 256  # Expected stride
    
    for miner_id in range(3):
        for worker_id in range(workers):
            nonces = list(iter_stride(0, stride * 3, worker_id, workers, miner_id=miner_id))
            if len(nonces) >= 2:
                actual_stride = nonces[1] - nonces[0]
                assert actual_stride == stride, f"Miner {miner_id} worker {worker_id} has incorrect stride"


def toy_check_modulo(nonce: int, target: int) -> tuple[bool, int | None]:
    """Helper function for testing parallel search."""
    return (nonce % target == 0, nonce if nonce % target == 0 else None)


def test_parallel_search_with_miner_id():
    """Test that parallel_nonce_search respects miner_id."""
    # Use a larger range so all miners can find solutions
    # Target is 17, so multiples are: 0, 17, 34, 51, 68, 85, ...
    result0 = parallel_nonce_search(
        toy_check_modulo, (17,), 0, 10000, workers=2, miner_id=0
    )
    result1 = parallel_nonce_search(
        toy_check_modulo, (17,), 0, 10000, workers=2, miner_id=1
    )
    
    # Both should find valid solutions
    assert result0 is not None, "Miner 0 failed to find solution"
    assert result1 is not None, "Miner 1 failed to find solution"
    
    # Both nonces should be valid
    assert result0.nonce % 17 == 0, "Miner 0 found invalid nonce"
    assert result1.nonce % 17 == 0, "Miner 1 found invalid nonce"
    
    # The key point: both miners can find solutions without overlapping their search


def test_miner_id_global_worker_id():
    """Test that global worker ID is calculated correctly."""
    workers = 2
    
    # Miner 0: global IDs 0, 1
    # Miner 1: global IDs 2, 3
    # Miner 2: global IDs 4, 5
    
    # Get first nonce for each worker (which equals their global_worker_id)
    for miner_id in range(3):
        for worker_id in range(workers):
            nonces = list(iter_stride(0, 1000, worker_id, workers, miner_id=miner_id))
            assert len(nonces) > 0, f"No nonces generated for miner {miner_id} worker {worker_id}"
            first_nonce = nonces[0]
            expected_global_id = miner_id * workers + worker_id
            assert first_nonce == expected_global_id, (
                f"Miner {miner_id} worker {worker_id} has incorrect first nonce "
                f"(got {first_nonce}, expected {expected_global_id})"
            )


if __name__ == "__main__":
    # Run tests directly
    test_miner_id_prevents_overlap()
    print("✓ Miner ID overlap prevention test passed")
    
    test_miner_id_coverage()
    print("✓ Miner ID coverage test passed")
    
    test_miner_id_zero_default()
    print("✓ Miner ID default behavior test passed")
    
    test_miner_id_stride_consistency()
    print("✓ Miner ID stride consistency test passed")
    
    test_parallel_search_with_miner_id()
    print("✓ Parallel search with miner ID test passed")
    
    test_miner_id_global_worker_id()
    print("✓ Global worker ID calculation test passed")
    
    print("\n✓ All miner_id tests passed!")
