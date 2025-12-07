"""Tests for hash algorithm registry and validation."""

import pytest

from python.animica.hash_work.algorithms import (
    HashAlgorithm,
    calculate_work_factor,
    get_algorithm_config,
    validate_algorithm_params,
)


def test_algorithm_enum():
    """Test HashAlgorithm enum values."""
    assert HashAlgorithm.SHA256.value == "SHA256"
    assert HashAlgorithm.SHA256D.value == "SHA256D"
    assert HashAlgorithm.SCRYPT.value == "SCRYPT"
    assert HashAlgorithm.ARGON2.value == "ARGON2"
    assert HashAlgorithm.BLAKE2B.value == "BLAKE2B"


def test_get_algorithm_config():
    """Test retrieving algorithm configurations."""
    # Test valid algorithms
    sha256_config = get_algorithm_config(HashAlgorithm.SHA256)
    assert sha256_config.name == "SHA256"
    assert sha256_config.min_target_bits == 8
    assert sha256_config.max_target_bits == 256
    assert sha256_config.psi_weight == 1.0

    scrypt_config = get_algorithm_config(HashAlgorithm.SCRYPT)
    assert scrypt_config.name == "SCRYPT"
    assert scrypt_config.scrypt_min_n == 1024
    assert scrypt_config.scrypt_max_n == 2**20
    assert scrypt_config.psi_weight == 2.0

    # Test all algorithms have configs
    for algo in HashAlgorithm:
        config = get_algorithm_config(algo)
        assert config.name is not None


def test_validate_sha256_params():
    """Test parameter validation for SHA256."""
    # Valid params
    valid, err = validate_algorithm_params(
        HashAlgorithm.SHA256, target_bits=16, max_iterations=1000000
    )
    assert valid
    assert err is None

    # Target too low
    valid, err = validate_algorithm_params(HashAlgorithm.SHA256, target_bits=4)
    assert not valid
    assert "below minimum" in err

    # Target too high
    valid, err = validate_algorithm_params(HashAlgorithm.SHA256, target_bits=300)
    assert not valid
    assert "above maximum" in err

    # Iterations negative
    valid, err = validate_algorithm_params(HashAlgorithm.SHA256, max_iterations=-1)
    assert not valid
    assert "must be positive" in err

    # Iterations too high
    valid, err = validate_algorithm_params(
        HashAlgorithm.SHA256, max_iterations=2**33
    )
    assert not valid
    assert "exceeds limit" in err


def test_validate_scrypt_params():
    """Test parameter validation for SCRYPT."""
    # Valid params
    valid, err = validate_algorithm_params(
        HashAlgorithm.SCRYPT,
        target_bits=16,
        max_iterations=100000,
        scrypt_n=16384,  # 2^14
        scrypt_r=8,
        scrypt_p=1,
    )
    assert valid
    assert err is None

    # N not power of 2
    valid, err = validate_algorithm_params(
        HashAlgorithm.SCRYPT, scrypt_n=16385  # Not power of 2
    )
    assert not valid
    assert "power of 2" in err

    # N too small
    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_n=512)
    assert not valid
    assert "below minimum" in err

    # N too large
    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_n=2**21)
    assert not valid
    assert "above maximum" in err

    # r out of range
    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_r=0)
    assert not valid
    assert "below minimum" in err

    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_r=64)
    assert not valid
    assert "above maximum" in err

    # p out of range
    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_p=0)
    assert not valid
    assert "below minimum" in err

    valid, err = validate_algorithm_params(HashAlgorithm.SCRYPT, scrypt_p=32)
    assert not valid
    assert "above maximum" in err


def test_calculate_work_factor_sha256():
    """Test work factor calculation for SHA256."""
    # Basic work factor
    work1 = calculate_work_factor(HashAlgorithm.SHA256, target_bits=16, iterations=1000)
    assert work1 > 0

    # Higher difficulty should increase work
    work2 = calculate_work_factor(HashAlgorithm.SHA256, target_bits=20, iterations=1000)
    assert work2 > work1

    # More iterations should increase work
    work3 = calculate_work_factor(HashAlgorithm.SHA256, target_bits=16, iterations=10000)
    assert work3 > work1

    # SHA256D should have higher work due to weight
    work_sha256d = calculate_work_factor(
        HashAlgorithm.SHA256D, target_bits=16, iterations=1000
    )
    assert work_sha256d > work1


def test_calculate_work_factor_scrypt():
    """Test work factor calculation for SCRYPT."""
    work = calculate_work_factor(
        HashAlgorithm.SCRYPT,
        target_bits=16,
        iterations=1000,
        scrypt_n=16384,
        scrypt_r=8,
        scrypt_p=1,
    )
    assert work > 0

    # Higher N should increase work
    work_high_n = calculate_work_factor(
        HashAlgorithm.SCRYPT,
        target_bits=16,
        iterations=1000,
        scrypt_n=32768,
        scrypt_r=8,
        scrypt_p=1,
    )
    assert work_high_n > work

    # SCRYPT should have higher work than SHA256 due to weight and memory cost
    sha256_work = calculate_work_factor(
        HashAlgorithm.SHA256, target_bits=16, iterations=1000
    )
    assert work > sha256_work


def test_algorithm_weights():
    """Test that different algorithms have expected relative weights."""
    base_params = {"target_bits": 16, "iterations": 10000}

    sha256_work = calculate_work_factor(HashAlgorithm.SHA256, **base_params)
    sha256d_work = calculate_work_factor(HashAlgorithm.SHA256D, **base_params)
    blake2b_work = calculate_work_factor(HashAlgorithm.BLAKE2B, **base_params)

    # SHA256D should be > SHA256
    assert sha256d_work > sha256_work

    # BLAKE2B should be < SHA256 (faster algo, lower weight)
    assert blake2b_work < sha256_work
