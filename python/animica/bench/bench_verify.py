#!/usr/bin/env python3
"""
Benchmark signature verification performance.

This CLI tool measures:
- Single transaction verification rate
- Batch verification scaling (1, 10, 100, 1000 tx)
- Block validation time vs transaction count

Usage:
    python -m animica.bench.bench_verify
    python -m animica.bench.bench_verify --single
    python -m animica.bench.bench_verify --batch
    python -m animica.bench.bench_verify --block
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from typing import List, Tuple

# Check PQ availability
try:
    from animica.pq import sig_keygen, sig_sign, sig_verify
except ImportError as e:
    sig_keygen = sig_sign = sig_verify = None  # type: ignore
    _pq_import_error = str(e)
    PQ_AVAILABLE = False
else:
    _pq_import_error = None
    PQ_AVAILABLE = True

# Check batch_verify availability
try:
    from animica.security.batch_verify import VerifyItem, verify_batch
except ImportError as e:
    if PQ_AVAILABLE:
        # PQ is available but batch_verify is not - this is an error
        raise ImportError(f"batch_verify module not available: {e}") from e


def time_function(func, *args, **kwargs) -> float:
    """Time a function call and return elapsed time in seconds."""
    start = time.perf_counter()
    func(*args, **kwargs)
    end = time.perf_counter()
    return end - start


def generate_test_data(count: int) -> Tuple[bytes, List[bytes], List[bytes]]:
    """
    Generate test signing data.
    
    Returns:
        (public_key, messages, signatures)
    """
    if not PQ_AVAILABLE:
        raise RuntimeError("PQ backend not available")
    
    print(f"Generating {count} test signatures...", end="", flush=True)
    pk, sk = sig_keygen()
    messages = [f"tx_message_{i}".encode() for i in range(count)]
    signatures = [sig_sign(sk, msg) for msg in messages]
    print(" done")
    return pk, messages, signatures


def bench_single_verify(iterations: int = 100) -> None:
    """Benchmark single signature verification."""
    print(f"\n=== Single Signature Verification (n={iterations}) ===")
    
    pk, messages, signatures = generate_test_data(iterations)
    
    # Warmup
    for _ in range(min(5, iterations)):
        sig_verify(pk, messages[0], signatures[0])
    
    # Benchmark
    times = []
    for i in range(iterations):
        elapsed = time_function(sig_verify, pk, messages[i], signatures[i])
        times.append(elapsed)
    
    # Stats
    total_time = sum(times)
    mean_time = statistics.mean(times)
    median_time = statistics.median(times)
    stdev_time = statistics.stdev(times) if len(times) > 1 else 0
    rate = 1.0 / mean_time if mean_time > 0 else 0
    
    print(f"Total time:    {total_time:.3f}s")
    print(f"Mean time:     {mean_time*1000:.2f}ms")
    print(f"Median time:   {median_time*1000:.2f}ms")
    print(f"Stdev:         {stdev_time*1000:.2f}ms")
    print(f"Rate:          {rate:.1f} verifications/sec")


def bench_batch_verify(batch_sizes: List[int] = None, workers: int = None) -> None:
    """Benchmark batch verification with different batch sizes."""
    if batch_sizes is None:
        batch_sizes = [1, 10, 100, 1000]
    
    print(f"\n=== Batch Verification Scaling (workers={workers or 'auto'}) ===")
    
    for size in batch_sizes:
        pk, messages, signatures = generate_test_data(size)
        
        # Create verify items
        items = [
            VerifyItem(
                index=i,
                message=messages[i],
                signature=signatures[i],
                public_key=pk,
                alg_id=0x2002,  # ML-DSA-65
            )
            for i in range(size)
        ]
        
        # Warmup
        if size <= 10:
            verify_batch(items[:min(2, size)], workers=workers)
        
        # Benchmark
        elapsed = time_function(verify_batch, items, workers=workers)
        rate = size / elapsed if elapsed > 0 else 0
        per_sig = elapsed / size if size > 0 else 0
        
        print(f"Batch size {size:4d}: {elapsed:6.3f}s total, "
              f"{per_sig*1000:6.2f}ms/sig, {rate:7.1f} sigs/sec")


def bench_block_validation(tx_counts: List[int] = None, workers: int = None) -> None:
    """Benchmark block validation time vs transaction count."""
    if tx_counts is None:
        tx_counts = [10, 50, 100, 500, 1000]
    
    print(f"\n=== Block Validation Time (workers={workers or 'auto'}) ===")
    
    for count in tx_counts:
        pk, messages, signatures = generate_test_data(count)
        
        # Simulate block validation: verify all signatures
        items = [
            VerifyItem(
                index=i,
                message=messages[i],
                signature=signatures[i],
                public_key=pk,
                alg_id=0x2002,
            )
            for i in range(count)
        ]
        
        # Benchmark (3 runs, take median)
        times = []
        for _ in range(3):
            elapsed = time_function(verify_batch, items, workers=workers)
            times.append(elapsed)
        
        median_time = statistics.median(times)
        rate = count / median_time if median_time > 0 else 0
        
        print(f"Block with {count:4d} tx: {median_time:6.3f}s validation, "
              f"{rate:7.1f} tx/sec throughput")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Benchmark Animica signature verification performance"
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="Run single verification benchmark"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Run batch verification benchmark"
    )
    parser.add_argument(
        "--block",
        action="store_true",
        help="Run block validation benchmark"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes (default: auto)"
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Iterations for single verification (default: 100)"
    )
    
    args = parser.parse_args()
    
    # Check PQ availability
    if not PQ_AVAILABLE:
        print("ERROR: PQ backend not available. Cannot run benchmarks.")
        print("Make sure animica.pq module is installed and configured.")
        sys.exit(1)
    
    # If no specific benchmark selected, run all
    run_all = not (args.single or args.batch or args.block)
    
    if run_all or args.single:
        bench_single_verify(args.iterations)
    
    if run_all or args.batch:
        bench_batch_verify(workers=args.workers)
    
    if run_all or args.block:
        bench_block_validation(workers=args.workers)
    
    print("\n=== Benchmark Complete ===\n")


if __name__ == "__main__":
    main()
