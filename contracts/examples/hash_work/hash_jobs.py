# -*- coding: utf-8 -*-
"""
HashJobs contract - Manage hash-based useful work jobs.

This contract provides an on-chain registry for hash work jobs and results,
mirroring the pattern used by AI/Quantum external services.

Persistent state (simple KV layout):
  j:{job_id} -> job descriptor (CBOR bytes)
  r:{job_id} -> result descriptor (CBOR bytes)
  s:{job_id} -> status byte (b"0"=pending, b"1"=completed)
  c:{job_id} -> creation height (int)

Events:
  HashJobPosted(job_id, algorithm, input_commitment, target_bits)
  HashJobCompleted(job_id, output_hash, iterations, device_type)
"""

from stdlib import abi, events, hash, storage

# State key prefixes
_J_PREFIX = b"j:"  # job descriptors
_R_PREFIX = b"r:"  # result descriptors
_S_PREFIX = b"s:"  # status
_C_PREFIX = b"c:"  # creation height

# Caps
_MAX_ALGORITHM_LEN = 32
_MAX_BACKEND_ID_LEN = 64


def _key(prefix: bytes, job_id: bytes) -> bytes:
    """Build storage key."""
    return prefix + job_id


def _sha3_32(b: bytes) -> bytes:
    """Return 32-byte SHA3-256 digest."""
    return hash.sha3_256(b)


# --- Public interface ---


def post_job(
    algorithm: bytes,
    input_commitment: bytes,
    target_bits: int,
    max_iterations: int,
    scrypt_n: int = 0,
    scrypt_r: int = 0,
    scrypt_p: int = 0,
) -> bytes:
    """
    Post a hash work job.

    Args:
        algorithm: Algorithm name (e.g., b"SHA256", b"SCRYPT")
        input_commitment: 32-byte commitment to input data
        target_bits: Difficulty target (log2 scale)
        max_iterations: Maximum iterations allowed
        scrypt_n: Scrypt N parameter (0 if not scrypt)
        scrypt_r: Scrypt r parameter (0 if not scrypt)
        scrypt_p: Scrypt p parameter (0 if not scrypt)

    Returns:
        job_id: 32-byte deterministic job identifier
    """
    # Validate inputs
    if len(algorithm) == 0 or len(algorithm) > _MAX_ALGORITHM_LEN:
        abi.revert(b"algorithm_invalid")
    if len(input_commitment) != 32:
        abi.revert(b"commitment_invalid")
    if target_bits <= 0 or target_bits > 512:
        abi.revert(b"target_bits_invalid")
    if max_iterations <= 0:
        abi.revert(b"max_iterations_invalid")

    # Normalize algorithm to uppercase
    algo_str = algorithm.decode("utf-8", errors="ignore").upper()
    algo_bytes = algo_str.encode("utf-8")[:_MAX_ALGORITHM_LEN]

    # Validate scrypt params if algorithm is SCRYPT
    if algo_str == "SCRYPT":
        if scrypt_n <= 0 or scrypt_r <= 0 or scrypt_p <= 0:
            abi.revert(b"scrypt_params_invalid")
        # Check N is power of 2
        if scrypt_n & (scrypt_n - 1) != 0:
            abi.revert(b"scrypt_n_not_power_of_2")

    # Generate deterministic job_id from job parameters
    # Hash(algorithm || input_commitment || target_bits || max_iterations || scrypt_params)
    job_data = (
        algo_bytes
        + input_commitment
        + target_bits.to_bytes(4, "big")
        + max_iterations.to_bytes(8, "big")
    )
    if algo_str == "SCRYPT":
        job_data += (
            scrypt_n.to_bytes(4, "big")
            + scrypt_r.to_bytes(4, "big")
            + scrypt_p.to_bytes(4, "big")
        )

    job_id = _sha3_32(job_data)

    # Check if job already exists
    existing_status = storage.get(_key(_S_PREFIX, job_id))
    if existing_status is not None:
        abi.revert(b"job_already_exists")

    # Store job descriptor (minimal encoding)
    job_desc = (
        algo_bytes
        + input_commitment
        + target_bits.to_bytes(4, "big")
        + max_iterations.to_bytes(8, "big")
    )
    if algo_str == "SCRYPT":
        job_desc += (
            scrypt_n.to_bytes(4, "big")
            + scrypt_r.to_bytes(4, "big")
            + scrypt_p.to_bytes(4, "big")
        )

    storage.set(_key(_J_PREFIX, job_id), job_desc)
    storage.set(_key(_S_PREFIX, job_id), b"0")  # pending

    # Emit event
    events.emit(
        b"HashJobPosted",
        {
            b"job_id": job_id,
            b"algorithm": algo_bytes,
            b"input_commitment": input_commitment,
            b"target_bits": target_bits,
        },
    )

    return job_id


