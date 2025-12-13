"""Pure-Python ML-KEM-768 (Kyber768) implementation.

This is a minimal, pure-Python implementation of ML-KEM-768 for Animica.
Based on the NIST ML-KEM standard (formerly Kyber).

Reference: FIPS 203 (Module-Lattice-Based Key-Encapsulation Mechanism Standard)
"""

from .kyber768 import Kyber768

__all__ = ["Kyber768"]
