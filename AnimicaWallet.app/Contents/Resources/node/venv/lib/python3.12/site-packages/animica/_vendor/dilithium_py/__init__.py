"""Pure-Python ML-DSA-65 (Dilithium3) implementation.

This is a minimal, pure-Python implementation of ML-DSA-65 for Animica.
Based on the NIST ML-DSA standard (formerly Dilithium).

Reference: FIPS 204 (Module-Lattice-Based Digital Signature Standard)
"""

from .dilithium3 import Dilithium3

__all__ = ["Dilithium3"]
