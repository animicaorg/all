"""
Batch signature verification for Animica.

This module provides parallel batch verification of post-quantum signatures
to improve throughput in hot paths (mempool admission, block validation).

Key features:
- Parallel verification using multiprocessing (forkserver or spawn)
- Configurable worker pool size via ANIMICA_VERIFY_WORKERS env var
- Deterministic ordering of results
- Stable error handling with normalized messages
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VerifyItem:
    """Single signature verification item."""
    
    index: int  # Original position in batch
    message: bytes
    signature: bytes
    public_key: bytes
    alg_id: int
    domain: bytes = b""


@dataclass
class VerifyResult:
    """Result of verification for a single item."""
    
    index: int
    valid: bool
    error: Optional[str] = None


def _get_worker_count() -> int:
    """
    Get number of worker processes from environment.
    
    Returns:
        Worker count from ANIMICA_VERIFY_WORKERS env var,
        or max(1, cpu_count()-1) if not set
    """
    env_val = os.environ.get("ANIMICA_VERIFY_WORKERS")
    if env_val:
        try:
            count = int(env_val)
            if count > 0:
                return count
        except ValueError:
            pass
    
    # Default: leave one CPU for other work
    cpu_count = os.cpu_count() or 1
    return max(1, cpu_count - 1)


def _verify_worker(item: VerifyItem) -> VerifyResult:
    """
    Worker function to verify a single signature.
    
    This runs in a separate process and must import dependencies fresh.
    """
    try:
        # Import in worker to avoid pickling issues
        # Try standard import first
        try:
            from animica.pq import sig_verify
        except ImportError:
            # If that fails, PQ module is not available
            return VerifyResult(
                index=item.index,
                valid=False,
                error="pq module not available"
            )
        
        # Verify the signature
        valid = sig_verify(item.public_key, item.message, item.signature)
        return VerifyResult(index=item.index, valid=valid)
    
    except Exception as e:
        # Log error but return structured result
        return VerifyResult(
            index=item.index,
            valid=False,
            error=f"verification error: {type(e).__name__}"
        )


def verify_batch(
    items: List[VerifyItem],
    workers: Optional[int] = None,
    timeout: Optional[float] = None
) -> List[VerifyResult]:
    """
    Verify a batch of signatures in parallel.
    
    Args:
        items: List of VerifyItem to verify
        workers: Number of worker processes (None = auto-detect)
        timeout: Timeout in seconds for entire batch (None = no timeout)
    
    Returns:
        List of VerifyResult in same order as input items
        
    Raises:
        TimeoutError: If batch verification exceeds timeout
        RuntimeError: If worker pool initialization fails
    
    Examples:
        >>> items = [
        ...     VerifyItem(0, b"msg1", sig1, pk1, alg_id),
        ...     VerifyItem(1, b"msg2", sig2, pk2, alg_id),
        ... ]
        >>> results = verify_batch(items)
        >>> all(r.valid for r in results)
        True
    """
    if not items:
        return []
    
    # Single item: verify directly (no multiprocessing overhead)
    if len(items) == 1:
        return [_verify_worker(items[0])]
    
    worker_count = workers if workers is not None else _get_worker_count()
    worker_count = min(worker_count, len(items))  # Don't spawn more workers than items
    
    try:
        # Prefer forkserver for better isolation, fallback to spawn
        ctx_method = _get_start_method()
        ctx = multiprocessing.get_context(ctx_method)
        
        with ctx.Pool(processes=worker_count) as pool:
            # Map items to workers (preserves order)
            async_result = pool.map_async(_verify_worker, items)
            
            # Wait for results with optional timeout
            results = async_result.get(timeout=timeout)
            
            # Sort by original index to ensure deterministic ordering
            results.sort(key=lambda r: r.index)
            return results
    
    except multiprocessing.TimeoutError as e:
        raise TimeoutError(f"Batch verification timed out after {timeout}s") from e
    except Exception as e:
        logger.error("Batch verification failed: %s", e)
        raise RuntimeError(f"Batch verification failed: {type(e).__name__}")


def _get_start_method() -> str:
    """
    Get multiprocessing start method.
    
    Prefers 'forkserver' for better isolation, falls back to 'spawn' on Windows.
    """
    if os.name == "nt":  # Windows
        return "spawn"
    
    # Unix-like: prefer forkserver for safety
    # (fork can be unsafe with threads/async)
    try:
        available = multiprocessing.get_all_start_methods()
        if "forkserver" in available:
            return "forkserver"
    except Exception:
        pass
    
    return "spawn"


def verify_batch_sequential(items: List[VerifyItem]) -> List[VerifyResult]:
    """
    Verify a batch of signatures sequentially (no parallelism).
    
    Useful for testing or when multiprocessing overhead is not desired.
    
    Args:
        items: List of VerifyItem to verify
    
    Returns:
        List of VerifyResult in same order as input items
    """
    return [_verify_worker(item) for item in items]


__all__ = [
    "VerifyItem",
    "VerifyResult",
    "verify_batch",
    "verify_batch_sequential",
]