def mark_completed(
    job_id: bytes,
    output_hash: bytes,
    nonce: bytes,
    iterations: int,
    device_type: bytes,
    backend_id: bytes,
) -> bool:
    """
    Mark a job as completed with result metadata.

    This should typically be called by authorized workers or via a
    syscall that validates the proof. For this example, we allow
    any caller to mark completion (in production, add access control).

    Args:
        job_id: 32-byte job identifier
        output_hash: 32-byte result hash
        nonce: Solution nonce (variable length)
        iterations: Actual iterations performed
        device_type: Device type string (e.g., b"CPU", b"GPU")
        backend_id: Backend identifier

    Returns:
        True if successfully marked completed
    """
    # Validate inputs
    if len(job_id) != 32:
        abi.revert(b"job_id_invalid")
    if len(output_hash) != 32:
        abi.revert(b"output_hash_invalid")
    if iterations <= 0:
        abi.revert(b"iterations_invalid")
    if len(backend_id) > _MAX_BACKEND_ID_LEN:
        abi.revert(b"backend_id_too_long")

    # Check job exists
    job_desc = storage.get(_key(_J_PREFIX, job_id))
    if job_desc is None:
        abi.revert(b"job_not_found")

    # Check not already completed
    status = storage.get(_key(_S_PREFIX, job_id))
    if status == b"1":
        abi.revert(b"job_already_completed")

    # Store result (minimal encoding)
    result_desc = (
        output_hash
        + len(nonce).to_bytes(2, "big")
        + nonce
        + iterations.to_bytes(8, "big")
        + len(device_type).to_bytes(1, "big")
        + device_type
        + len(backend_id).to_bytes(1, "big")
        + backend_id
    )

    storage.set(_key(_R_PREFIX, job_id), result_desc)
    storage.set(_key(_S_PREFIX, job_id), b"1")  # completed

    # Emit completion event
    events.emit(
        b"HashJobCompleted",
        {
            b"job_id": job_id,
            b"output_hash": output_hash,
            b"iterations": iterations,
            b"device_type": device_type,
        },
    )

    return True


def get_job(job_id: bytes) -> tuple:
    """
    Get job descriptor.

    Args:
        job_id: 32-byte job identifier

    Returns:
        (exists: bool, algorithm: bytes, input_commitment: bytes,
         target_bits: int, max_iterations: int, status: bytes)
    """
    if len(job_id) != 32:
        return (False, b"", b"", 0, 0, b"")

    job_desc = storage.get(_key(_J_PREFIX, job_id))
    if job_desc is None:
        return (False, b"", b"", 0, 0, b"")

    # Decode job descriptor
    # Format: algorithm (up to 32) + commitment (32) + target (4) + max_iters (8) [+ scrypt params]
    # Find first null byte or end for algorithm
    algo_end = min(32, len(job_desc))
    for i in range(32):
        if i >= len(job_desc):
            break
        if job_desc[i] == 0:
            algo_end = i
            break

    algorithm = job_desc[:algo_end].rstrip(b"\x00")
    if len(job_desc) < 32 + 32 + 4 + 8:
        return (False, algorithm, b"", 0, 0, b"")

    input_commitment = job_desc[32 : 32 + 32]
    target_bits = int.from_bytes(job_desc[64:68], "big")
    max_iterations = int.from_bytes(job_desc[68:76], "big")

    status = storage.get(_key(_S_PREFIX, job_id))
    if status is None:
        status = b"0"

    return (True, algorithm, input_commitment, target_bits, max_iterations, status)


def get_result(job_id: bytes) -> tuple:
    """
    Get result for a completed job.

    Args:
        job_id: 32-byte job identifier

    Returns:
        (exists: bool, output_hash: bytes, nonce: bytes,
         iterations: int, device_type: bytes)
    """
    if len(job_id) != 32:
        return (False, b"", b"", 0, b"")

    result_desc = storage.get(_key(_R_PREFIX, job_id))
    if result_desc is None:
        return (False, b"", b"", 0, b"")

    # Decode result descriptor
    # Format: output_hash (32) + nonce_len (2) + nonce + iterations (8) + device_type_len (1) + device_type + backend_id_len (1) + backend_id
    if len(result_desc) < 32 + 2:
        return (False, b"", b"", 0, b"")

    output_hash = result_desc[:32]
    nonce_len = int.from_bytes(result_desc[32:34], "big")

    if len(result_desc) < 32 + 2 + nonce_len + 8 + 1:
        return (False, output_hash, b"", 0, b"")

    nonce = result_desc[34 : 34 + nonce_len]
    iterations = int.from_bytes(result_desc[34 + nonce_len : 34 + nonce_len + 8], "big")
    device_type_len = result_desc[34 + nonce_len + 8]

    if len(result_desc) < 34 + nonce_len + 8 + 1 + device_type_len:
        return (False, output_hash, nonce, iterations, b"")

    device_type = result_desc[
        34 + nonce_len + 9 : 34 + nonce_len + 9 + device_type_len
    ]

    return (True, output_hash, nonce, iterations, device_type)


def get_status(job_id: bytes) -> bytes:
    """
    Get job status.

    Args:
        job_id: 32-byte job identifier

    Returns:
        Status byte: b"0"=pending, b"1"=completed, b""=not found
    """
    if len(job_id) != 32:
        return b""

    status = storage.get(_key(_S_PREFIX, job_id))
    return status if status is not None else b""
