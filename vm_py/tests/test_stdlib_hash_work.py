"""Tests for VM-Py stdlib hash_work module."""

import hashlib

import pytest

from vm_py.stdlib import hash_work


def test_sha256():
    """Test SHA-256 hash function."""
    data = b"hello world"
    result = hash_work.sha256(data)

    # Verify it's 32 bytes
    assert isinstance(result, bytes)
    assert len(result) == 32

    # Verify against known hash
    expected = hashlib.sha256(data).digest()
    assert result == expected


def test_sha256_empty():
    """Test SHA-256 with empty input."""
    result = hash_work.sha256(b"")
    assert len(result) == 32
    expected = hashlib.sha256(b"").digest()
    assert result == expected


def test_sha256d():
    """Test double SHA-256 hash."""
    data = b"test data"
    result = hash_work.sha256d(data)

    # Verify it's 32 bytes
    assert isinstance(result, bytes)
    assert len(result) == 32

    # Verify against double hash
    expected = hashlib.sha256(hashlib.sha256(data).digest()).digest()
    assert result == expected


def test_blake2b_256():
    """Test BLAKE2b-256 hash."""
    data = b"blake2b test"
    result = hash_work.blake2b_256(data)

    # Verify it's 32 bytes
    assert isinstance(result, bytes)
    assert len(result) == 32

    # Verify against known hash
    expected = hashlib.blake2b(data, digest_size=32).digest()
    assert result == expected


def test_compute_commitment():
    """Test commitment computation."""
    data = b"commitment test data"
    commitment = hash_work.compute_commitment(data)

    assert isinstance(commitment, bytes)
    assert len(commitment) == 32

    # Should be SHA-256 of data
    expected = hashlib.sha256(data).digest()
    assert commitment == expected


def test_make_hash_job_sha256():
    """Test SHA-256 job descriptor creation."""
    commitment = b"\x01" * 32
    target_bits = 16
    max_iterations = 1000000

    job = hash_work.make_hash_job_sha256(commitment, target_bits, max_iterations)

    assert job["algorithm"] == "SHA256"
    assert job["input_commitment"] == commitment
    assert job["target_bits"] == target_bits
    assert job["max_iterations"] == max_iterations


def test_make_hash_job_sha256_invalid_commitment():
    """Test SHA-256 job with invalid commitment length."""
    with pytest.raises(ValueError, match="must be 32 bytes"):
        hash_work.make_hash_job_sha256(b"\x01" * 16, 16, 1000000)


def test_make_hash_job_sha256_invalid_target():
    """Test SHA-256 job with invalid target_bits."""
    commitment = b"\x01" * 32

    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_sha256(commitment, 0, 1000000)

    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_sha256(commitment, -1, 1000000)


def test_make_hash_job_sha256_invalid_iterations():
    """Test SHA-256 job with invalid max_iterations."""
    commitment = b"\x01" * 32

    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_sha256(commitment, 16, 0)

    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_sha256(commitment, 16, -1000)


def test_make_hash_job_sha256d():
    """Test double SHA-256 job descriptor creation."""
    commitment = b"\x02" * 32
    target_bits = 20
    max_iterations = 5000000

    job = hash_work.make_hash_job_sha256d(commitment, target_bits, max_iterations)

    assert job["algorithm"] == "SHA256D"
    assert job["input_commitment"] == commitment
    assert job["target_bits"] == target_bits
    assert job["max_iterations"] == max_iterations


def test_make_hash_job_scrypt():
    """Test Scrypt job descriptor creation."""
    commitment = b"\x03" * 32
    N = 16384  # 2^14
    r = 8
    p = 1
    target_bits = 18
    max_cost = 500000

    job = hash_work.make_hash_job_scrypt(
        commitment, N, r, p, target_bits, max_cost
    )

    assert job["algorithm"] == "SCRYPT"
    assert job["input_commitment"] == commitment
    assert job["target_bits"] == target_bits
    assert job["max_iterations"] == max_cost
    assert job["scrypt_n"] == N
    assert job["scrypt_r"] == r
    assert job["scrypt_p"] == p


