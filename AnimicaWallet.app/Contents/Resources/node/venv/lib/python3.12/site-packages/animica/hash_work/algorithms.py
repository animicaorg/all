"""
Hash algorithm registry and configuration for Animica hash-based useful work.

Defines supported hash algorithms, their validation rules, and PoIES weighting
configuration consumed by consensus scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class HashAlgorithm(str, Enum):
    """Supported hash algorithms for useful work."""

    SHA256 = "SHA256"
    SHA256D = "SHA256D"  # Double SHA-256 (Bitcoin-style)
    SCRYPT = "SCRYPT"
    # Future extensibility hooks
    ARGON2 = "ARGON2"
    BLAKE2B = "BLAKE2B"


@dataclass(frozen=True)
class AlgorithmConfig:
    """Configuration and validation rules for a hash algorithm."""

    name: str
    # Target difficulty validation (log2 scale)
    min_target_bits: int
    max_target_bits: int
    # Iteration/work limits
    max_iterations: int
    # Scrypt-specific parameters (N, r, p)
    scrypt_min_n: Optional[int] = None
    scrypt_max_n: Optional[int] = None
    scrypt_min_r: Optional[int] = None
    scrypt_max_r: Optional[int] = None
    scrypt_min_p: Optional[int] = None
    scrypt_max_p: Optional[int] = None
    # PoIES scoring weight (base multiplier for psi calculation)
    psi_weight: float = 1.0


# Registry of algorithm configurations
_ALGORITHM_CONFIGS: Dict[HashAlgorithm, AlgorithmConfig] = {
    HashAlgorithm.SHA256: AlgorithmConfig(
        name="SHA256",
        min_target_bits=8,
        max_target_bits=256,
        max_iterations=2**32,  # 4 billion iterations max
        psi_weight=1.0,
    ),
    HashAlgorithm.SHA256D: AlgorithmConfig(
        name="SHA256D",
        min_target_bits=8,
        max_target_bits=256,
        max_iterations=2**32,
        psi_weight=1.2,  # Slightly higher weight for double hashing
    ),
    HashAlgorithm.SCRYPT: AlgorithmConfig(
        name="SCRYPT",
        min_target_bits=8,
        max_target_bits=256,
        max_iterations=2**31,
        scrypt_min_n=1024,  # 2^10
        scrypt_max_n=2**20,  # ~1M, memory-hard upper bound
        scrypt_min_r=1,
        scrypt_max_r=32,
        scrypt_min_p=1,
        scrypt_max_p=16,
        psi_weight=2.0,  # Higher weight for memory-hard work
    ),
    HashAlgorithm.ARGON2: AlgorithmConfig(
        name="ARGON2",
        min_target_bits=8,
        max_target_bits=256,
        max_iterations=2**31,
        psi_weight=2.5,  # Highest weight for most modern memory-hard algo
    ),
    HashAlgorithm.BLAKE2B: AlgorithmConfig(
        name="BLAKE2B",
        min_target_bits=8,
        max_target_bits=512,  # BLAKE2B supports up to 512 bits
        max_iterations=2**32,
        psi_weight=0.9,  # Slightly lower weight as it's faster
    ),
}


def get_algorithm_config(algo: HashAlgorithm) -> AlgorithmConfig:
    """Get configuration for a hash algorithm."""
    config = _ALGORITHM_CONFIGS.get(algo)
    if config is None:
        raise ValueError(f"Unknown hash algorithm: {algo}")
    return config


def validate_algorithm_params(
    algo: HashAlgorithm,
    target_bits: Optional[int] = None,
    max_iterations: Optional[int] = None,
    scrypt_n: Optional[int] = None,
    scrypt_r: Optional[int] = None,
    scrypt_p: Optional[int] = None,
) -> tuple[bool, Optional[str]]:
    """
    Validate parameters for a hash algorithm.

    Returns:
        (valid, error_message) tuple. error_message is None if valid.
    """
    config = get_algorithm_config(algo)

    # Validate target bits
    if target_bits is not None:
        if target_bits < config.min_target_bits:
            return False, f"target_bits {target_bits} below minimum {config.min_target_bits}"
        if target_bits > config.max_target_bits:
            return False, f"target_bits {target_bits} above maximum {config.max_target_bits}"

    # Validate max iterations
    if max_iterations is not None:
        if max_iterations <= 0:
            return False, "max_iterations must be positive"
        if max_iterations > config.max_iterations:
            return (
                False,
                f"max_iterations {max_iterations} exceeds limit {config.max_iterations}",
            )

    # Validate scrypt-specific parameters
    if algo == HashAlgorithm.SCRYPT:
        if scrypt_n is not None:
            if config.scrypt_min_n and scrypt_n < config.scrypt_min_n:
                return False, f"scrypt N {scrypt_n} below minimum {config.scrypt_min_n}"
            if config.scrypt_max_n and scrypt_n > config.scrypt_max_n:
                return False, f"scrypt N {scrypt_n} above maximum {config.scrypt_max_n}"
            # N must be power of 2
            if scrypt_n & (scrypt_n - 1) != 0:
                return False, f"scrypt N {scrypt_n} must be power of 2"

        if scrypt_r is not None:
            if config.scrypt_min_r and scrypt_r < config.scrypt_min_r:
                return False, f"scrypt r {scrypt_r} below minimum {config.scrypt_min_r}"
            if config.scrypt_max_r and scrypt_r > config.scrypt_max_r:
                return False, f"scrypt r {scrypt_r} above maximum {config.scrypt_max_r}"

        if scrypt_p is not None:
            if config.scrypt_min_p and scrypt_p < config.scrypt_min_p:
                return False, f"scrypt p {scrypt_p} below minimum {config.scrypt_min_p}"
            if config.scrypt_max_p and scrypt_p > config.scrypt_max_p:
                return False, f"scrypt p {scrypt_p} above maximum {config.scrypt_max_p}"

    return True, None


def calculate_work_factor(
    algo: HashAlgorithm,
    target_bits: int,
    iterations: int,
    scrypt_n: Optional[int] = None,
    scrypt_r: Optional[int] = None,
    scrypt_p: Optional[int] = None,
) -> float:
    """
    Calculate a normalized work factor for PoIES scoring.

    This converts algorithm-specific parameters into a single scalar that
    can be weighted and capped by the PoIES scorer.

    Returns:
        Work factor (float >= 0) representing computational effort.
    """
    config = get_algorithm_config(algo)

    # Base work from target difficulty
    # Higher target bits = exponentially harder (2^bits operations expected)
    difficulty_factor = 2.0 ** max(0, target_bits - 8)  # Normalize to 8-bit baseline

    # Iteration multiplier (log scale to avoid overflow)
    import math

    iter_factor = math.log1p(iterations)

    # Algorithm-specific adjustments
    if algo == HashAlgorithm.SCRYPT and scrypt_n and scrypt_r and scrypt_p:
        # Memory-hard work factor based on N*r*p
        memory_cost = scrypt_n * scrypt_r * scrypt_p
        memory_factor = math.log1p(memory_cost) / 10.0  # Scale down
        work = difficulty_factor * iter_factor * memory_factor
    else:
        # Standard hash work
        work = difficulty_factor * iter_factor

    # Apply algorithm weight
    return work * config.psi_weight


__all__ = [
    "HashAlgorithm",
    "AlgorithmConfig",
    "get_algorithm_config",
    "validate_algorithm_params",
    "calculate_work_factor",
]
