"""Pure-Python deterministic random bit generator (DRBG) for testing.

This module provides AES-256 CTR DRBG implementation in pure Python
for generating deterministic test vectors (KAT verification).

For production, os.urandom() is used for cryptographic randomness.
"""

from .aes256_ctr_drbg import AES256_CTR_DRBG

__all__ = ["AES256_CTR_DRBG"]
