# -*- coding: utf-8 -*-
"""
Mining Pool example contract using hash_work stdlib.

Demonstrates integration with hash-based useful work:
- Posts hash jobs with varying difficulty
- Tracks worker contributions
- Distributes rewards based on work completed
"""

from stdlib import abi, events, hash, storage
from stdlib import hash_work

# State key prefixes
_J_PREFIX = b"j:"  # job tracking
_W_PREFIX = b"w:"  # worker shares
_R_PREFIX = b"r:"  # rewards pool
_D_PREFIX = b"d:"  # current difficulty

# Constants
_INITIAL_DIFFICULTY = 16  # target bits


def _key(prefix: bytes, id_bytes: bytes) -> bytes:
    """Build storage key."""
    return prefix + id_bytes


def _sha3_32(b: bytes) -> bytes:
    """Return 32-byte SHA3-256 digest."""
    return hash.sha3_256(b)


# --- Initialization ---


def init(initial_difficulty: int) -> bool:
    """
    Initialize the mining pool.

    Args:
        initial_difficulty: Initial target difficulty (bits)

    Returns:
        True if successfully initialized
    """
    if initial_difficulty <= 0 or initial_difficulty > 256:
        abi.revert(b"difficulty_invalid")

    # Check not already initialized
    existing = storage.get(_D_PREFIX)
    if existing is not None:
        abi.revert(b"already_initialized")

    # Set initial difficulty
    storage.set(_D_PREFIX, initial_difficulty.to_bytes(4, "big"))
    storage.set(_R_PREFIX, (0).to_bytes(8, "big"))  # initial rewards pool

    events.emit(
        b"PoolInitialized",
        {
            b"difficulty": initial_difficulty,
        },
    )

    return True


# --- Job management ---


def create_sha256_job(input_data: bytes) -> bytes:
    """
    Create a SHA-256 hash job.

    Args:
        input_data: Input data to hash (will be committed)

    Returns:
        job_id: Job descriptor as bytes (can be used off-chain)
    """
    if len(input_data) == 0 or len(input_data) > 4096:
        abi.revert(b"input_data_invalid")

    # Get current difficulty
    diff_bytes = storage.get(_D_PREFIX)
    if diff_bytes is None:
        abi.revert(b"pool_not_initialized")
    difficulty = int.from_bytes(diff_bytes, "big")

    # Compute commitment
    commitment = hash_work.compute_commitment(input_data)

    # Create job descriptor
    job_desc = hash_work.make_hash_job_sha256(
        input_commitment=commitment,
        target_bits=difficulty,
        max_iterations=2**32 - 1,  # Allow full range
    )

    # Generate job_id from descriptor
    job_bytes = (
        job_desc["algorithm"].encode("utf-8")
        + job_desc["input_commitment"]
        + job_desc["target_bits"].to_bytes(4, "big")
    )
    job_id = _sha3_32(job_bytes)

    # Store job
    storage.set(_key(_J_PREFIX, job_id), commitment)

    # Emit event
    events.emit(
        b"HashJobCreated",
        {
            b"job_id": job_id,
            b"algorithm": b"SHA256",
            b"commitment": commitment,
            b"difficulty": difficulty,
        },
    )

    return job_id


def submit_work(
    job_id: bytes, output_hash: bytes, nonce: bytes, iterations: int
) -> bool:
    """
    Submit completed hash work.

    Args:
        job_id: Job identifier
        output_hash: Computed hash result
        nonce: Solution nonce
        iterations: Iterations performed

    Returns:
        True if work is valid and accepted
    """
    if len(job_id) != 32:
        abi.revert(b"job_id_invalid")
    if len(output_hash) != 32:
        abi.revert(b"output_hash_invalid")
    if iterations <= 0:
        abi.revert(b"iterations_invalid")

    # Check job exists
    commitment = storage.get(_key(_J_PREFIX, job_id))
    if commitment is None:
        abi.revert(b"job_not_found")

    # Get difficulty
    diff_bytes = storage.get(_D_PREFIX)
    difficulty = int.from_bytes(diff_bytes, "big") if diff_bytes else _INITIAL_DIFFICULTY

    # Build job descriptor for verification
    job_desc = {
        "algorithm": "SHA256",
        "input_commitment": commitment,
        "target_bits": difficulty,
        "max_iterations": 2**32 - 1,
    }

    # Build result descriptor
    result_desc = {
        "algorithm": "SHA256",
        "output_hash": output_hash,
        "nonce": nonce,
        "iterations": iterations,
    }

    # Verify result
    if not hash_work.verify_hash_result(job_desc, result_desc):
        abi.revert(b"invalid_proof")

    # Calculate share value based on difficulty and iterations
    # Simple formula: share = difficulty * log(iterations)
    # In practice, use more sophisticated reward calculations
    share_value = difficulty * (iterations // 1000)  # Simplified

    # Track worker share (caller address would be available via syscall)
    # For this example, just emit event
    events.emit(
        b"WorkSubmitted",
        {
            b"job_id": job_id,
            b"output_hash": output_hash,
            b"iterations": iterations,
            b"share_value": share_value,
        },
    )

    return True


# --- Pool management ---


def adjust_difficulty(new_difficulty: int) -> bool:
    """
    Adjust mining difficulty (admin function in production).

    Args:
        new_difficulty: New target difficulty bits

    Returns:
        True if successfully adjusted
    """
    if new_difficulty <= 0 or new_difficulty > 256:
        abi.revert(b"difficulty_invalid")

    # Get current difficulty
    diff_bytes = storage.get(_D_PREFIX)
    if diff_bytes is None:
        abi.revert(b"pool_not_initialized")

    old_difficulty = int.from_bytes(diff_bytes, "big")

    # Update difficulty
    storage.set(_D_PREFIX, new_difficulty.to_bytes(4, "big"))

    events.emit(
        b"DifficultyAdjusted",
        {
            b"old_difficulty": old_difficulty,
            b"new_difficulty": new_difficulty,
        },
    )

    return True


def get_difficulty() -> int:
    """
    Get current pool difficulty.

    Returns:
        Current difficulty in target bits
    """
    diff_bytes = storage.get(_D_PREFIX)
    if diff_bytes is None:
        return _INITIAL_DIFFICULTY
    return int.from_bytes(diff_bytes, "big")


def get_job_commitment(job_id: bytes) -> bytes:
    """
    Get the input commitment for a job.

    Args:
        job_id: Job identifier

    Returns:
        32-byte commitment or empty if not found
    """
    if len(job_id) != 32:
        return b""

    commitment = storage.get(_key(_J_PREFIX, job_id))
    return commitment if commitment is not None else b""
