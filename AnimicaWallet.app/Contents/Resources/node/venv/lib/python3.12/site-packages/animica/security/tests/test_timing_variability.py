"""
Timing variability tests for constant-time operations.

These tests check that constant-time comparison functions don't have
obvious timing side-channels. They use simple statistical bounds on
median timing ratios across different input classes.

NOTE: These tests are PROBABILISTIC and may be FLAKY due to:
- OS scheduler interference
- CPU frequency scaling
- Background processes
- Python interpreter variability
- Garbage collection

They are disabled by default and only run when:
    ANIMICA_TIMING_TESTS=1

Usage:
    ANIMICA_TIMING_TESTS=1 pytest python/animica/security/tests/test_timing_variability.py -v
"""

import os
import statistics
import time

import pytest

from animica.security.ct import ct_eq_bytes, ct_eq_str

# Skip all tests unless explicitly enabled
pytestmark = pytest.mark.skipif(
    os.environ.get("ANIMICA_TIMING_TESTS") != "1",
    reason="Timing tests disabled (set ANIMICA_TIMING_TESTS=1 to enable)"
)


def measure_timing(func, *args, iterations: int = 1000):
    """
    Measure timing of a function over many iterations.
    
    Returns median time in seconds to reduce noise from outliers.
    """
    times = []
    
    # Warmup
    for _ in range(min(10, iterations // 10)):
        func(*args)
    
    # Measure
    for _ in range(iterations):
        start = time.perf_counter()
        func(*args)
        end = time.perf_counter()
        times.append(end - start)
    
    return statistics.median(times)


class TestTimingVariabilityBytes:
    """Test timing variability of ct_eq_bytes()."""

    def test_equal_vs_unequal_timing(self):
        """
        Test that equal and unequal comparisons have similar timing.
        
        This is a weak test since Python timing is highly variable.
        We allow up to 2x difference in median time.
        """
        # Use 32-byte values (typical for hashes/keys)
        value1 = b"a" * 32
        value2 = b"a" * 32
        value3 = b"b" * 32
        
        # Measure equal comparison
        time_equal = measure_timing(ct_eq_bytes, value1, value2, iterations=1000)
        
        # Measure unequal comparison
        time_unequal = measure_timing(ct_eq_bytes, value1, value3, iterations=1000)
        
        # Calculate ratio
        ratio = max(time_equal, time_unequal) / min(time_equal, time_unequal)
        
        # Allow up to 2x difference (Python is noisy)
        assert ratio < 2.0, f"Timing ratio {ratio:.2f} exceeds threshold"

    def test_early_vs_late_mismatch_timing(self):
        """
        Test that mismatch position doesn't affect timing.
        
        Constant-time comparison should take same time regardless of
        where bytes differ.
        """
        base = b"a" * 32
        
        # Mismatch at position 0
        early = b"b" + b"a" * 31
        
        # Mismatch at position 31
        late = b"a" * 31 + b"b"
        
        time_early = measure_timing(ct_eq_bytes, base, early, iterations=1000)
        time_late = measure_timing(ct_eq_bytes, base, late, iterations=1000)
        
        ratio = max(time_early, time_late) / min(time_early, time_late)
        
        # Allow up to 2x difference
        assert ratio < 2.0, f"Timing ratio {ratio:.2f} exceeds threshold"

    def test_different_lengths(self):
        """
        Test that different lengths fail fast (OK for timing).
        
        Length is NOT secret in our threat model, so early length check is OK.
        """
        short = b"a" * 16
        long = b"a" * 32
        
        time_length_check = measure_timing(ct_eq_bytes, short, long, iterations=1000)
        
        # Just verify it doesn't crash
        assert time_length_check > 0


class TestTimingVariabilityStr:
    """Test timing variability of ct_eq_str()."""

    def test_equal_vs_unequal_timing(self):
        """Test that equal and unequal string comparisons have similar timing."""
        str1 = "password123456789012345678901234"
        str2 = "password123456789012345678901234"
        str3 = "wrongpas123456789012345678901234"
        
        time_equal = measure_timing(ct_eq_str, str1, str2, iterations=1000)
        time_unequal = measure_timing(ct_eq_str, str1, str3, iterations=1000)
        
        ratio = max(time_equal, time_unequal) / min(time_equal, time_unequal)
        
        assert ratio < 2.0, f"Timing ratio {ratio:.2f} exceeds threshold"

    def test_unicode_timing(self):
        """Test timing with Unicode strings."""
        ascii_str = "a" * 32
        unicode_str = "世" * 10  # About 30 bytes in UTF-8
        
        # Both should complete without error
        time_ascii = measure_timing(ct_eq_str, ascii_str, ascii_str, iterations=1000)
        time_unicode = measure_timing(ct_eq_str, unicode_str, unicode_str, iterations=1000)
        
        # Just verify both work
        assert time_ascii > 0
        assert time_unicode > 0


class TestTimingNotes:
    """Document timing test limitations."""

    def test_timing_limitations_documented(self):
        """
        Verify that timing test limitations are documented.
        
        This is a documentation test that always passes but serves as
        a reminder of the limitations:
        
        1. Python is NOT constant-time at the language level
        2. CPython interpreter adds significant variability
        3. OS scheduler can preempt at any time
        4. CPU frequency scaling affects measurements
        5. Garbage collection pauses are unpredictable
        6. These tests can only catch OBVIOUS timing leaks
        
        For true constant-time code, use C/Rust/assembly with:
        - Constant-time primitives (e.g., libsodium)
        - Memory locking (prevent swapping)
        - Cache timing mitigations
        - Hardware timing randomization
        """
        # Always passes - just documents limitations
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
