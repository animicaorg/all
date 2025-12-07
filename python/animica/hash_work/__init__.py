"""
Hash-based useful work module for Animica.

Provides algorithm registry, job/result schemas, and utilities for integrating
hash-based computation as a first-class external service in the PoIES consensus.
"""

from .algorithms import (
    HashAlgorithm,
    get_algorithm_config,
    validate_algorithm_params,
)
from .schemas import (
    DeviceType,
    HashJob,
    HashJobDescriptor,
    HashResult,
    encode_hash_job,
    encode_hash_result,
    decode_hash_job,
    decode_hash_result,
)

__all__ = [
    "HashAlgorithm",
    "DeviceType",
    "HashJob",
    "HashJobDescriptor",
    "HashResult",
    "get_algorithm_config",
    "validate_algorithm_params",
    "encode_hash_job",
    "encode_hash_result",
    "decode_hash_job",
    "decode_hash_result",
]
