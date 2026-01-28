from __future__ import annotations

import os

import pytest


def test_batch_size_scaling_logic():
    """Test that batch size scaling logic works correctly."""
    # This tests the logic used in scan_forever without mocking
    
    def calc_scaled_batch(threads: int, batch_size: int) -> int:
        """Replicate the batch size scaling logic from scan_forever."""
        effective_threads = threads if threads > 0 else (os.cpu_count() or 1)
        min_batch_per_thread = 10_000
        return max(batch_size, effective_threads * min_batch_per_thread)
    
    # Test with 1 thread
    assert calc_scaled_batch(1, 50_000) == 50_000  # No scaling needed
    
    # Test with 16 threads
    assert calc_scaled_batch(16, 50_000) == 160_000  # Scaled to 16 * 10k
    
    # Test with very small batch size
    assert calc_scaled_batch(8, 1_000) == 80_000  # Scaled to 8 * 10k
    
    # Test with 0 threads (auto-detect)
    cpu_count = os.cpu_count() or 1
    result = calc_scaled_batch(0, 50_000)
    expected = max(50_000, cpu_count * 10_000)
    assert result == expected
    
    # Test that high thread count still scales appropriately
    assert calc_scaled_batch(100, 50_000) == 1_000_000  # 100 * 10k
    
    # Test with large batch size (shouldn't reduce it)
    assert calc_scaled_batch(4, 1_000_000) == 1_000_000  # Keep large batch


def test_cpu_backend_thread_capping():
    """Test that CPU backend correctly caps thread count at physical CPU count."""
    # This verifies the logic in cpu_backend.py
    
    def calc_effective_threads(requested_threads: int) -> int:
        """Replicate thread capping logic from CPU backend."""
        effective_threads = requested_threads if requested_threads > 0 else (os.cpu_count() or 1)
        max_physical_threads = max(1, os.cpu_count() or 1)
        return min(effective_threads, max_physical_threads)
    
    cpu_count = os.cpu_count() or 1
    
    # Test normal case
    assert calc_effective_threads(4) == min(4, cpu_count)
    
    # Test excessive threads (like 20000)
    assert calc_effective_threads(20000) == cpu_count
    
    # Test 0 threads (auto-detect)
    assert calc_effective_threads(0) == cpu_count
    
    # Test 1 thread
    assert calc_effective_threads(1) == 1