def test_make_hash_job_scrypt_invalid_n():
    """Test Scrypt job with invalid N (not power of 2)."""
    commitment = b"\x03" * 32

    with pytest.raises(ValueError, match="must be power of 2"):
        hash_work.make_hash_job_scrypt(
            commitment, N=16385, r=8, p=1, target_bits=16, max_cost=100000
        )

    with pytest.raises(ValueError, match="must be power of 2"):
        hash_work.make_hash_job_scrypt(
            commitment, N=12345, r=8, p=1, target_bits=16, max_cost=100000
        )


def test_make_hash_job_scrypt_invalid_params():
    """Test Scrypt job with invalid parameters."""
    commitment = b"\x03" * 32

    # Invalid r
    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_scrypt(
            commitment, N=16384, r=0, p=1, target_bits=16, max_cost=100000
        )

    # Invalid p
    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_scrypt(
            commitment, N=16384, r=8, p=-1, target_bits=16, max_cost=100000
        )

    # Invalid max_cost
    with pytest.raises(ValueError, match="must be positive"):
        hash_work.make_hash_job_scrypt(
            commitment, N=16384, r=8, p=1, target_bits=16, max_cost=0
        )


def test_verify_hash_result_valid():
    """Test verification of valid hash result."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 8,
        "max_iterations": 1000000,
    }

    # Result with low difficulty (8 leading zero bits = first byte < 1)
    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x00" + b"\xff" * 31,  # Meets 8-bit target
        "nonce": b"\x12\x34\x56\x78",
        "iterations": 5000,
    }

    assert hash_work.verify_hash_result(job, result) is True


def test_verify_hash_result_algorithm_mismatch():
    """Test verification fails with algorithm mismatch."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 8,
        "max_iterations": 1000000,
    }

    result = {
        "algorithm": "SHA256D",  # Different algorithm
        "output_hash": b"\x00" * 32,
        "nonce": b"\x12\x34",
        "iterations": 5000,
    }

    assert hash_work.verify_hash_result(job, result) is False


def test_verify_hash_result_invalid_hash_length():
    """Test verification fails with wrong hash length."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 8,
        "max_iterations": 1000000,
    }

    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x00" * 16,  # Wrong length
        "nonce": b"\x12\x34",
        "iterations": 5000,
    }

    assert hash_work.verify_hash_result(job, result) is False


def test_verify_hash_result_exceeds_iterations():
    """Test verification fails when iterations exceed limit."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 8,
        "max_iterations": 1000,
    }

    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x00" * 32,
        "nonce": b"\x12\x34",
        "iterations": 10000,  # Exceeds max_iterations
    }

    assert hash_work.verify_hash_result(job, result) is False


def test_verify_hash_result_fails_difficulty():
    """Test verification fails when hash doesn't meet target."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 16,  # Requires 16 leading zero bits
        "max_iterations": 1000000,
    }

    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x01" + b"\x00" * 31,  # Only ~7 leading zeros
        "nonce": b"\x12\x34",
        "iterations": 5000,
    }

    assert hash_work.verify_hash_result(job, result) is False


def test_verify_hash_result_meets_difficulty():
    """Test verification succeeds when hash meets target."""
    job = {
        "algorithm": "SHA256",
        "input_commitment": b"\x01" * 32,
        "target_bits": 16,  # Requires 16 leading zero bits
        "max_iterations": 1000000,
    }

    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x00\x00" + b"\xff" * 30,  # 16 leading zeros
        "nonce": b"\x12\x34",
        "iterations": 5000,
    }

    assert hash_work.verify_hash_result(job, result) is True


def test_verify_hash_result_invalid_types():
    """Test verification handles invalid types gracefully."""
    # Non-dict job
    assert hash_work.verify_hash_result("not a dict", {}) is False

    # Non-dict result
    assert hash_work.verify_hash_result({}, "not a dict") is False

    # Invalid iterations type
    job = {"algorithm": "SHA256", "max_iterations": 1000}
    result = {
        "algorithm": "SHA256",
        "output_hash": b"\x00" * 32,
        "iterations": "invalid",
    }
    assert hash_work.verify_hash_result(job, result) is False
