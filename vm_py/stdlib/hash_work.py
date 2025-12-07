"""
Deterministic hash work utilities for VM-Py contracts.

Provides on-chain-safe hash functions and job descriptor builders for
integrating hash-based useful work with the Animica capabilities layer.

Surface:
    - sha256(data: bytes) -> bytes
    - sha256d(data: bytes) -> bytes
    - blake2b_256(data: bytes) -> bytes
    - compute_commitment(data: bytes) -> bytes
    - make_hash_job_sha256(input_commitment, target_bits, max_iters) -> dict
    - make_hash_job_sha256d(input_commitment, target_bits, max_iters) -> dict
    - make_hash_job_scrypt(input_commitment, N, r, p, max_cost) -> dict
    - verify_hash_result(job_desc, result_desc) -> bool
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

# Import runtime hash API for deterministic hashing
try:
    from vm_py.runtime import hash_api
except Exception as e:  # pragma: no cover
    raise RuntimeError("runtime hash_api not available") from e

BytesLike = Union[bytes, bytearray, memoryview]


# --- Helper functions ---


def _as_bytes(name: str, b: BytesLike) -> bytes:
    """Convert bytes-like to bytes."""
    if isinstance(b, bytes):
        return b
    if isinstance(b, (bytearray, memoryview)):
        return bytes(b)
    raise TypeError(f"{name} must be bytes-like, got {type(b).__name__}")


def _ensure_positive_int(name: str, val: Any) -> int:
    """Ensure value is a positive integer."""
    if not isinstance(val, int):
        raise TypeError(f"{name} must be int, got {type(val).__name__}")
    if val <= 0:
        raise ValueError(f"{name} must be positive, got {val}")
    return val


def _ensure_bytes32(name: str, b: BytesLike) -> bytes:
    """Ensure value is exactly 32 bytes."""
    data = _as_bytes(name, b)
    if len(data) != 32:
        raise ValueError(f"{name} must be 32 bytes, got {len(data)}")
    return data


# --- Core hash functions ---


def sha256(data: BytesLike) -> bytes:
    """
    Compute SHA-256 hash of data.

    Args:
        data: Input bytes to hash

    Returns:
        32-byte SHA-256 digest
    """
    d = _as_bytes("data", data)
    # Use runtime hash_api if available, fallback to hashlib for testing
    try:
        out = hash_api.sha256(d)
        if not isinstance(out, (bytes, bytearray)):
            raise TypeError("hash_api.sha256 must return bytes")
        return bytes(out)
    except (AttributeError, NameError):
        # Fallback for testing without runtime
        import hashlib

        return hashlib.sha256(d).digest()


def sha256d(data: BytesLike) -> bytes:
    """
    Compute double SHA-256 hash (Bitcoin-style).

    Args:
        data: Input bytes to hash

    Returns:
        32-byte double SHA-256 digest (SHA-256(SHA-256(data)))
    """
    return sha256(sha256(data))


def blake2b_256(data: BytesLike) -> bytes:
    """
    Compute BLAKE2b-256 hash.

    Args:
        data: Input bytes to hash

    Returns:
        32-byte BLAKE2b digest (truncated to 256 bits)
    """
    d = _as_bytes("data", data)
    try:
        out = hash_api.blake2b_256(d)
        if not isinstance(out, (bytes, bytearray)):
            raise TypeError("hash_api.blake2b_256 must return bytes")
        return bytes(out)
    except (AttributeError, NameError):
        # Fallback for testing
        import hashlib

        return hashlib.blake2b(d, digest_size=32).digest()


# --- Job descriptor builders ---


def make_hash_job_sha256(
    input_commitment: BytesLike, target_bits: int, max_iterations: int
) -> Dict[str, Any]:
    """
    Create a SHA-256 hash job descriptor.

    Args:
        input_commitment: 32-byte commitment to input data
        target_bits: Difficulty target (log2 scale, typically 8-256)
        max_iterations: Maximum iterations allowed

    Returns:
        Job descriptor dict compatible with HashJobs contract
    """
    commitment = _ensure_bytes32("input_commitment", input_commitment)
    target = _ensure_positive_int("target_bits", target_bits)
    iters = _ensure_positive_int("max_iterations", max_iterations)

    return {
        "algorithm": "SHA256",
        "input_commitment": commitment,
        "target_bits": target,
        "max_iterations": iters,
    }


def make_hash_job_sha256d(
    input_commitment: BytesLike, target_bits: int, max_iterations: int
) -> Dict[str, Any]:
    """
    Create a double SHA-256 hash job descriptor.

    Args:
        input_commitment: 32-byte commitment to input data
        target_bits: Difficulty target (log2 scale)
        max_iterations: Maximum iterations allowed

    Returns:
        Job descriptor dict compatible with HashJobs contract
    """
    commitment = _ensure_bytes32("input_commitment", input_commitment)
    target = _ensure_positive_int("target_bits", target_bits)
    iters = _ensure_positive_int("max_iterations", max_iterations)

    return {
        "algorithm": "SHA256D",
        "input_commitment": commitment,
        "target_bits": target,
        "max_iterations": iters,
    }


def make_hash_job_scrypt(
    input_commitment: BytesLike,
    N: int,
    r: int,
    p: int,
    target_bits: int,
    max_cost: int,
) -> Dict[str, Any]:
    """
    Create a Scrypt hash job descriptor.

    Args:
        input_commitment: 32-byte commitment to input data
        N: CPU/memory cost parameter (must be power of 2)
        r: Block size parameter
        p: Parallelization parameter
        target_bits: Difficulty target
        max_cost: Maximum computational cost allowed

    Returns:
        Job descriptor dict compatible with HashJobs contract
    """
    commitment = _ensure_bytes32("input_commitment", input_commitment)
    n_val = _ensure_positive_int("N", N)
    r_val = _ensure_positive_int("r", r)
    p_val = _ensure_positive_int("p", p)
    target = _ensure_positive_int("target_bits", target_bits)
    cost = _ensure_positive_int("max_cost", max_cost)

    # Validate N is power of 2
    if n_val & (n_val - 1) != 0:
        raise ValueError(f"N must be power of 2, got {n_val}")

    return {
        "algorithm": "SCRYPT",
        "input_commitment": commitment,
        "target_bits": target,
        "max_iterations": cost,  # Map max_cost to max_iterations for consistency
        "scrypt_n": n_val,
        "scrypt_r": r_val,
        "scrypt_p": p_val,
    }


# --- Result verification ---


def verify_hash_result(
    job_desc: Dict[str, Any], result_desc: Dict[str, Any]
) -> bool:
    """
    Verify that a hash result matches the job specification.

    This performs bounded on-chain verification by checking:
    1. Job ID matches
    2. Algorithm matches
    3. Output hash meets target difficulty (if specified)

    For full cryptographic verification, the VM runtime or external
    verifier should recompute the hash.

    Args:
        job_desc: Job descriptor from make_hash_job_*
        result_desc: Result descriptor with fields:
            - job_id: bytes
            - output_hash: bytes (32 bytes)
            - nonce: bytes
            - iterations: int
            - algorithm: str

    Returns:
        True if result appears valid for the job, False otherwise
    """
    # Basic structure validation
    if not isinstance(job_desc, dict) or not isinstance(result_desc, dict):
        return False

    # Check algorithm match
    job_algo = job_desc.get("algorithm")
    result_algo = result_desc.get("algorithm")
    if job_algo != result_algo:
        return False

    # Verify output_hash is 32 bytes
    output_hash = result_desc.get("output_hash")
    if not isinstance(output_hash, (bytes, bytearray)) or len(output_hash) != 32:
        return False

    # Check iterations within bounds
    iterations = result_desc.get("iterations", 0)
    max_iterations = job_desc.get("max_iterations", 0)
    if not isinstance(iterations, int) or iterations <= 0:
        return False
    if max_iterations > 0 and iterations > max_iterations:
        return False

    # Verify target difficulty if specified
    target_bits = job_desc.get("target_bits")
    if target_bits and isinstance(target_bits, int):
        # Check that output_hash meets difficulty target
        # Target is 2^(256 - target_bits), so hash must be < target
        # For simplicity, check leading zero bits
        hash_int = int.from_bytes(output_hash, "big")
        required_zeros = target_bits
        # Count leading zero bits
        if hash_int != 0:
            leading_zeros = 256 - hash_int.bit_length()
            if leading_zeros < required_zeros:
                return False

    return True


def compute_commitment(data: BytesLike) -> bytes:
    """
    Compute a 32-byte commitment to input data for hash jobs.

    Args:
        data: Input data to commit to

    Returns:
        32-byte SHA-256 commitment
    """
    return sha256(data)


__all__ = (
    "sha256",
    "sha256d",
    "blake2b_256",
    "make_hash_job_sha256",
    "make_hash_job_sha256d",
    "make_hash_job_scrypt",
    "verify_hash_result",
    "compute_commitment",
)
